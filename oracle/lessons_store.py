"""Beta lessons: the learned weights of the transparent signal model, versioned by run_seq.

Spec: kit/02-product/prediction-model.md "From signals to probability" (p = sigma(b0 + sum bk*xk); the beta
weights ARE the lessons; cold start = small equal positive weights with b6 = 0; each beta stored as a lesson
in HydraDB `shared` with signal, weight, run_seq, supporting run ids and a one-line statement),
kit/03-architecture/data-model.md (HydraDB: lesson memories id = lesson_<signal>_r<seq>),
contracts/README.md (HydraDB ingest shape, verified against hydradb-sdk 2.1.4),
contracts/ledger-schema.sql (the `lessons` mirror table, filled from to_ledger_rows()).

A lessons *record* — what read_latest() returns and write() stores:
    {"run_seq": int | None,            # None = cold start (no lessons written yet)
     "weights": {"intercept", "x1".."x7": float},
     "statements": {signal: str},      # subset of SIGNALS; {} at cold start
     "supporting_runs": [run_id, ...]}
"Latest" = the highest run_seq ever written (ties: the most recent write), so a late or replayed write of an
older run can never move the lessons backwards.
"""
from __future__ import annotations

import fcntl
import json
import math
import numbers
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from oracle import config

FEATURES = ("x1", "x2", "x3", "x4", "x5", "x6", "x7")
SIGNALS = ("intercept", *FEATURES)
# Display names of the signals (prediction-model.md "Seven signals" table), for statements and C's charts.
SIGNAL_LABELS = {"x1": "Track record", "x2": "Coverage", "x3": "Homework echo", "x4": "Lecture time",
                 "x5": "Untested recent material", "x6": "Already tested", "x7": "Professor said"}
RECORD_KEYS = ("run_seq", "weights", "statements", "supporting_runs")
MAX_STATEMENT_CHARS = 500
MAX_METADATA_BYTES = 1024  # HydraDB additional_metadata cap (hydra_db ContextClient.ingest docstring)
POINTER_ID = "lessons_latest"

# Cold start (prediction-model.md): every feature weight is the same small positive number, except x6
# ("already tested"), whose sign is unknown a priori ("may be positive or negative"), so it starts at 0.
# The intercept puts a mid-signal topic near p = 0.4. "Mid-signal" (MID_SIGNAL_EXAMPLE) is a typical
# full-feature-set topic: on half the prior exams (x1 = 0.5), half its lectures in the window (x2 = 0.5),
# a middling homework echo (x3 = 0.5), a 1/N lecture share (x4 = 0.1), and no x5/x7 flag:
#     sum(b*x) = 0.5 * (0.5 + 0.5 + 0.5 + 0.1) = 0.8;  z = -1.2 + 0.8 = -0.4;  p = sigma(-0.4) = 0.40.
# A topic with no signal gets sigma(-1.2) = 0.23; one maxed on every signal gets sigma(1.8) = 0.86.
# Treat this dict as read-only; cold_start_weights() returns a copy.
COLD_START_WEIGHTS: dict[str, float] = {
    "intercept": -1.2, "x1": 0.5, "x2": 0.5, "x3": 0.5, "x4": 0.5, "x5": 0.5, "x6": 0.0, "x7": 0.5,
}
MID_SIGNAL_EXAMPLE: dict[str, float] = {"x1": 0.5, "x2": 0.5, "x3": 0.5, "x4": 0.1, "x5": 0.0, "x6": 0.0, "x7": 0.0}


def cold_start_weights() -> dict[str, float]:
    return dict(COLD_START_WEIGHTS)


def cold_start_record() -> dict[str, Any]:
    return {"run_seq": None, "weights": cold_start_weights(), "statements": {}, "supporting_runs": []}


def predict_p(weights: Mapping[str, float], signals: Mapping[str, float]) -> float:
    """p = sigma(b0 + sum_k bk*xk) (prediction-model.md). A missing signal counts as 0."""
    z = float(weights["intercept"]) + sum(float(weights[k]) * float(signals.get(k, 0.0)) for k in FEATURES)
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def validate_weights(weights: Mapping[str, Any]) -> dict[str, float]:
    """Exactly the keys intercept, x1..x7, each a finite number. Returns plain floats in SIGNALS order."""
    if not isinstance(weights, Mapping):
        raise ValueError("weights must be a mapping")
    missing = [s for s in SIGNALS if s not in weights]
    extra = [k for k in weights if k not in SIGNALS]
    if missing or extra:
        raise ValueError(f"weights must have exactly {SIGNALS} (missing={missing}, extra={extra})")
    out: dict[str, float] = {}
    for signal in SIGNALS:
        value = weights[signal]
        if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(float(value)):
            raise ValueError(f"weight {signal}={value!r} is not a finite number")
        out[signal] = float(value)
    return out


