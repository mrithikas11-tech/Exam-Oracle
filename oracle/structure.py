"""Load a course's structure into the ledger: courses, topics, exams, lectures, guidelines.

Source: role B's data/courses/<course>/ (course.json, topics.csv, exams.csv, sessions.csv, guidelines.csv).
Only the contract columns are loaded (contracts/ledger-schema.sql). The sealed exam gets its `exams` row
(metadata only, sealed = TRUE) so the ordering rule can place it in time; its items are never loaded.
Every load is a keyed upsert, so a replay never duplicates rows.
"""
from __future__ import annotations

from oracle.backend import get_backend

from .loader_config import OracleError, check_course, emit, save_report
from .structure_io import contract_rows, course_dir, read_course_json, read_csv

TOPIC_SOURCE_COLUMNS = {"first_lecture": "first_session", "last_lecture": "last_session"}


def add_args(p):
    p.add_argument("--course", required=True)


def build_tables(course: str) -> dict[str, list[dict]]:
    folder = course_dir(course)
    tables = {
        "courses": contract_rows("courses", [read_course_json(course)]),
        "topics": contract_rows("topics", read_csv(folder / "topics.csv"), TOPIC_SOURCE_COLUMNS),
        "exams": contract_rows("exams", read_csv(folder / "exams.csv")),
        "lectures": contract_rows("lectures", read_csv(folder / "sessions.csv")),
        "guidelines": contract_rows("guidelines", read_csv(folder / "guidelines.csv"))
        if (folder / "guidelines.csv").is_file() else [],
    }
    for table, rows in tables.items():
        wrong = {r["course"] for r in rows if r.get("course") != course}
        if wrong:
            raise OracleError(f"{table}: rows for other courses {sorted(wrong)} in data/courses/{course}/", 3)
    return tables


def run(args) -> int:
    course = check_course(args.course)
    tables = build_tables(course)
    backend = get_backend()
    ledger = backend.ensure_ledger()
    counts = {table: backend.load_table(ledger, table, rows, mode="upsert") if rows else 0
              for table, rows in tables.items()}
    report = {"ok": True, "course": course, "backend": backend.name, "rows": counts,
              "sealed_exams": sorted(r["exam_id"] for r in tables["exams"] if r["sealed"])}
    save_report(course, "structure", report)
    emit(report)
    return 0
