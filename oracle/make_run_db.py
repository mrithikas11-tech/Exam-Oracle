"""python -m oracle.make_run_db --course C --target-exam E --run-id r<seq>-E [--expires-in-hours 2]

Build the per-run database run-<run_id>: a fresh database holding ONLY the ledger rows of course C that the
target exam E may see. First step of Play 2 `run-backtest` (kit/03-architecture/rote-plays.md).

Spec: contracts/README.md "The time-ordering rule" (visible iff earlier term, or same term and earlier
session; unknown session = end of term), kit/02-product/data-sources-and-sealing.md sealing rule 2 (one
database per backtest with only pre-target rows), kit/BUILDER-RULES.md §4 (sealing is sacred).

Row filters, per table (ROW_FILTERS; the visibility predicate is ordering.visible_sql()):
  courses, topics   course structure: every row of course C (the fixed topic list is shared by all terms)
  exams             visible, NOT sealed, not E itself        (E's metadata is read from the ledger instead)
  exam_items        visible, not E, and its exam is a copied exams row (so a sealed exam's items never pass)
  lectures, homework_items   visible
  homework_vec      its hw_id is a visible homework_items row (the table carries no term/session)
  echo_pairs        homework side AND exam side visible, the homework is visible, the exam is a copied exam
  guidelines        visible by (source_term_seq, source_session): stated before E
Every ledger read happens before the run database is created; if a load fails the run database is dropped.
Prints ONE JSON object: row counts per table and the run database handle. Exit 0 ok, 1 hard fault.
"""
from __future__ import annotations

import sys
from typing import Any

from oracle import config, ordering
from oracle.backend import RUN_TABLES, Backend, columns, get_backend
from oracle.run_context import JsonArgumentParser, add_run_args, load_target, run_seq_of

_VISIBLE = ordering.visible_sql()
_COPIED_EXAMS = ("SELECT exam_id FROM {{exams}} WHERE course = $course AND NOT sealed "
                 "AND exam_id <> $target_exam AND " + _VISIBLE)
_VISIBLE_HW = "SELECT hw_id FROM {{homework_items}} WHERE course = $course AND " + _VISIBLE

ROW_FILTERS: dict[str, str] = {
    "courses": "course = $course",
    "topics": "course = $course",
    "exams": "course = $course AND NOT sealed AND exam_id <> $target_exam AND " + _VISIBLE,
    "exam_items": ("course = $course AND exam_id <> $target_exam AND " + _VISIBLE
                   + " AND exam_id IN (" + _COPIED_EXAMS + ")"),
    "lectures": "course = $course AND " + _VISIBLE,
    "homework_items": "course = $course AND " + _VISIBLE,
    "homework_vec": "course = $course AND hw_id IN (" + _VISIBLE_HW + ")",
    "echo_pairs": ("course = $course AND exam_id <> $target_exam"
                   " AND " + ordering.visible_sql("hw_term_seq", "hw_session")
                   + " AND " + ordering.visible_sql("exam_term_seq", "exam_session")
                   + " AND hw_id IN (" + _VISIBLE_HW + ") AND exam_id IN (" + _COPIED_EXAMS + ")"),
    "guidelines": "course = $course AND " + ordering.visible_sql("source_term_seq", "source_session"),
}
if set(ROW_FILTERS) != set(RUN_TABLES):  # every table a run database holds must have an explicit filter
    raise RuntimeError(f"ROW_FILTERS must cover RUN_TABLES exactly: {sorted(set(RUN_TABLES) ^ set(ROW_FILTERS))}")


def select_sql(table: str) -> str:
    """SELECT of exactly the contract columns of `table`, filtered to the rows target E may see."""
    cols = ", ".join(f'"{c.name}"' for c in columns(table))
    return "SELECT %s FROM {{%s}} WHERE %s" % (cols, table, ROW_FILTERS[table])


def build_run_db(backend: Backend, course: str, target_exam: str, run_id: str, *,
                 expires_in_hours: float = 2.0) -> dict[str, Any]:
    """Create run-<run_id> and fill every RUN_TABLES table with the visible rows. Returns the JSON summary."""
    target = load_target(backend, course, target_exam)
    run_seq = run_seq_of(run_id, target.exam_id)
    ledger = backend.ledger()
    params = target.params()
    frames = {table: backend.query(ledger, select_sql(table), params) for table in RUN_TABLES}
    sealed = backend.query(ledger, "SELECT exam_id FROM {{exams}} WHERE course = $course AND sealed "
                                   "ORDER BY exam_id", {"course": course})
    handle = backend.create_run_db(run_id, expires_in_hours=expires_in_hours, tables=RUN_TABLES)
    try:
        counts = {table: backend.load_table(handle, table, frames[table], mode="replace") for table in RUN_TABLES}
    except BaseException:
        backend.drop_run_db(handle)  # never leave a half-filled run database behind
        raise
    return {
        "ok": True, "course": course, "target_exam": target.exam_id, "run_id": run_id, "run_seq": run_seq,
        "feature_set": target.feature_set, "target": target.to_dict(), "run_db": handle.to_dict(),
        "row_counts": counts, "excluded_sealed_exams": [str(x) for x in sealed["exam_id"]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(prog="python -m oracle.make_run_db",
                                description="Build run-<run_id> with only the ledger rows the target may see.")
    add_run_args(parser, run_db=False)
    parser.add_argument("--expires-in-hours", type=float, default=2.0, help="hotdata expiry (default 2)")
    args = parser.parse_args(argv)
    try:
        out = build_run_db(get_backend(), args.course, args.target_exam, args.run_id,
                           expires_in_hours=args.expires_in_hours)
    except Exception as exc:  # CLI boundary (incl. duckdb.Error, which is not a RuntimeError): one JSON object
        config.emit({"ok": False, "error": f"{type(exc).__name__}: {exc}", "course": args.course,
                     "target_exam": args.target_exam, "run_id": args.run_id})
        return config.EXIT_HARD
    config.emit(out)
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