def make_record(run_seq: int, weights: Mapping[str, Any], statements: Mapping[str, str],
                supporting_runs: Sequence[str]) -> dict[str, Any]:
    """Validate and normalise one lessons version (see the module docstring for the shape)."""
    if isinstance(run_seq, bool) or not isinstance(run_seq, numbers.Integral) or int(run_seq) < 0:
        raise ValueError(f"run_seq must be a non-negative integer, got {run_seq!r}")
    clean_weights = validate_weights(weights)
    if not isinstance(statements, Mapping):
        raise ValueError("statements must be a mapping {signal: text}")
    clean_statements: dict[str, str] = {}
    for signal, text in statements.items():
        if signal not in SIGNALS or not isinstance(text, str):
            raise ValueError(f"bad statement entry {signal!r}: {text!r}")
        text = " ".join(text.split())
        if len(text) > MAX_STATEMENT_CHARS:
            raise ValueError(f"statement for {signal} exceeds {MAX_STATEMENT_CHARS} characters")
        if text:
            clean_statements[signal] = text
    if isinstance(supporting_runs, str):
        raise ValueError("supporting_runs must be a list of run_ids, not one string")
    runs = list(dict.fromkeys(config.validate_id(r, what="run_id") for r in supporting_runs))
    return {"run_seq": int(run_seq), "weights": clean_weights,
            "statements": dict(sorted(clean_statements.items())), "supporting_runs": runs}


