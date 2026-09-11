"""Student preferences, feedback and system prompts (the B side of contracts/student.md).

Spec: contracts/student.md (§1 profile fields and allowed values; §2 HydraDB item shapes — explicit preferences
infer:false under fixed ids s<n>_pref_style|length|modality|pace with text "field=value", feedback infer:true
under s<n>_fb_<seq> with custom_instructions; the hotdata `students` row; §3 feedback event; §4 preference read
with the system prompt built from explicit + inferred; §6 never put another student's memory into a prompt);
kit/02-product/student-layer.md (collection per student; feedback -> HydraDB infer:true AND Cognee add_feedback +
improve); kit/BUILDER-RULES.md §8 (store only what the student gives; each student isolated).

Stores (STUDENT_STORE):
  local    LocalJSONStudentStore — one JSON file per student under <LOCAL_DIR>/students/, holding exactly the
           HydraDB memory items (upsert by id). HydraDB's infer:true extraction is replaced by infer_preferences(),
           a small deterministic phrase table — the offline stand-in, not an LLM.
  hydradb  HydraDBStudentStore — database HYDRADB_DATABASE, collection s<n>; context.ingest / inspect / list and
           client.query (one collection per query). LIVE [TEST] throughout.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from oracle import config
from oracle._cli import ScriptParser, read_json_file

PREF_FIELDS = ("style_order", "length", "modality", "pace")
PREF_CHOICES: dict[str, tuple[str, ...]] = {
    "style_order": ("examples_first", "proofs_first"), "length": ("short", "detailed"),
    "modality": ("visual", "verbal"), "pace": ("slow", "normal", "fast")}
PREF_ID_SUFFIX = {"style_order": "style", "length": "length", "modality": "modality", "pace": "pace"}
PREF_TITLES = {"style_order": "Teaching order", "length": "Explanation length", "modality": "Modality", "pace": "Pace"}
PROFILE_FIELDS = ("student_id", "display_name", "course", "exam_id", "exam_date", "weekly_hours", *PREF_FIELDS)
STUDENT_ROW_FIELDS = ("student_id", "course", "exam_id", "exam_date", "weekly_hours")

FEEDBACK_TITLE = "Feedback on plan answer"
FEEDBACK_INSTRUCTIONS = "Extract a durable teaching preference about explanation length, order, modality or pace."
INFERRED_QUERY = "durable teaching preferences about explanation length, order, modality or pace"
MAX_FEEDBACK_CHARS = 2000
MAX_INFERRED = 5

# System-prompt sentences for the explicit preferences. The student.md §4 example is Maya's four settings.
PROMPT_SENTENCES: dict[str, dict[str, str]] = {
    "style_order": {"examples_first": "Teach with a worked example before any theory.",
                    "proofs_first": "Start from the definitions and the proof, then give one worked example."},
    "length": {"short": "Keep each explanation under 120 words.",
               "detailed": "Give detailed explanations of up to 400 words that show every step."},
    "modality": {"visual": "Prefer diagrams described in words.",
                 "verbal": "Explain in words rather than with diagrams."},
    "pace": {"slow": "Slow pace: one idea at a time, with a check question after each.",
             "normal": "Normal pace.",
             "fast": "Fast pace: skip steps the student already knows."},
}

# Offline stand-in for HydraDB's infer:true extraction: (dimension, durable preference, phrases). Within a
# dimension the more specific phrases come first; one preference per dimension per feedback text; the latest
# feedback wins per dimension.
INFERENCE_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = tuple(
    (dim, statement, re.compile(pattern, re.IGNORECASE)) for dim, statement, pattern in (
        ("length", "Prefers concise explanations", r"\b(too (wordy|long|verbose)|wordy|verbose|shorter|too much text|tl;?dr)\b"),
        ("length", "Prefers more detailed explanations", r"\b(too (short|brief|terse)|more details?|more steps|longer)\b"),
        ("style_order", "Prefers a worked example before the theory", r"\b(examples? (helped|helps|first)|more examples|worked examples?)\b"),
        ("style_order", "Prefers the theory before examples", r"\b(theory first|proofs? first|derivation first|show the proof)\b"),
        ("modality", "Prefers verbal explanations", r"\b(no diagrams?|words only|just explain)\b"),
        ("modality", "Prefers visual explanations", r"\b(diagrams?|pictures?|figures?|sketch(es)?|visual)\b"),
        ("pace", "Prefers a faster pace", r"\b(too slow|faster|speed up)\b"),
        ("pace", "Prefers a slower pace", r"\b(too fast|slow down|slower|lost me)\b"),
    ))

_STUDENT_ID_RE = re.compile(r"s[0-9]{1,9}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SOURCE_SUFFIX_RE = re.compile(r"\s*\(from feedback .*\)\s*$", re.DOTALL)


# ------------------------------------------------------------------ validation and shapes
def validate_student_id(value: Any) -> str:
    if not isinstance(value, str) or not _STUDENT_ID_RE.fullmatch(value):
        raise ValueError(f"student_id must look like s<n>, got {value!r}")
    return value


def student_dataset_name(student_id: str) -> str:
    """Cognee private dataset of a student: 'student-<n>' (student.md §2)."""
    return f"student-{validate_student_id(student_id)[1:]}"


def pref_id(student_id: str, field: str) -> str:
    return f"{validate_student_id(student_id)}_pref_{PREF_ID_SUFFIX[field]}"


def _clean_line(text: str, limit: int) -> str:
    return " ".join(_CONTROL_RE.sub(" ", text).split())[:limit]


def validate_prefs(prefs: Mapping[str, Any]) -> dict[str, str]:
    """The four explicit preferences, each one of its allowed values (student.md §1)."""
    if not isinstance(prefs, Mapping):
        raise ValueError("preferences must be an object")
    out = {}
    for field in PREF_FIELDS:
        value = prefs.get(field)
        if value not in PREF_CHOICES[field]:
            raise ValueError(f"{field} must be one of {PREF_CHOICES[field]}, got {value!r}")
        out[field] = value
    return out


def validate_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """A StudentProfile (student.md §1): every field present and within its allowed values."""
    if not isinstance(profile, Mapping):
        raise ValueError("profile must be an object")
    missing = [f for f in PROFILE_FIELDS if f not in profile]
    if missing:
        raise ValueError(f"profile is missing {missing}")
    name = profile["display_name"]
    if not isinstance(name, str) or not _clean_line(name, 100) or len(name) > 100:
        raise ValueError("display_name must be a non-empty string of at most 100 characters")
    course = config.validate_id(profile["course"], what="course")
    exam_id = config.validate_id(profile["exam_id"], what="exam_id")
    if not exam_id.startswith(f"{course}-"):
        raise ValueError(f"exam_id {exam_id!r} is not an exam of course {course!r}")
    exam_date = profile["exam_date"]
    try:
        if not isinstance(exam_date, str) or len(exam_date) != 10:
            raise ValueError
        date.fromisoformat(exam_date)
    except ValueError:
        raise ValueError(f"exam_date must be an ISO date YYYY-MM-DD, got {exam_date!r}") from None
    hours = profile["weekly_hours"]
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or not 0.5 <= hours <= 60:
        raise ValueError(f"weekly_hours must be a number between 0.5 and 60, got {hours!r}")
    return {"student_id": validate_student_id(profile["student_id"]), "display_name": _clean_line(name, 100),
            "course": course, "exam_id": exam_id, "exam_date": exam_date, "weekly_hours": float(hours),
            **validate_prefs(profile)}


def validate_feedback(event: Mapping[str, Any]) -> dict[str, Any]:
    """A feedback event (student.md §3): text and/or an integer score 1..5."""
    if not isinstance(event, Mapping):
        raise ValueError("feedback event must be an object")
    text, score, qa_id = event.get("text"), event.get("score"), event.get("qa_id")
    if text is not None:
        if not isinstance(text, str) or len(text) > MAX_FEEDBACK_CHARS:
            raise ValueError(f"text must be a string of at most {MAX_FEEDBACK_CHARS} characters")
        text = text.strip() or None
    if score is not None and (isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5):
        raise ValueError(f"score must be an integer 1..5 or null, got {score!r}")
    if text is None and score is None:
        raise ValueError("feedback needs text, a score, or both")
    if qa_id is not None and (not isinstance(qa_id, str) or not qa_id or len(qa_id) > 200 or _CONTROL_RE.search(qa_id)):
        raise ValueError(f"bad qa_id {qa_id!r}")
    created_at = event.get("created_at")
    if created_at is not None and (not isinstance(created_at, str) or len(created_at) > 64):
        raise ValueError("created_at must be an ISO timestamp string")
    return {"student_id": validate_student_id(event.get("student_id")),
            "session_id": config.validate_id(event.get("session_id"), what="session_id"),
            "qa_id": qa_id, "text": text, "score": score, "created_at": created_at}


def pref_memories(student_id: str, prefs: Mapping[str, Any]) -> list[dict[str, Any]]:
    """HydraDB items for the explicit preferences — verbatim (infer:false), fixed ids so updates replace."""
    clean = validate_prefs(prefs)
    return [{"id": pref_id(student_id, f), "title": PREF_TITLES[f], "text": f"{f}={clean[f]}", "infer": False,
             "additional_metadata": {"kind": "explicit_pref", "field": f}} for f in PREF_FIELDS]


def feedback_memory(event: Mapping[str, Any], seq: int) -> dict[str, Any]:
    """HydraDB item for one feedback event (infer:true; HydraDB extracts the durable preference)."""
    ev = validate_feedback(event)
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise ValueError(f"seq must be a positive integer, got {seq!r}")
    text = ev["text"] if ev["text"] is not None else f"(rating only) {ev['score']}/5"
    return {"id": f"{ev['student_id']}_fb_{seq:04d}", "title": FEEDBACK_TITLE, "text": text, "infer": True,
            "custom_instructions": FEEDBACK_INSTRUCTIONS,
            "additional_metadata": {"kind": "feedback", "session_id": ev["session_id"], "score": ev["score"]}}


def parse_pref_text(text: Any) -> tuple[str, str] | None:
    """('style_order', 'examples_first') from 'style_order=examples_first'; None if it is not an explicit pref."""
    if not isinstance(text, str) or "=" not in text:
        return None
    field, _, value = text.strip().partition("=")
    return (field, value) if field in PREF_CHOICES and value in PREF_CHOICES[field] else None


def infer_preferences(feedback_texts: Sequence[str]) -> list[str]:
    """Durable preferences from feedback texts, oldest first (offline stand-in for HydraDB infer:true).
    One entry per dimension — the latest feedback wins — as "<preference> (from feedback '<text>')"."""
    latest: dict[str, str] = {}
    for text in feedback_texts:
        if not isinstance(text, str):
            continue
        done: set[str] = set()
        for dim, statement, pattern in INFERENCE_RULES:
            if dim not in done and pattern.search(text):
                done.add(dim)
                latest.pop(dim, None)  # re-insert: most recent last
                latest[dim] = f"{statement} (from feedback '{_clean_line(text, 80)}')"
    return list(latest.values())


def _prompt_statement(inferred: str) -> str:
    """An inferred preference as prompt text: one line, at most 200 characters, without the quoted feedback."""
    line = _clean_line(_SOURCE_SUFFIX_RE.sub("", inferred), 200).rstrip(".")
    return line[:1].lower() + line[1:]


def build_system_prompt(explicit: Mapping[str, str], inferred: Sequence[str]) -> str:
    """The system prompt C's pipelines pass to the LLM (student.md §4): one sentence per explicit preference,
    then the inferred ones, which win because they come from more recent feedback. Only this student's data."""
    parts = [PROMPT_SENTENCES[f][explicit[f]] for f in PREF_FIELDS if explicit.get(f) in PROMPT_SENTENCES[f]]
    learned = [s for s in (_prompt_statement(i) for i in inferred) if s]
    if learned:
        parts.append("Learned from recent feedback (takes priority over the settings above): "
                     + "; ".join(learned) + ".")
    return " ".join(parts)


