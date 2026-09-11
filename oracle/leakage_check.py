"""python -m oracle.leakage_check --course C --target-exam E --run-id r<seq>-E [--run-db ...]

Assert, INSIDE the run database, that nothing the target exam may not see is there. Third step of Play 2
(kit/03-architecture/rote-plays.md: exit 4 = leakage, hard). Its result is runs.leakage_ok.

Spec: contracts/README.md (time-ordering rule), kit/02-product/data-sources-and-sealing.md sealing rule 2
("the run asserts ... inside that database and logs leakage_ok"), kit/BUILDER-RULES.md §4.
Checks (sql/leakage.sql, one row each, n > 0 = leakage):
  * every dated row of every table is visible to E (lectures, exams, exam_items, homework_items,
    echo_pairs on both sides, guidelines by when they were stated); homework_vec rows map to visible homework;
  * no exams row is sealed; no row of E itself (exams, exam_items, echo_pairs);
  * every exam item / echo pair points at an exams row of the run database (a sealed exam never is one);
  * plus, from the LEDGER: no exam id the ledger marks sealed appears anywhere in the run database.
Prints ONE JSON object with every check, and the violations with sample row keys as evidence.
Exit 0 clean, 4 leakage, 1 hard fault (e.g. the run database or a table is missing).
"""
from __future__ import annotations

import sys
from typing import Any

from oracle import config
from oracle.backend import Backend, DbRef, get_backend
from oracle.run_context import (JsonArgumentParser, add_run_args, db_label, load_sql, load_target, resolve_run_db,
                                run_seq_of)

_EXAM_REFS_SQL = """
SELECT 'exams' AS tbl, exam_id, count(*) AS n FROM {{exams}} GROUP BY exam_id
UNION ALL
SELECT 'exam_items', exam_id, count(*) FROM {{exam_items}} GROUP BY exam_id
UNION ALL
SELECT 'echo_pairs', exam_id, count(*) FROM {{echo_pairs}} GROUP BY exam_id
"""


def _key(value: Any) -> str | None:
    """A sample row key, or None for SQL NULL (None, NaN or pandas.NA, depending on the backend's DataFrame)."""
    if value is None:
        return None
    try:
        if value != value:  # NaN
            return None
    except TypeError:  # pandas.NA has no truth value
        return None
    return str(value)


def check_leakage(backend: Backend, course: str, target_exam: str, run_id: str, *,
                  run_db: DbRef | None = None) -> dict[str, Any]:
    """Run every leakage check against the run database; returns the JSON result (leakage_ok True/False)."""
    target = load_target(backend, course, target_exam)
    run_seq_of(run_id, target.exam_id)
    ref = run_db if run_db is not None else resolve_run_db(None, run_id)

    rows = backend.query(ref, load_sql("leakage"), target.params())
    checks = [{"table": str(r.tbl), "check": str(r.chk), "n": int(r.n),
               "sample": [k for k in dict.fromkeys((_key(r.first_key), _key(r.last_key))) if k is not None]}
              for r in rows.itertuples(index=False)]

    sealed = {str(x) for x in backend.query(backend.ledger(), "SELECT exam_id FROM {{exams}} WHERE sealed")["exam_id"]}
    refs = backend.query(ref, _EXAM_REFS_SQL)
    for table in ("exams", "exam_items", "echo_pairs"):
        hits = {str(e): int(n) for t, e, n in zip(refs["tbl"], refs["exam_id"], refs["n"])
                if t == table and str(e) in sealed}
        checks.append({"table": table, "check": "sealed_in_ledger", "n": sum(hits.values()), "sample": sorted(hits)})

    violations = [c for c in checks if c["n"] > 0]
    return {
        "ok": not violations, "leakage_ok": not violations, "course": course, "target_exam": target.exam_id,
        "run_id": run_id, "run_db": db_label(ref), "target_term_seq": target.term_seq,
        "target_session": target.session, "checks": checks, "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(prog="python -m oracle.leakage_check",
                                description="Assert the run database holds only rows the target may see.")
    add_run_args(parser)
    args = parser.parse_args(argv)
    try:
        ref = resolve_run_db(args.run_db, args.run_id)
        out = check_leakage(get_backend(), args.course, args.target_exam, args.run_id, run_db=ref)
    except Exception as exc:  # CLI boundary (incl. duckdb.Error): a hard fault is exit 1, never exit 4
        config.emit({"ok": False, "leakage_ok": False, "error": f"{type(exc).__name__}: {exc}",
                     "course": args.course, "target_exam": args.target_exam, "run_id": args.run_id})
        return config.EXIT_HARD
    config.emit(out)
    return config.EXIT_OK if out["leakage_ok"] else config.EXIT_LEAKAGE


if __name__ == "__main__":
    sys.exit(main())
