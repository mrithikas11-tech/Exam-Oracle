"""Store one lessons version: `python -m oracle.store_lessons --lessons-json '<refit_lessons output>'`.

Spec: kit/03-architecture/rote-plays.md (store_lessons.py: lessons -> HydraDB `shared` memories; Play 2 step 9),
kit/02-product/prediction-model.md (each beta is stored as a lesson with signal, weight, run_seq, supporting
run ids and a one-line statement), contracts/ledger-schema.sql `lessons` (mirror for charts, key (run_seq, signal)).

Input: the JSON object oracle.refit_lessons prints. Only run_seq, weights, statements and supporting_runs are
used. It can be passed inline (--lessons-json, which is how a Rote step passes an earlier step's output) or read
from a file (--input PATH, where '-' means stdin). A failed refit ({"ok": false}) and a cold-start record
(run_seq null) are refused.

Order: the lessons store (LESSONS_STORE) is written first, then the 8 mirror rows are upserted into the ledger.
A failure part-way therefore leaves at most a store version without its mirror. Re-running the step is
idempotent: HydraDB upserts by memory id, the local JSONL serves the newest write of a run_seq, and the mirror
upserts on (run_seq, signal).

Prints ONE JSON object. Exit codes (rote-plays.md): 0 ok; 1 hard fault (bad input, store or ledger write failed).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from oracle import config
from oracle.backend import Backend, DbRef, get_backend
from oracle.lessons_store import (HydraDBLessonsStore, LessonsStore, LocalJSONLessonsStore, get_lessons_store,
                                  make_record, to_ledger_rows)
from oracle.read_lessons import ScriptArgumentParser, parse_cli


def load_lessons_input(*, lessons_json: str | None = None, path: str | None = None) -> dict[str, Any]:
    """Parse the refit output from an inline JSON string or a file ('-' = stdin)."""
    if lessons_json is None and path is None:
        raise ValueError("pass the lessons as inline JSON or a file path")
    if lessons_json is not None:
        text = lessons_json
    elif path == "-":
        text = sys.stdin.read()
    else:
        text = Path(path).read_text(encoding="utf-8")  # type: ignore[arg-type]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("the lessons input must be one JSON object (the output of oracle.refit_lessons)")
    if data.get("ok") is False:
        raise ValueError(f"refusing to store the output of a failed refit: {data.get('error')}")
    return data


def _store_kind(store: LessonsStore) -> str:
    if isinstance(store, HydraDBLessonsStore):
        return "hydradb"
    if isinstance(store, LocalJSONLessonsStore):
        return "local"
    return type(store).__name__


def store_lessons(lessons: Mapping[str, Any], *, store: LessonsStore | None = None,
                  backend: Backend | None = None, ledger: DbRef | None = None) -> dict[str, Any]:
    """Write one version to the lessons store, then mirror it into the ledger `lessons` table (upsert).
    Returns the JSON object the CLI prints."""
    if "run_seq" not in lessons or "weights" not in lessons:
        raise ValueError("the lessons input needs run_seq and weights")
    record = make_record(lessons["run_seq"], lessons["weights"], lessons.get("statements") or {},
                         lessons.get("supporting_runs") or [])
    target = store if store is not None else get_lessons_store()
    written = target.write(record["run_seq"], record["weights"], record["statements"], record["supporting_runs"])
    be = backend if backend is not None else get_backend()
    db = ledger if ledger is not None else be.ensure_ledger()
    mirrored = be.load_table(db, "lessons", to_ledger_rows(written), mode="upsert")
    return {"ok": True, **written, "lessons_store": _store_kind(target), "ledger_rows": mirrored}


def main(argv: Sequence[str] | None = None) -> int:
    parser = ScriptArgumentParser(
        prog="python -m oracle.store_lessons",
        description="Store one lessons version in the lessons store and mirror it into the ledger `lessons` "
                    "table. Prints one JSON object.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--lessons-json", metavar="JSON", help="the JSON object printed by oracle.refit_lessons")
    source.add_argument("--input", metavar="PATH", help="a file holding that JSON object ('-' = stdin)")
    args = parse_cli(parser, argv)
    if isinstance(args, int):
        return args
    try:
        out = store_lessons(load_lessons_input(lessons_json=args.lessons_json, path=args.input))
    except Exception as exc:  # script boundary: every failure is reported as one JSON object (hard-fault lane)
        config.emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return config.EXIT_HARD
    config.emit(out)
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
