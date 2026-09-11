"""Rank every topic, build the prediction, seal it with SHA-256, and record it in the ledger.

Spec: kit/02-product/prediction-model.md ("From signals to probability", "Study list size K", "Scoring" baselines,
"Prediction output"), contracts/prediction.schema.json (the exact object), contracts/README.md (time-ordering rule,
feature_set), contracts/ledger-schema.sql (`predictions`), kit/03-architecture/rote-plays.md (CLI, exit codes).

CLI:  python -m oracle.rank_and_seal --run-id r7-FX.101-final-2022F --course FX.101 \
          --target-exam FX.101-final-2022F --signals signals.json [--cold-start] [--made-at 2026-09-11T13:12:04-07:00]
Prints ONE JSON object {ok, run_id, hash, made_at, k, top_k, ...}. Exit 0 ok | 1 hard fault (bad input, missing
ledger rows, refusing to re-seal) | 4 leakage (x2..x6 != 0 on an exam_history run, or lessons from this run or later).

Signals input (--signals <path>, or '-' for stdin), produced by run_signals:
    {"signals": [{"topic_id": "T01", "x1": 0.5, ..., "x7": 0.0}, ...],      # or "topics"/"rows", or a bare list
     "course": ..., "target_exam": ..., "feature_set": ...}                   # optional; checked when present
A row may nest its values ({"topic_id": "T01", "signals": {"x1": ...}}), and "signals" may be a mapping
{topic_id: {"x1": ...}}. Every topic of the course's fixed list must be present, and no other; a missing or null
x_k counts as 0. On exam_history runs x2..x6 MUST be 0 (contracts/README.md), otherwise exit 4.

Design decisions:
  * p = sigma(b0 + sum bk*xk) (lessons_store.predict_p) with lessons_store.read_latest() weights, or
    COLD_START_WEIGHTS with --cold-start. Signals and p are rounded to 6 decimals BEFORE ranking, so the sealed object
    is self-consistent (p recomputes from its own weights and signals) and rank follows the stored p; ties go to the
    smaller topic_id. Lessons must come from an EARLIER run: lessons_run_seq >= this run's seq means the weights were
    fitted on this target's own label (a replayed older run) -> exit 4.
  * K = median number of distinct topics over VISIBLE exams of the same type in this course; if there are none, the
    same median over all courses; then over all visible exams of any type; then 1. The median rounds half up and K
    is clamped to [1, number of topics]. The tier used is reported as k_source.
  * Baselines: even = top-K by x4 (ties topic_id) when feature_set = full, [] when exam_history (score.py then uses
    the closed form K/N). last_exam = topics of the most recent visible same-type exam of this course ranked by
    their points there (ties topic_id), cut to K, padded to K by x4 then topic_id.
  * K and last_exam read the LEDGER through ordering.visible_sql(), so they see exactly the rows the run database
    holds; the target's own rows are excluded twice (visibility, and exam_id <> target).
  * Seal: <LOCAL_DIR>/predictions/<run_id>.prediction.json is the PRETTY file (indent 2, sorted keys). <run_id>.sha256
    holds 64 hex + newline: SHA-256 of config.canonical_json(obj), NOT of the pretty file's bytes (so `shasum` on the
    file will not match). verify_hash() re-parses the file and recomputes the canonical form: re-indenting the file
    keeps the seal, changing any value breaks it.
  * A sealed run_id is never overwritten. A re-run that rebuilds the identical object (the stored made_at is reused
    unless --made-at is given) is idempotent and only re-upserts the ledger rows; any difference is refused (exit 1).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import numbers
import os
import re
import statistics
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Mapping, NoReturn, Sequence

import pandas as pd
from jsonschema import Draft202012Validator

from oracle import config, ordering
from oracle.backend import Backend, DbRef, get_backend
from oracle.lessons_store import FEATURES, LessonsStore, cold_start_weights, get_lessons_store, predict_p, validate_weights

SCHEMA_VERSION = 1
ROUND_DIGITS = 6
PREDICTION_SUFFIX = ".prediction.json"
HASH_SUFFIX = ".sha256"
_RUN_ID_RE = re.compile(r"r([0-9]+)-(.+)")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")


class LeakageError(RuntimeError):
    """Input a prediction for this target may not see (exit 4)."""


class SealBroken(config.SealViolation):
    """No seal for the run, or the sealed file no longer matches its hash."""


class ResealRefused(RuntimeError):
    """The run_id is already sealed with a different prediction."""


class JsonArgumentParser(argparse.ArgumentParser):
    """argparse that reports usage errors as ONE JSON object and exit 1 (argparse's own exit 2 would read as the
    skip-list lane in rote-plays.md)."""

    def error(self, message: str) -> NoReturn:
        config.emit({"ok": False, "error": f"usage: {message}"})
        sys.exit(config.EXIT_HARD)


def read_json_arg(value: str) -> Any:
    """JSON from a file path, or from stdin when value is '-'."""
    return json.loads(sys.stdin.read() if value == "-" else Path(value).read_text(encoding="utf-8"))


def round6(value: float) -> float:
    """Round to ROUND_DIGITS; `+ 0.0` turns -0.0 into 0.0 so the canonical bytes never carry a sign artefact."""
    return round(float(value), ROUND_DIGITS) + 0.0


# ------------------------------------------------------------------ paths, hash, schema
def predictions_dir(settings: config.Settings | None = None) -> Path:
    return (settings or config.get_settings()).local_dir / "predictions"


def prediction_paths(run_id: str, directory: str | os.PathLike | None = None) -> tuple[Path, Path]:
    """(<dir>/<run_id>.prediction.json, <dir>/<run_id>.sha256); dir defaults to <LOCAL_DIR>/predictions."""
    config.validate_id(run_id, what="run_id")
    base = Path(directory) if directory is not None else predictions_dir()
    return base / f"{run_id}{PREDICTION_SUFFIX}", base / f"{run_id}{HASH_SUFFIX}"


def prediction_hash(obj: Mapping[str, Any]) -> str:
    """SHA-256 hex of the canonical JSON (contracts/prediction.schema.json). Independent of key order."""
    return config.sha256_hex(config.canonical_json(obj))


def pretty_json(obj: Mapping[str, Any]) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(config.PREDICTION_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_prediction(obj: Any) -> None:
    """ValueError unless `obj` matches contracts/prediction.schema.json."""
    errors = sorted(_validator().iter_errors(obj), key=lambda e: e.json_path)
    if errors:
        raise ValueError("prediction does not match contracts/prediction.schema.json: "
                         + "; ".join(f"{e.json_path}: {e.message}" for e in errors[:5]))


def parse_run_id(run_id: str, target_exam: str) -> int:
    """run_id must be r<seq>-<target_exam> (kit/03-architecture/data-model.md); returns seq."""
    config.validate_id(run_id, what="run_id")
    match = _RUN_ID_RE.fullmatch(run_id)
    if not match or match.group(2) != target_exam:
        raise ValueError(f"run_id {run_id!r} must be r<seq>-{target_exam}")
    return int(match.group(1))


def parse_made_at(value: str | None) -> str:
    """The given ISO-8601 timestamp (it must carry a UTC offset), or now in local time with its offset."""
    if value is None:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"made_at {value!r} is not an ISO-8601 timestamp") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"made_at {value!r} needs a UTC offset, e.g. 2026-09-11T13:12:04-07:00")
    return value


# ------------------------------------------------------------------ signals
def _signal(value: Any, what: str) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(float(value)):
        raise ValueError(f"signal {what} must be a finite number, got {value!r}")
    return round6(value)


def parse_signals(data: Any) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """({topic_id: {x1..x7: float}}, {course?, target_exam?, feature_set?}) from the shapes in the module docstring."""
    meta: dict[str, Any] = {}
    rows = data
    if isinstance(data, Mapping):
        meta = {k: data[k] for k in ("course", "target_exam", "feature_set") if data.get(k) is not None}
        keys = [k for k in ("signals", "topics", "rows") if k in data]
        if len(keys) != 1:
            raise ValueError("signals JSON needs exactly one of the keys 'signals', 'topics', 'rows'")
        rows = data[keys[0]]
    if isinstance(rows, Mapping):
        rows = [{"topic_id": tid, "signals": values} for tid, values in rows.items()]
    if not isinstance(rows, list):
        raise ValueError("signals must be a list of topic rows or a {topic_id: signals} mapping")
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError(f"bad signals row {row!r}")
        tid = config.validate_id(row.get("topic_id"), what="topic_id")
        values = row.get("signals", row)
        if not isinstance(values, Mapping):
            raise ValueError(f"signals of {tid} must be an object")
        if tid in out:
            raise ValueError(f"duplicate signals row for topic {tid}")
        out[tid] = {x: _signal(values.get(x), f"{tid}.{x}") for x in FEATURES}
    return out, meta


# ------------------------------------------------------------------ K and baselines
def choose_k(exam_counts: pd.DataFrame, course: str, exam_type: str, n_topics: int) -> tuple[int, str]:
    """K from one row per visible exam (columns course, exam_type, n_topics); see the module docstring."""
    if n_topics < 1:
        raise ValueError("the course has no topics")
    tiers = (("course_same_type", (exam_counts["course"] == course) & (exam_counts["exam_type"] == exam_type)),
             ("all_courses_same_type", exam_counts["exam_type"] == exam_type),
             ("all_visible_exams", pd.Series(True, index=exam_counts.index, dtype=bool)))
    for source, mask in tiers:
        counts = [int(n) for n in exam_counts.loc[mask, "n_topics"]]
        if counts:
            return max(1, min(n_topics, math.floor(statistics.median(counts) + 0.5))), source
    return 1, "floor"


def exam_topic_counts(items: pd.DataFrame) -> pd.DataFrame:
    """Distinct topics per visible exam, from the (exam, topic) rows load_context() returns."""
    if items.empty:
        return pd.DataFrame({"course": [], "exam_id": [], "exam_type": [], "n_topics": []})
    return items.groupby(["course", "exam_id", "exam_type"], as_index=False).agg(n_topics=("topic_id", "nunique"))


def baseline_even(signals: Mapping[str, Mapping[str, float]], k: int, feature_set: str) -> list[str]:
    """Baseline A, study evenly: top-K by lecture time x4 (ties topic_id); [] when lecture data is not visible."""
    if feature_set != "full":
        return []
    return sorted(signals, key=lambda t: (-signals[t]["x4"], t))[:k]


def baseline_last_exam(items: pd.DataFrame, course: str, exam_type: str,
                       signals: Mapping[str, Mapping[str, float]], k: int) -> tuple[list[str], str | None]:
    """Baseline B, copy last exam: (topic_ids, exam_id copied or None). See the module docstring."""
    chosen: list[str] = []
    last_exam: str | None = None
    same = items[(items["course"] == course) & (items["exam_type"] == exam_type)] if not items.empty else items
    if not same.empty:
        exams = same[["exam_id", "term_seq", "session"]].drop_duplicates("exam_id")
        last_exam = min(exams.itertuples(index=False),
                        key=lambda e: (-int(e.term_seq), -ordering.session_or_end(e.session), e.exam_id)).exam_id
        points = same[same["exam_id"] == last_exam].groupby("topic_id")["pts"].sum()
        ranked = sorted(((str(t), round(float(v), 9)) for t, v in points.items() if t in signals),
                        key=lambda tv: (-tv[1], tv[0]))
        chosen = [t for t, _ in ranked][:k]
    pad = sorted((t for t in signals if t not in chosen), key=lambda t: (-signals[t]["x4"], t))
    return chosen + pad[: k - len(chosen)], last_exam


# ------------------------------------------------------------------ the prediction object
def build_prediction(*, run_id: str, course: str, target_exam: str, feature_set: str, made_at: str,
                     weights: Mapping[str, float], lessons_run_seq: int | None, cold_start: bool, k: int,
                     topic_names: Mapping[str, str], signals: Mapping[str, Mapping[str, float]],
                     even: Sequence[str], last_exam: Sequence[str]) -> dict[str, Any]:
    """The object of contracts/prediction.schema.json, every topic ranked by (p desc, topic_id); validated."""
    w = validate_weights(weights)
    scored = sorted(((tid, round6(predict_p(w, signals[tid]))) for tid in topic_names), key=lambda tp: (-tp[1], tp[0]))
    topics = [{"topic_id": tid, "topic": topic_names[tid], "p": p, "rank": rank, "in_top_k": rank <= k,
               "signals": {x: signals[tid][x] for x in FEATURES}} for rank, (tid, p) in enumerate(scored, 1)]
    obj = {"schema_version": SCHEMA_VERSION, "run_id": run_id, "course": course, "target_exam": target_exam,
           "feature_set": feature_set, "cold_start": bool(cold_start), "made_at": made_at,
           "lessons_run_seq": lessons_run_seq, "k": k, "weights": w, "topics": topics,
           "baselines": {"even": list(even), "last_exam": list(last_exam)}}
    validate_prediction(obj)
    return obj


def prediction_rows(obj: Mapping[str, Any], digest: str) -> list[dict[str, Any]]:
    """Rows of the ledger `predictions` table, one per topic."""
    return [{"run_id": obj["run_id"], "exam_id": obj["target_exam"], "topic_id": t["topic_id"], "p": t["p"],
             "rank": t["rank"], "in_top_k": t["in_top_k"], **{x: t["signals"][x] for x in FEATURES},
             "hash": digest, "made_at": obj["made_at"], "feature_set": obj["feature_set"]} for t in obj["topics"]]


# ------------------------------------------------------------------ ledger context
_COURSE_SQL = "SELECT published_term_seq FROM {{courses}} WHERE course = $course"
_TOPICS_SQL = "SELECT topic_id, topic FROM {{topics}} WHERE course = $course ORDER BY topic_id"
_EXAM_SQL = 'SELECT course, exam_type, term_seq, "session" FROM {{exams}} WHERE exam_id = $exam_id'
_ITEMS_SQL = ('SELECT course, exam_id, exam_type, term_seq, "session", topic_id, sum(points_share) AS pts '
              "FROM {{exam_items}} WHERE exam_id <> $exam_id AND " + ordering.visible_sql()
              + ' GROUP BY course, exam_id, exam_type, term_seq, "session", topic_id')


def load_context(backend: Backend, ledger: DbRef, course: str, target_exam: str) -> dict[str, Any]:
    """Course, fixed topic list, target exam and the visible (exam, topic, points) rows, all from the ledger."""
    courses = backend.query(ledger, _COURSE_SQL, {"course": course})
    if len(courses) != 1:
        raise ValueError(f"course {course!r} is not in the ledger's courses table")
    topics = backend.query(ledger, _TOPICS_SQL, {"course": course})
    if topics.empty:
        raise ValueError(f"course {course!r} has no topics in the ledger")
    exam = backend.query(ledger, _EXAM_SQL, {"exam_id": target_exam})
    if len(exam) != 1 or str(exam.iloc[0]["course"]) != course:
        raise ValueError(f"exam {target_exam!r} of course {course!r} is not in the ledger's exams table")
    row = exam.iloc[0]
    term_seq = int(row["term_seq"])
    session = None if pd.isna(row["session"]) else int(row["session"])
    items = backend.query(ledger, _ITEMS_SQL, {"exam_id": target_exam, **ordering.visibility_params(term_seq, session)})
    return {"exam_type": str(row["exam_type"]), "term_seq": term_seq, "session": session,
            "published_term_seq": int(courses.iloc[0]["published_term_seq"]),
            "topics": {str(r.topic_id): str(r.topic) for r in topics.itertuples(index=False)}, "items": items}


# ------------------------------------------------------------------ the seal
@contextmanager
def _seal_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / ".seal.lock", "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)  # mkstemp creates 0600; the reveal step must be able to read and re-hash the file
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _check_seal(json_path: Path, hash_path: Path) -> tuple[Any, str]:
    try:
        stored = hash_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SealBroken(f"no seal at {hash_path}") from exc
    if not _HEX64_RE.fullmatch(stored):
        raise SealBroken(f"{hash_path} does not hold a SHA-256 hex digest")
    try:
        obj = json.loads(json_path.read_text(encoding="utf-8"))
        actual = prediction_hash(obj)
    except (OSError, ValueError) as exc:
        raise SealBroken(f"cannot re-hash {json_path}: {exc}") from exc
    if actual != stored:
        raise SealBroken(f"{json_path.name} does not match its seal (sealed {stored[:12]}..., now {actual[:12]}...)")
    return obj, stored


def verify_hash(path: str | os.PathLike, hash_path: str | os.PathLike | None = None) -> bool:
    """True iff SHA-256(canonical JSON of the parsed file) equals the stored hash. The hash file defaults to the
    sibling <run_id>.sha256. Formatting of the file does not matter; any changed value does."""
    path = Path(path)
    if hash_path is None:
        if not path.name.endswith(PREDICTION_SUFFIX):
            raise ValueError(f"{path} is not a <run_id>{PREDICTION_SUFFIX} file; pass hash_path")
        hash_path = path.with_name(path.name[: -len(PREDICTION_SUFFIX)] + HASH_SUFFIX)
    try:
        _check_seal(path, Path(hash_path))
        return True
    except SealBroken:
        return False


def read_sealed(run_id: str, directory: str | os.PathLike | None = None) -> tuple[dict[str, Any], str]:
    """(prediction, hash) of a sealed run after verifying it; SealBroken if unsealed, missing or tampered."""
    json_path, hash_path = prediction_paths(run_id, directory)
    obj, digest = _check_seal(json_path, hash_path)
    if not isinstance(obj, dict) or obj.get("run_id") != run_id:
        raise SealBroken(f"{json_path.name} is not the prediction of run {run_id}")
    return obj, digest


def seal(obj: Mapping[str, Any], directory: str | os.PathLike | None = None) -> tuple[str, Path, bool]:
    """Write the pretty file, then its hash file. Returns (hash, prediction path, already_sealed).
    An existing seal is never replaced: identical content is a no-op, anything else raises ResealRefused."""
    validate_prediction(obj)
    digest = prediction_hash(obj)
    json_path, hash_path = prediction_paths(obj["run_id"], directory)
    with _seal_lock(json_path.parent):
        if hash_path.exists():
            _, existing = read_sealed(obj["run_id"], directory)
            if existing != digest:
                raise ResealRefused(f"run {obj['run_id']} is already sealed ({existing[:12]}...) with a different "
                                    f"prediction; a sealed prediction is never replaced, use a new run_id")
            return digest, json_path, True
        atomic_write(json_path, pretty_json(obj))
        atomic_write(hash_path, (digest + "\n").encode("ascii"))
    return digest, json_path, False


# ------------------------------------------------------------------ the step
def rank_and_seal(run_id: str, course: str, target_exam: str, signals_data: Any, *, cold_start: bool = False,
                  made_at: str | None = None, backend: Backend | None = None,
                  lessons_store: LessonsStore | None = None,
                  directory: str | os.PathLike | None = None) -> dict[str, Any]:
    """Rank, seal and record one prediction; returns the summary the CLI prints."""
    config.validate_id(course, what="course")
    config.validate_id(target_exam, what="target_exam")
    run_seq = parse_run_id(run_id, target_exam)
    be = backend or get_backend()
    ledger = be.ensure_ledger()
    ctx = load_context(be, ledger, course, target_exam)
    fset = ordering.feature_set(ctx["term_seq"], ctx["published_term_seq"])

    signals, meta = parse_signals(signals_data)
    for key, want in (("course", course), ("target_exam", target_exam), ("feature_set", fset)):
        if key in meta and meta[key] != want:
            raise ValueError(f"signals were computed for {key}={meta[key]!r}, expected {want!r}")
    missing, extra = sorted(set(ctx["topics"]) - set(signals)), sorted(set(signals) - set(ctx["topics"]))
    if missing or extra:
        raise ValueError(f"signals must cover exactly the course's topic list (missing={missing}, unknown={extra})")
    if fset == "exam_history":
        leaked = sorted(f"{t}.{x}" for t, s in signals.items() for x in ordering.EXAM_HISTORY_ZERO_SIGNALS if s[x])
        if leaked:
            raise LeakageError(f"exam_history target {target_exam}: x2..x6 must be 0 (published-term material is in "
                               f"its future), got non-zero {leaked[:6]}")

    if cold_start:
        weights, lessons_seq = cold_start_weights(), None
    else:
        record = (lessons_store or get_lessons_store()).read_latest()
        weights, lessons_seq = record["weights"], record["run_seq"]
        if lessons_seq is not None and lessons_seq >= run_seq:
            raise LeakageError(f"latest lessons come from run_seq {lessons_seq} >= this run's {run_seq}: they were "
                               f"fitted on this target's label; runs go forward in time only")

    items = ctx["items"]
    k, k_source = choose_k(exam_topic_counts(items), course, ctx["exam_type"], len(ctx["topics"]))
    even = baseline_even(signals, k, fset)
    last, last_exam_id = baseline_last_exam(items, course, ctx["exam_type"], signals, k)

    _, hash_path = prediction_paths(run_id, directory)
    if made_at is None and hash_path.exists():
        made_at = read_sealed(run_id, directory)[0]["made_at"]  # an idempotent re-run keeps the sealed timestamp
    made_at = parse_made_at(made_at)

    obj = build_prediction(run_id=run_id, course=course, target_exam=target_exam, feature_set=fset, made_at=made_at,
                           weights=weights, lessons_run_seq=lessons_seq, cold_start=cold_start, k=k,
                           topic_names=ctx["topics"], signals=signals, even=even, last_exam=last)
    digest, path, already = seal(obj, directory)
    be.load_table(ledger, "predictions", prediction_rows(obj, digest), mode="upsert")
    return {"ok": True, "run_id": run_id, "hash": digest, "made_at": made_at, "k": k,
            "top_k": [t["topic_id"] for t in obj["topics"] if t["in_top_k"]], "k_source": k_source,
            "feature_set": fset, "cold_start": bool(cold_start), "lessons_run_seq": lessons_seq,
            "baselines": obj["baselines"], "last_exam_copied": last_exam_id, "prediction_path": str(path),
            "already_sealed": already}


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(prog="python -m oracle.rank_and_seal",
                                description="Rank all topics, seal the prediction (SHA-256) and record it.")
    parser.add_argument("--run-id", required=True, help="r<seq>-<target_exam>")
    parser.add_argument("--course", required=True)
    parser.add_argument("--target-exam", required=True)
    parser.add_argument("--signals", required=True, help="signals JSON file, or '-' for stdin")
    parser.add_argument("--cold-start", action="store_true", help="use COLD_START_WEIGHTS instead of the lessons")
    parser.add_argument("--made-at", help="ISO-8601 timestamp with offset (default: now)")
    args = parser.parse_args(argv)
    try:
        out = rank_and_seal(args.run_id, args.course, args.target_exam, read_json_arg(args.signals),
                            cold_start=args.cold_start, made_at=args.made_at)
    except LeakageError as exc:
        config.emit({"ok": False, "leakage": True, "error": str(exc)})
        return config.EXIT_LEAKAGE
    except Exception as exc:  # every other failure is a hard fault of this step
        config.emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return config.EXIT_HARD
    config.emit(out)
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
