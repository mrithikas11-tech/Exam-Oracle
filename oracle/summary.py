"""Roll up one load of a course and append it to ledger.course_loads (the "cheaper" chart).

seconds = wall time since the index step started. agent_tokens is the coding agent's
reasoning tokens for a recorded (agent) load, passed in by hand; replays spend none (-1 = n/a).
"""
from __future__ import annotations

import csv
import datetime as dt
import json

from .config import OracleError, check_course, course_path, emit, read_report, write_json
from .hotdata_cli import ledger_catalog, load_file

COLUMNS = ["load_id", "course", "mode", "started_at", "finished_at", "seconds", "docs", "items",
           "degraded", "sealed_excluded", "checks_first_try", "agent_tokens"]


def add_args(p):
    p.add_argument("--course", required=True)
    p.add_argument("--mode", choices=["agent", "replay"], default="replay")
    p.add_argument("--agent-tokens", type=int, default=-1)


def run(args) -> int:
    course = check_course(args.course)
    work = course_path("work", course)
    index_path = work / "index.json"
    if not index_path.exists():
        raise OracleError(f"no index for {course}")
    index = json.loads(index_path.read_text())
    started = dt.datetime.fromisoformat(index["started_at"])
    finished = dt.datetime.now(dt.timezone.utc)
    reports = {s: read_report(course, s) or {} for s in ("extract", "split", "validate", "load", "cognee", "tags")}
    validate = reports["validate"]
    row = {
        "load_id": f"load-{course}-{started.strftime('%Y%m%dT%H%M%SZ')}",
        "course": course, "mode": args.mode,
        "started_at": index["started_at"], "finished_at": finished.isoformat(timespec="seconds"),
        "seconds": round((finished - started).total_seconds(), 1),
        "docs": len(list((work / "docs").glob("*.json"))),
        "items": reports["load"].get("items", 0),
        "degraded": sum(int(r.get("degraded", 0) or 0) for r in reports.values()),
        "sealed_excluded": index.get("sealed_excluded", 0),
        "checks_first_try": bool(validate.get("ok") and validate.get("attempts", 1) == 1),
        "agent_tokens": args.agent_tokens,
    }
    path = course_path("work", course, "load") / "course_loads.csv"
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow(row)
    load_file(ledger_catalog(), "course_loads", str(path), "upsert")
    summary = {"ok": True, **row, "rows": reports["load"].get("rows", {}),
               "warnings": [r["warning"] for r in reports.values() if r.get("warning")]}
    write_json(work / "summary.json", summary)
    emit(summary)
    return 0