def preference_read(student_id: str, explicit: Mapping[str, str], inferred: Sequence[str]) -> dict[str, Any]:
    """The B -> C preference read (student.md §4)."""
    return {"student_id": validate_student_id(student_id), "explicit": dict(explicit), "inferred": list(inferred),
            "system_prompt": build_system_prompt(explicit, inferred)}


def students_row(profile: Mapping[str, Any]) -> dict[str, Any]:
    """The hotdata `students` row (planner arithmetic)."""
    p = validate_profile(profile)
    return {f: p[f] for f in STUDENT_ROW_FIELDS}


# ------------------------------------------------------------------ stores
@runtime_checkable
class StudentStore(Protocol):
    def write_profile(self, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Store the profile's explicit preferences (and, locally, the profile). Returns the memory items."""
        ...

    def write_prefs(self, student_id: str, prefs: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Replace the four explicit preference memories. Returns the items written."""
        ...

    def write_feedback(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Store one feedback memory (next s<n>_fb_<seq>). Returns the item written."""
        ...

    def read_prefs(self, student_id: str) -> dict[str, Any]:
        """The student.md §4 object for this student only."""
        ...


class LocalJSONStudentStore:
    """Offline store: <root>/<student_id>.json = {"profile", "memories": {id: item}, "events": {key: id}}.
    One file per student mirrors HydraDB's collection-per-student isolation; writes hold an exclusive lock and
    replace the file atomically. Replaying the same feedback event returns the item already stored."""

    def __init__(self, root: str | os.PathLike | None = None) -> None:
        self.root = Path(root) if root is not None else config.get_settings().local_dir / "students"

    def _path(self, student_id: str) -> Path:
        return self.root / f"{validate_student_id(student_id)}.json"

    def load(self, student_id: str) -> dict[str, Any]:
        path = self._path(student_id)
        if not path.is_file():
            return {"student_id": student_id, "profile": None, "memories": {}, "events": {}}
        return json.loads(path.read_text(encoding="utf-8"))

    def _update(self, student_id: str, change) -> Any:
        path = self._path(student_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_suffix(".lock"), "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = self.load(student_id)
                result = change(data)
                fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, sort_keys=True, ensure_ascii=False, indent=1)
                os.replace(tmp, path)
                return result
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def write_profile(self, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        p = validate_profile(profile)
        items = pref_memories(p["student_id"], p)

        def change(data: dict[str, Any]) -> None:
            data["profile"] = p
            data["memories"].update({i["id"]: i for i in items})

        self._update(p["student_id"], change)
        return items

    def write_prefs(self, student_id: str, prefs: Mapping[str, Any]) -> list[dict[str, Any]]:
        items = pref_memories(student_id, prefs)

        def change(data: dict[str, Any]) -> None:
            data["memories"].update({i["id"]: i for i in items})
            if data.get("profile"):
                data["profile"].update(validate_prefs(prefs))

        self._update(student_id, change)
        return items

    def write_feedback(self, event: Mapping[str, Any]) -> dict[str, Any]:
        ev = validate_feedback(event)
        key = config.sha256_hex(config.canonical_json(ev))

        def change(data: dict[str, Any]) -> dict[str, Any]:
            if key in data["events"]:
                return data["memories"][data["events"][key]]
            prefix = f"{ev['student_id']}_fb_"
            seqs = [int(i[len(prefix):]) for i in data["memories"] if i.startswith(prefix) and i[len(prefix):].isdigit()]
            item = feedback_memory(ev, max(seqs, default=0) + 1)
            data["memories"][item["id"]] = item
            data["events"][key] = item["id"]
            return item

        return self._update(ev["student_id"], change)

    def read_prefs(self, student_id: str) -> dict[str, Any]:
        memories = self.load(student_id)["memories"]
        explicit: dict[str, str] = {}
        for item in memories.values():
            parsed = parse_pref_text(item.get("text")) if item.get("additional_metadata", {}).get("kind") == "explicit_pref" else None
            if parsed:
                explicit[parsed[0]] = parsed[1]
        feedback = sorted((i for i in memories.values() if i.get("additional_metadata", {}).get("kind") == "feedback"),
                          key=lambda i: int(i["id"].rsplit("_", 1)[1]))
        texts = [i["text"] for i in feedback if not i["text"].startswith("(rating only)")]
        ordered = {f: explicit[f] for f in PREF_FIELDS if f in explicit}
        return preference_read(student_id, ordered, infer_preferences(texts))


class HydraDBStudentStore:
    """Hosted HydraDB: database HYDRADB_DATABASE, collection = the student id (s<n>). LIVE [TEST] throughout.
    ingest answers 202 Accepted, so a read straight after a write may still see the previous state."""

    def __init__(self, client: Any | None = None, database: str | None = None) -> None:
        self._client = client
        self.database = config.validate_id(database or config.get_settings().hydradb_database, what="HydraDB database")
        self._last_seq: dict[str, int] = {}

    @property
    def client(self) -> Any:
        if self._client is None:
            token = config.env("HYDRADB_API_KEY")
            if not token:
                raise config.ConfigError("HYDRADB_API_KEY is not set (STUDENT_STORE=hydradb)")
            from hydra_db import HydraDB
            self._client = HydraDB(token=token, base_url=config.env("HYDRADB_BASE_URL"))  # LIVE [TEST]
        return self._client

    def _ingest(self, student_id: str, items: list[dict[str, Any]]) -> None:
        self.client.context.ingest(  # LIVE [TEST]
            database=self.database, collection=validate_student_id(student_id), type="memory",
            memories=json.dumps(items, ensure_ascii=False, allow_nan=False), upsert="true")

    def write_profile(self, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        p = validate_profile(profile)
        return self.write_prefs(p["student_id"], p)

    def write_prefs(self, student_id: str, prefs: Mapping[str, Any]) -> list[dict[str, Any]]:
        items = pref_memories(student_id, prefs)
        self._ingest(student_id, items)
        return items

    def _next_seq(self, student_id: str) -> int:
        """1 + the highest s<n>_fb_<seq> listed in the collection (and any seq this process already used)."""
        from hydra_db.errors import NotFoundError

        pattern = re.compile(rf"{re.escape(student_id)}_fb_([0-9]+)")
        best = self._last_seq.get(student_id, 0)
        for page in range(1, 101):
            try:
                response = self.client.context.list(  # LIVE [TEST]
                    database=self.database, collection=student_id, type="memory", page=page, page_size=100)
            except NotFoundError:
                break
            data = getattr(response, "data", None)
            rows = getattr(data, "user_memories", None) or []
            for row in rows:
                match = pattern.fullmatch(getattr(row, "memory_id", "") or "")
                if match:
                    best = max(best, int(match.group(1)))
            total = getattr(data, "total", None)
            if len(rows) < 100 or (total is not None and page * 100 >= total):
                break
        self._last_seq[student_id] = best + 1
        return best + 1

    def write_feedback(self, event: Mapping[str, Any]) -> dict[str, Any]:
        ev = validate_feedback(event)
        item = feedback_memory(ev, self._next_seq(ev["student_id"]))
        self._ingest(ev["student_id"], [item])
        return item

    def read_prefs(self, student_id: str) -> dict[str, Any]:
        from hydra_db.errors import NotFoundError

        student_id = validate_student_id(student_id)
        explicit: dict[str, str] = {}
        for field in PREF_FIELDS:
            try:
                response = self.client.context.inspect(  # LIVE [TEST] does inspect serve memories?
                    id=pref_id(student_id, field), database=self.database, collection=student_id)
            except NotFoundError:
                continue
            parsed = parse_pref_text(getattr(getattr(response, "data", None), "content", None))
            if parsed and parsed[0] == field:
                explicit[field] = parsed[1]
        response = self.client.query(  # LIVE [TEST] is chunk_content the inferred preference or the raw text?
            database=self.database, collection=student_id, query=INFERRED_QUERY, query_by="hybrid",
            recency_bias=0.8, type="memory", max_results=MAX_INFERRED)
        inferred: list[str] = []
        for chunk in getattr(getattr(response, "data", None), "chunks", None) or []:
            content = getattr(chunk, "chunk_content", None)
            meta = getattr(chunk, "additional_metadata", None) or {}
            if not isinstance(content, str) or parse_pref_text(content) or meta.get("kind") == "explicit_pref":
                continue  # explicit preferences share the collection
            line = _clean_line(content, 200)
            if line and line not in inferred:
                inferred.append(line)
        return preference_read(student_id, explicit, inferred)


def get_student_store(settings: config.Settings | None = None) -> StudentStore:
    """The store selected by STUDENT_STORE (local by default)."""
    s = settings or config.get_settings()
    if s.student_store == "hydradb":
        return HydraDBStudentStore(database=s.hydradb_database)
    return LocalJSONStudentStore(s.local_dir / "students")


# ------------------------------------------------------------------ operations
def write_student_row(profile: Mapping[str, Any], backend: Any | None = None) -> int:
    """Upsert the hotdata `students` row (key student_id)."""
    from oracle.backend import get_backend

    be = backend if backend is not None else get_backend()
    return be.load_table(be.ensure_ledger(), "students", [students_row(profile)], mode="upsert")


def save_student(profile: Mapping[str, Any], *, store: StudentStore | None = None,
                 backend: Any | None = None) -> dict[str, Any]:
    """Validate a StudentProfile, store its explicit preferences, and upsert its `students` ledger row."""
    p = validate_profile(profile)
    items = (store or get_student_store()).write_profile(p)
    rows = write_student_row(p, backend)
    return {"student_id": p["student_id"], "memories": [i["id"] for i in items], "ledger_rows": rows}


def handle_feedback(event: Mapping[str, Any], *, store: StudentStore | None = None) -> dict[str, Any]:
    """student.md §3: HydraDB infer:true memory in s<n>, then Cognee add_feedback + improve on the student's
    dataset (skipped without LLM_API_KEY; a Cognee failure is reported, not raised)."""
    from oracle.cognee_feedback import apply_feedback

    ev = validate_feedback(event)
    item = (store or get_student_store()).write_feedback(ev)
    try:
        cognee = apply_feedback(ev["session_id"], dataset=student_dataset_name(ev["student_id"]), score=ev["score"],
                                text=ev["text"], qa_ids=[ev["qa_id"]] if ev["qa_id"] else None)
    except Exception as exc:  # the stored memory is the durable part; Cognee is best effort
        cognee = {"warning": f"cognee feedback failed: {exc}"}
    return {"memory": item, "cognee": cognee}


def main(argv: list[str] | None = None) -> int:
    parser = ScriptParser(prog="python -m oracle.students", description="Student preferences, feedback and prompts.")
    sub = parser.add_subparsers(dest="command", required=True)
    save = sub.add_parser("save", help="store a StudentProfile (student.md §1) and its students ledger row")
    save.add_argument("--profile", required=True, help="JSON file with the profile")
    feedback = sub.add_parser("feedback", help="store a feedback event (student.md §3)")
    feedback.add_argument("--event", required=True, help="JSON file with the event")
    read = sub.add_parser("read", help="print the preference read (student.md §4)")
    read.add_argument("--student-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "save":
            result = save_student(read_json_file(args.profile))
        elif args.command == "feedback":
            result = handle_feedback(read_json_file(args.event))
        else:
            result = get_student_store().read_prefs(args.student_id)
    except Exception as exc:  # validation, file or service error: still one JSON object, exit 1
        config.emit({"ok": False, "error": str(exc) if isinstance(exc, (ValueError, OSError))
                     else f"{type(exc).__name__}: {exc}"})
        return config.EXIT_HARD
    config.emit({"ok": True, **result})
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
