"""Tag a course's exam problems onto its fixed topic list with Cognee, then upsert the exam_items rows.

Runs role B's `oracle.cognee_tag` on work/<course>/items.json (it needs the course's `exams` and `topics`
rows in the ledger — run `structure` first) and loads the rows it returns. `--dry-run` only prices the
Cognee run. Without LLM_API_KEY the step is degraded (exit 0 + warning), unless --mock-tags is given.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile

from oracle import config as structure_config
from oracle.backend import get_backend, read_contract_csv

from .loader_config import OracleError, check_course, course_path, emit, log, save_report


def add_args(p):
    p.add_argument("--course", required=True)
    p.add_argument("--dry-run", action="store_true", help="price the Cognee run; loads nothing")
    p.add_argument("--mock-tags", help="offline stand-in: CSV exam_id,problem,topic_ids (see oracle.cognee_tag)")


def run_captured(fn) -> tuple[int, str, str]:
    """Run fn() with stdout captured at the file-descriptor level: cognee prints its cost tables straight to fd 1,
    which would break this step's one-JSON-line contract. Returns (exit code, Python stdout, raw fd-1 output)."""
    sys.stdout.flush()
    saved = os.dup(1)
    raw_file = tempfile.TemporaryFile()
    buf = io.StringIO()
    try:
        os.dup2(raw_file.fileno(), 1)
        with contextlib.redirect_stdout(buf):
            code = fn()
    finally:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os.dup2(saved, 1)
        os.close(saved)
    raw_file.seek(0)
    raw = raw_file.read().decode("utf-8", "replace")
    raw_file.close()
    return code, buf.getvalue(), raw


def _last_json(text: str) -> dict:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def run(args) -> int:
    course = check_course(args.course)
    work = course_path("work", course)
    items = work / "items.json"
    if not items.is_file():
        raise OracleError(f"no {items.name} for {course}; run `exam-oracle items` first")
    if not args.mock_tags and not structure_config.env("LLM_API_KEY"):
        report = {"ok": True, "course": course, "available": False, "degraded": 1,
                  "warning": "LLM_API_KEY not set; Cognee tagging skipped (exam_items not loaded)"}
        save_report(course, "tag", report)
        emit(report)
        return 0
    out_csv = work / "exam_items.tagged.csv"
    argv = ["--course", course, "--items", str(items)]
    argv += ["--dry-run"] if args.dry_run else ["--out", str(out_csv)]
    if args.mock_tags:
        argv += ["--mock-tags", args.mock_tags]
    def call() -> int:
        from oracle import cognee_tag  # imports cognee lazily: only this step needs it

        return cognee_tag.main(argv)

    code, py_out, raw_out = run_captured(call)
    if raw_out.strip():
        log(raw_out.strip()[-6000:])  # cognee's own output (cost tables, logs) goes to stderr
    result = _last_json(py_out) or _last_json(raw_out)
    if code != 0:
        log(py_out[-2000:])
        raise OracleError(f"cognee_tag exited {code}: {result.get('error', 'no JSON output')}",
                          code if code in (2, 3) else 1)
    if args.dry_run:
        report = {"ok": True, "course": course, "mode": "dry-run", "items": result.get("items"),
                  "estimates": result.get("estimates", [])}
        emit(report)
        return 0
    backend = get_backend()
    ledger = backend.ensure_ledger()
    rows = read_contract_csv(out_csv, "exam_items")
    loaded = backend.load_table(ledger, "exam_items", rows, mode="upsert")
    report = {"ok": True, "course": course, "available": True, "mode": result.get("mode"),
              "items": result.get("items"), "tagged": result.get("tagged"), "untagged": result.get("untagged", []),
              "exam_items_rows": loaded, "degraded": 1 if result.get("warning") else 0}
    if result.get("warning"):
        report["warning"] = result["warning"]
    save_report(course, "tag", report)
    emit(report)
    return 0
