"""Roll up one load of a course and upsert it into ledger.course_loads (the "cheaper" chart).

seconds = wall time since the index step started. agent_tokens is the coding agent's reasoning tokens for a
recorded (agent) load, passed in by hand; NULL when not measured. Replays spend no agent tokens.
"""
from __future__ import annotations

import datetime as dt
import json

from oracle.backend import get_backend

from .loader_config import OracleError, check_course, course_path, emit, read_report, write_json
from .structure_io import contract_rows

STEPS = ("extract", "split", "validate", "structure", "items", "tag", "load")


def add_args(p):
    p.add_argument("--course", required=True)
    p.add_argument("--mode", choices=["agent", "replay"], default="replay")
    p.add_argument("--agent-tokens", type=int, default=None)


def run(args) -> int:
    course = check_course(args.course)
    work = course_path("work", course)
    index_path = work / "index.json"
    if not index_path.exists():
        raise OracleError(f"no index for {course}")
    index = json.loads(index_path.read_text())
    started = dt.datetime.fromisoformat(index["started_at"])
    finished = dt.datetime.now(dt.timezone.utc)
    reports = {s: read_report(course, s) or {} for s in STEPS}
    validate = reports["validate"]
    row = {
        "load_id": f"load-{course}-{started.strftime('%Y%m%dT%H%M%SZ')}",
        "course": course, "mode": args.mode,
        "started_at": index["started_at"], "finished_at": finished.isoformat(timespec="seconds"),
        "seconds": round((finished - started).total_seconds(), 1),
        "docs": len(list((work / "docs").glob("*.json"))),
        "items": int(reports["items"].get("items", 0)) + int(reports["load"].get("homework_problems", 0)),
        "degraded": sum(int(r.get("degraded", 0) or 0) for r in reports.values()),
        "sealed_excluded": index.get("sealed_excluded", 0),
        "checks_first_try": bool(validate.get("ok") and validate.get("attempts", 1) == 1),
        "agent_tokens": args.agent_tokens,
    }
    backend = get_backend()
    backend.load_table(backend.ensure_ledger(), "course_loads", contract_rows("course_loads", [row]), mode="upsert")
    summary = {"ok": True, **row, "backend": backend.name,
               "exam_items_rows": reports["tag"].get("exam_items_rows"),
               "warnings": [f"{s}: {r['warning']}" for s, r in reports.items() if r.get("warning")]}
    write_json(work / "summary.json", summary)
    emit(summary)
    return 0
