"""python -m oracle.run_signals --course C --target-exam E --run-id r<seq>-E [--run-db ...] [--tau 0.02]

Compute the seven signals x1..x7 for EVERY topic of course C (0 where a signal has no row) in the run
database built by make_run_db, plus both baselines' topic orders. Second step of Play 2
(kit/03-architecture/rote-plays.md); rank_and_seal consumes the JSON it prints.

Spec: kit/02-product/prediction-model.md "Seven signals" and "Scoring" (baselines), adapted to
contracts/README.md: time is (term_seq, session), and a target outside the course's published term gets
feature_set = exam_history, where x2..x6 are 0 (the published term's lectures and psets are in its future);
only x1 and x7 are computed. Formulas: the header of each sql/x*.sql file.

Inputs outside the run database (both read-only, neither is exam content):
  * the target's own exams row (window, cumulative flag) from the ledger -- it is never copied into the run DB;
  * guideline trust per kind (sql/guideline_trust.sql) from the ledger: a lesson learned from strictly earlier
    runs, read the way the beta weights are read.
Signals are rounded to 6 decimals so replays on DuckDB and hotdata give identical sealed JSON.
Prints ONE JSON object. Exit 0 ok, 1 hard fault.
"""
from __future__ import annotations

import math
import sys
from typing import Any

from oracle import config
from oracle.backend import Backend, DbRef, get_backend
from oracle.lessons_store import FEATURES
from oracle.ordering import EXAM_HISTORY_ZERO_SIGNALS
from oracle.run_context import (JsonArgumentParser, RunError, TargetExam, add_run_args, db_label, load_sql,
                                load_target, resolve_run_db, run_seq_of)

SIGNAL_SQL: dict[str, str] = {
    "x1": "x1_track_record", "x2": "x2_coverage", "x3": "x3_homework_echo", "x4": "x4_lecture_time",
    "x5": "x5_untested_recent", "x6": "x6_already_tested", "x7": "x7_professor_said",
}
DEFAULT_TAU = 0.02      # echo threshold on the RRF score (1/(60+rank) per list; 2/61 ~ 0.033 is the maximum)
TRUST_KINDS = ("cumulative", "emphasis_window", "coverage")   # 'format' guidelines are never scored
DEFAULT_TRUST = 0.5     # trust of a guideline kind with no test yet
DIGITS = 6


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):  # pandas.NA
        return None
    return None if math.isnan(number) else number


def guideline_trust(backend: Backend, target: TargetExam, run_seq: int) -> dict[str, dict[str, Any]]:
    """{kind: {"trust", "n_tests"}} from the ledger's guideline tests of strictly earlier runs (guideline_trust.sql)."""
    rows = backend.query(backend.ledger(), load_sql("guideline_trust"), {**target.params(), "run_seq": run_seq})
    trust: dict[str, dict[str, Any]] = {k: {"trust": DEFAULT_TRUST, "n_tests": 0} for k in TRUST_KINDS}
    for kind, n_tests, value in zip(rows["kind"], rows["n_tests"], rows["trust"]):
        number = _num(value)
        if kind in trust and int(n_tests) > 0 and number is not None:
            trust[kind] = {"trust": round(number, DIGITS), "n_tests": int(n_tests)}
    return trust


def coverage_window(backend: Backend, run_db: DbRef, target: TargetExam) -> dict[str, Any]:
    """T's coverage window {"from", "to", "source"} (sql/coverage_window.sql)."""
    rows = backend.query(run_db, load_sql("coverage_window"), {
        **target.params(), "stated_from": target.coverage_from_session,
        "stated_to": target.coverage_to_session, "cumulative": target.cumulative})
    row = rows.iloc[0]
    return {"from": int(row["win_from"]), "to": int(row["win_to"]), "source": str(row["window_source"])}