def to_ledger_rows(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Rows for the hotdata `lessons` mirror (ledger-schema.sql), one per signal; supporting_runs ';'-joined."""
    rec = make_record(record["run_seq"], record["weights"], record.get("statements", {}),
                      record.get("supporting_runs", []))
    runs = ";".join(rec["supporting_runs"])
    return [{"run_seq": rec["run_seq"], "signal": s, "weight": rec["weights"][s],
             "statement": rec["statements"].get(s), "supporting_runs": runs} for s in SIGNALS]


@runtime_checkable
class LessonsStore(Protocol):
    def read_latest(self) -> dict[str, Any]:
        """The latest record, or cold_start_record() when nothing has been written."""
        ...

    def write(self, run_seq: int, weights: Mapping[str, float], statements: Mapping[str, str],
              supporting_runs: Sequence[str]) -> dict[str, Any]:
        """Store one lessons version; returns the normalised record."""
        ...


# ------------------------------------------------------------------ local (offline) store
class LocalJSONLessonsStore:
    """Append-only history in <LOCAL_DIR>/lessons.jsonl (one record per line, plus written_at).
    read_latest() derives "latest" from the history, so there is no second copy that can drift."""

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path is not None else config.get_settings().local_dir / "lessons.jsonl"

    def history(self) -> list[dict[str, Any]]:
        """Every record ever written, oldest first."""
        if not self.path.is_file():
            return []
        records = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"{self.path}:{number}: corrupt lessons record") from exc
        return records

    def read_latest(self) -> dict[str, Any]:
        records = self.history()
        if not records:
            return cold_start_record()
        _, best = max(enumerate(records), key=lambda pair: (pair[1]["run_seq"], pair[0]))
        return make_record(best["run_seq"], best["weights"], best.get("statements", {}), best.get("supporting_runs", []))

    def write(self, run_seq: int, weights: Mapping[str, float], statements: Mapping[str, str],
              supporting_runs: Sequence[str]) -> dict[str, Any]:
        record = make_record(run_seq, weights, statements, supporting_runs)
        line = json.dumps({**record, "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                          sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        return record


# ------------------------------------------------------------------ HydraDB (live) store
def _metadata_size(metadata: Mapping[str, Any]) -> int:
    return len(json.dumps(metadata, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _fit_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Keep additional_metadata under HydraDB's 1 KiB cap by dropping the bulky optional fields."""
    if _metadata_size(metadata) <= MAX_METADATA_BYTES:
        return metadata
    slim = {k: v for k, v in metadata.items() if k not in ("runs", "weights")}
    if _metadata_size(slim) > MAX_METADATA_BYTES:
        raise ValueError("lesson metadata exceeds HydraDB's 1 KiB additional_metadata cap")
    return slim


def lesson_memories(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """HydraDB memory items for one record: lesson_<signal>_r<seq> per signal (text = the statement) plus the
    pointer 'lessons_latest' (text = canonical JSON of the record). All infer:false (stored verbatim)."""
    run_seq = record["run_seq"]
    runs = ";".join(record["supporting_runs"])
    items = []
    for signal in SIGNALS:
        weight = record["weights"][signal]
        text = record["statements"].get(signal) or f"{signal} weight {weight:+.4f} after run {run_seq}."
        items.append({"id": f"lesson_{signal}_r{run_seq}", "title": f"Lesson {signal} (run {run_seq})", "text": text,
                      "infer": False, "additional_metadata": _fit_metadata(
                          {"kind": "lesson", "signal": signal, "weight": weight, "run_seq": run_seq, "runs": runs})})
    items.append({"id": POINTER_ID, "title": "Latest lessons", "text": config.canonical_json(dict(record)).decode("utf-8"),
                  "infer": False, "additional_metadata": _fit_metadata(
                      {"kind": "lessons_pointer", "run_seq": run_seq, "weights": record["weights"]})})
    return items


class HydraDBLessonsStore:
    """Lessons in hosted HydraDB, database HYDRADB_DATABASE, collection 'shared' (data-model.md).

    write() upserts one memory per signal (id lesson_<signal>_r<seq>) and the pointer memory 'lessons_latest'
    (skipped when the stored pointer already has a higher run_seq). read_latest() reads the pointer via
    context.inspect; if its content is unavailable it falls back to context.list(ids=[...]) and the weights
    copy in the pointer's additional_metadata. Everything that touches the service is LIVE [TEST]:
    ingest answers 202 Accepted, so a read straight after a write may still see the previous version — the
    hotdata `lessons` mirror (to_ledger_rows) is the synchronous record for charts."""

    def __init__(self, client: Any | None = None, database: str | None = None,
                 collection: str = config.HYDRADB_SHARED_COLLECTION) -> None:
        self._client = client
        self.database = config.validate_id(database or config.get_settings().hydradb_database, what="HydraDB database")
        self.collection = config.validate_id(collection, what="HydraDB collection")

    @property
    def client(self) -> Any:
        if self._client is None:
            token = config.env("HYDRADB_API_KEY")
            if not token:
                raise config.ConfigError("HYDRADB_API_KEY is not set (LESSONS_STORE=hydradb)")
            from hydra_db import HydraDB
            self._client = HydraDB(token=token, base_url=config.env("HYDRADB_BASE_URL"))  # LIVE [TEST]
        return self._client

    def _read_pointer(self) -> dict[str, Any] | None:
        from hydra_db.errors import NotFoundError
        try:
            response = self.client.context.inspect(  # LIVE [TEST] does inspect serve memories, not only sources?
                id=POINTER_ID, database=self.database, collection=self.collection)
            content = getattr(getattr(response, "data", None), "content", None)
            if content:
                data = json.loads(content)
                return make_record(data["run_seq"], data["weights"], data.get("statements", {}),
                                   data.get("supporting_runs", []))
        except NotFoundError:
            pass
        except (ValueError, KeyError, TypeError):
            pass  # unexpected content shape: fall back to the metadata copy below
        try:
            response = self.client.context.list(  # LIVE [TEST]
                database=self.database, collection=self.collection, ids=[POINTER_ID], type="memory")
        except NotFoundError:
            return None
        for item in getattr(getattr(response, "data", None), "user_memories", None) or []:
            metadata = getattr(item, "additional_metadata", None) or {}
            if getattr(item, "memory_id", None) == POINTER_ID and "weights" in metadata:
                return make_record(metadata["run_seq"], metadata["weights"], {}, [])
        return None

    def read_latest(self) -> dict[str, Any]:
        return self._read_pointer() or cold_start_record()

    def write(self, run_seq: int, weights: Mapping[str, float], statements: Mapping[str, str],
              supporting_runs: Sequence[str]) -> dict[str, Any]:
        record = make_record(run_seq, weights, statements, supporting_runs)
        items = lesson_memories(record)
        current = self._read_pointer()
        if current is not None and current["run_seq"] > record["run_seq"]:
            items = [item for item in items if item["id"] != POINTER_ID]  # never move "latest" backwards
        self.client.context.ingest(  # LIVE [TEST]
            database=self.database, collection=self.collection, type="memory",
            memories=json.dumps(items, ensure_ascii=False, allow_nan=False), upsert="true")
        return record


def get_lessons_store(settings: config.Settings | None = None) -> LessonsStore:
    """The store selected by LESSONS_STORE (local by default)."""
    s = settings or config.get_settings()
    if s.lessons_store == "hydradb":
        return HydraDBLessonsStore(database=s.hydradb_database)
    return LocalJSONLessonsStore(s.local_dir / "lessons.jsonl")