def compute_signals(backend: Backend, course: str, target_exam: str, run_id: str, *,
                    run_db: DbRef | None = None, tau: float = DEFAULT_TAU) -> dict[str, Any]:
    """Signals for every topic of `course` for target `target_exam`, computed in the run database."""
    if isinstance(tau, bool) or not math.isfinite(float(tau)) or float(tau) < 0:
        raise ValueError(f"tau must be a finite number >= 0, got {tau!r}")
    target = load_target(backend, course, target_exam)
    run_seq = run_seq_of(run_id, target.exam_id)
    ref = run_db if run_db is not None else resolve_run_db(None, run_id)
    full = target.feature_set == "full"

    params: dict[str, object] = {**target.params(), "tau": float(tau)}
    trust = guideline_trust(backend, target, run_seq)
    params.update({f"trust_{kind}": entry["trust"] for kind, entry in trust.items()})
    window = coverage_window(backend, ref, target) if full else None
    if window:
        params.update(win_from=window["from"], win_to=window["to"])

    topics = backend.query(ref, "SELECT topic_id, topic FROM {{topics}} WHERE course = $course ORDER BY topic_id",
                           {"course": course})
    if topics.empty:
        raise RunError(f"run database has no topics for course {course!r} (did make_run_db run?)")
    values = {str(t): {x: 0.0 for x in FEATURES} for t in topics["topic_id"]}
    computed = [x for x in FEATURES if full or x not in EXAM_HISTORY_ZERO_SIGNALS]
    unknown: set[str] = set()
    for signal in computed:
        rows = backend.query(ref, load_sql(SIGNAL_SQL[signal]), params)
        for topic_id, value in zip(rows["topic_id"], rows[signal]):
            number = _num(value)
            if str(topic_id) not in values:
                unknown.add(str(topic_id))  # tagged outside the fixed topic list: ignored, reported
            elif number is not None:
                values[str(topic_id)][signal] = round(number, DIGITS)

    even: list[str] = []
    if full:
        even = [str(t) for t in backend.query(ref, load_sql("baseline_even"), params)["topic_id"]
                if str(t) in values]
    last_rows = backend.query(ref, load_sql("baseline_last_exam"), params)
    last_exam = [str(t) for t in last_rows["topic_id"] if str(t) in values]
    last_exam += [t for t in even if t not in last_exam]  # pad with lecture time (baseline A order)

    return {
        "ok": True, "course": course, "target_exam": target.exam_id, "run_id": run_id, "run_seq": run_seq,
        "run_db": db_label(ref), "feature_set": target.feature_set, "exam_type": target.exam_type,
        "target_term_seq": target.term_seq, "target_session": target.session,
        "computed": computed, "zeroed": [x for x in FEATURES if x not in computed],
        "window": window, "tau": float(tau), "trust": trust, "unknown_topics": sorted(unknown),
        "topics": [{"topic_id": str(t), "topic": str(name), "signals": values[str(t)]}
                   for t, name in zip(topics["topic_id"], topics["topic"])],
        "baselines": {"even": even, "last_exam": last_exam,
                      "last_exam_id": str(last_rows["exam_id"].iloc[0]) if not last_rows.empty else None},
    }


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(prog="python -m oracle.run_signals",
                                description="Compute x1..x7 and the baselines in the run database.")
    add_run_args(parser)
    parser.add_argument("--tau", type=float, default=DEFAULT_TAU, help=f"echo threshold (default {DEFAULT_TAU})")
    args = parser.parse_args(argv)
    try:
        ref = resolve_run_db(args.run_db, args.run_id)
        out = compute_signals(get_backend(), args.course, args.target_exam, args.run_id, run_db=ref, tau=args.tau)
    except Exception as exc:  # CLI boundary (incl. duckdb.Error, which is not a RuntimeError): one JSON object
        config.emit({"ok": False, "error": f"{type(exc).__name__}: {exc}", "course": args.course,
                     "target_exam": args.target_exam, "run_id": args.run_id})
        return config.EXIT_HARD
    config.emit(out)
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
