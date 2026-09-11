"""python -m oracle.run_list --list data/run_lists/fixture.csv [--dry-run] [--from-seq N] [--allow-machine-key]
                            [--allow-machine-labels]

Run a CSV run list through oracle.backtest, strictly forward in time: kit/02-product/prediction-model.md "Run list
(forward in time only)", kit/02-product/data-sources-and-sealing.md sealing rule 5, kit/BUILDER-RULES.md §4.

File: CSV, one header line, then one row per backtest; lines whose first non-blank character is '#' are comments.
  run_seq      integer >= 1, strictly increasing down the file (so unique); gaps are allowed
  course       course code
  target_exam  exam_id of the target (<course>-<exam_type>-<term>)
  cold_start   true | false; true = the blank-weights twin of an earlier row with the same target
  feature_set  optional annotation, checked: full iff the target is in the course's published term, else
               exam_history (contracts/README.md). Other columns (e.g. note) are ignored.

Validation, all of it before the first run (any violation: exit 1, nothing runs, every problem is listed):
  * every target is in the ledger's exams table, in the row's course, and that course has a courses row;
  * forward in time: within a course, every non-twin run is strictly later, by (term_seq, session) with an unknown
    session = end of term, than every non-twin run with a smaller run_seq. "Runs" are the rows of the list AND the
    rows already in the ledger's `runs` table (a list row whose run_id is already there is a replay of that run; its
    cold_start must match). Different courses are ordered by run_seq only: lessons transfer across courses, and the
    kit's list runs course 2 (possibly 2.71, Spring 2014) before 6.003 (2009-2011).
  * a twin repeats the target of an earlier non-twin run; one twin per target; its own time is exempt (it re-makes a
    prediction already made, with the fixed cold-start weights);
  * no other run_id may already hold a row's run_seq in the ledger's `runs` table (backtest's preflight also checks
    `predictions`).
Then the rows run in file order (from the first row with run_seq >= --from-seq, if given); the first failure stops
the list and its exit code is the list's. Prints ONE JSON object: {ok, list, rows, runs: [per-run summary],
failed_run?, evidence?}. --dry-run validates and prints the plan only.
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from oracle import backtest, config, ordering
from oracle.backend import Backend, DbRef, get_backend

REQUIRED_COLUMNS = ("run_seq", "course", "target_exam", "cold_start")
FEATURE_SETS = ("full", "exam_history")
_EXAMS_SQL = ('SELECT e.exam_id, e.course, e.term_seq, e."session", c.published_term_seq '
              "FROM {{exams}} AS e LEFT JOIN {{courses}} AS c ON c.course = e.course")
_RUNS_SQL = "SELECT run_id, run_seq, course, target_exam, cold_start FROM {{runs}}"


class RunListError(ValueError):
    """The run list file cannot be read (exit 1)."""


@dataclass(frozen=True)
class Row:
    line: int
    run_seq: int
    course: str
    target_exam: str
    cold_start: bool
    feature_set: str | None = None

    @property
    def run_id(self) -> str:
        return f"r{self.run_seq}-{self.target_exam}"


@dataclass(frozen=True)
class ExamTime:
    """When an exam was given, and its course's published term (from the ledger)."""
    course: str
    term_seq: int
    session: int | None
    published_term_seq: int | None

    @property
    def key(self) -> tuple[int, int]:
        return self.term_seq, ordering.session_or_end(self.session)

    @property
    def feature_set(self) -> str | None:
        if self.published_term_seq is None:
            return None
        return ordering.feature_set(self.term_seq, self.published_term_seq)


@dataclass(frozen=True)
class LedgerRun:
    run_id: str
    run_seq: int
    course: str
    target_exam: str
    cold_start: bool


# ------------------------------------------------------------------ reading
def _cell(record: Mapping[str, Any], name: str) -> str:
    value = record.get(name)
    return value.strip() if isinstance(value, str) else ""


def read_run_list(path: str | Path) -> list[Row]:
    """Parse the run list file (module docstring). RunListError on a malformed file or row."""
    numbered = [(n, line) for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1)
                if line.strip() and not line.lstrip().startswith("#")]
    if not numbered:
        raise RunListError(f"{path}: no header line")
    reader = csv.DictReader([line for _, line in numbered])
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise RunListError(f"{path}: missing columns {missing} (need {', '.join(REQUIRED_COLUMNS)})")
    rows: list[Row] = []
    for (line, _), record in zip(numbered[1:], reader):
        where = f"{Path(path).name}:{line}"
        try:
            run_seq = int(_cell(record, "run_seq"))
            course = config.validate_id(_cell(record, "course"), what="course")
            target = config.validate_id(_cell(record, "target_exam"), what="target_exam")
        except ValueError as exc:
            raise RunListError(f"{where}: {exc}") from None
        twin = _cell(record, "cold_start").lower()
        if twin not in ("true", "false"):
            raise RunListError(f"{where}: cold_start must be true or false, got {twin!r}")
        fset = _cell(record, "feature_set") or None
        if fset is not None and fset not in FEATURE_SETS:
            raise RunListError(f"{where}: feature_set must be one of {FEATURE_SETS}, got {fset!r}")
        if run_seq < 1:
            raise RunListError(f"{where}: run_seq must be >= 1")
        rows.append(Row(line, run_seq, course, target, twin == "true", fset))
    if not rows:
        raise RunListError(f"{path}: the run list has no rows")
    return rows


def load_exam_times(backend: Backend, ledger: DbRef) -> dict[str, ExamTime]:
    frame = backend.query(ledger, _EXAMS_SQL)
    return {str(r.exam_id): ExamTime(str(r.course), int(r.term_seq),
                                     None if ordering.session_or_end(r.session) == ordering.END_OF_TERM else int(r.session),
                                     None if ordering.session_or_end(r.published_term_seq) == ordering.END_OF_TERM
                                     else int(r.published_term_seq))
            for r in frame.itertuples(index=False)}


def load_ledger_runs(backend: Backend, ledger: DbRef) -> list[LedgerRun]:
    frame = backend.query(ledger, _RUNS_SQL)
    return [LedgerRun(str(r.run_id), int(r.run_seq), str(r.course), str(r.target_exam), bool(r.cold_start))
            for r in frame.itertuples(index=False)]


# ------------------------------------------------------------------ validation
def validate(rows: Sequence[Row], exams: Mapping[str, ExamTime],
             history: Sequence[LedgerRun] = ()) -> list[str]:
    """Every problem with the list (module docstring); [] means it may run."""
    problems: list[str] = []
    previous = 0
    for r in rows:
        where = f"line {r.line} ({r.run_id})"
        if r.run_seq <= previous:
            problems.append(f"{where}: run_seq {r.run_seq} is not greater than {previous} (it must increase)")
        previous = max(previous, r.run_seq)
        info = exams.get(r.target_exam)
        if info is None:
            problems.append(f"{where}: {r.target_exam} is not in the ledger's exams table")
            continue
        if info.course != r.course:
            problems.append(f"{where}: {r.target_exam} belongs to course {info.course}, not {r.course}")
        if info.published_term_seq is None:
            problems.append(f"{where}: course {info.course} has no courses row (published term unknown)")
        elif r.feature_set is not None and r.feature_set != info.feature_set:
            problems.append(f"{where}: annotated feature_set {r.feature_set} but the ordering rule gives "
                            f"{info.feature_set} (published term {ordering.seq_to_term(info.published_term_seq)})")

    listed = {r.run_id: r for r in rows}
    for h in history:
        if h.run_id in listed:
            if listed[h.run_id].cold_start != h.cold_start:
                problems.append(f"line {listed[h.run_id].line} ({h.run_id}): cold_start differs from the ledger's run")
        elif any(r.run_seq == h.run_seq for r in rows):
            problems.append(f"run_seq {h.run_seq} is already used by ledger run {h.run_id}")

    # (run_seq, course, target, twin, where) for the list rows and the ledger's other runs, in run_seq order
    timeline = [(r.run_seq, r.course, r.target_exam, r.cold_start, f"line {r.line} ({r.run_id})")
                for r in rows if r.target_exam in exams]
    timeline += [(h.run_seq, h.course, h.target_exam, h.cold_start, f"ledger run {h.run_id}")
                 for h in history if h.run_id not in listed and h.target_exam in exams]
    latest: dict[str, tuple[tuple[int, int], str]] = {}
    made: set[str] = set()
    twins: set[str] = set()
    for _, course, exam, twin, where in sorted(timeline, key=lambda e: e[0]):
        if twin:
            if exam not in made:
                problems.append(f"{where}: a cold-start twin needs an earlier non-twin run of {exam}")
            elif exam in twins:
                problems.append(f"{where}: {exam} already has a cold-start twin")
            twins.add(exam)
            continue
        key = exams[exam].key
        if course in latest and key <= latest[course][0]:
            problems.append(f"{where}: {exam} is not later than {latest[course][1]}: runs go forward in time only")
        if course not in latest or key > latest[course][0]:
            latest[course] = (key, f"{exam} ({where})")
        made.add(exam)
    return problems


# ------------------------------------------------------------------ running
def _compact(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in summary.items() if k != "outputs"}


def run_list(path: str | Path, *, dry_run: bool = False, from_seq: int | None = None,
             allow_machine_key: bool = False, allow_machine_labels: bool = False) -> tuple[int, dict[str, Any]]:
    """Validate the whole list, then run it (module docstring). Returns (exit code, the printed object)."""
    rows = read_run_list(path)
    be = get_backend()
    ledger = be.ledger()
    problems = validate(rows, load_exam_times(be, ledger), load_ledger_runs(be, ledger))
    base: dict[str, Any] = {"list": str(path), "rows": len(rows)}
    if problems:
        return config.EXIT_HARD, {"ok": False, **base, "error": "run list refused; nothing ran", "problems": problems}
    if dry_run:
        return config.EXIT_OK, {"ok": True, **base, "dry_run": True,
                                "plan": [{"run_id": r.run_id, "course": r.course, "cold_start": r.cold_start,
                                          "feature_set": r.feature_set} for r in rows]}
    runs: list[dict[str, Any]] = []
    for r in rows:
        if from_seq is not None and r.run_seq < from_seq:
            runs.append({"run_id": r.run_id, "skipped": f"before --from-seq {from_seq}"})
            continue
        bt = backtest.Backtest(r.course, r.target_exam, r.run_seq, cold_start=r.cold_start,
                               allow_machine_key=allow_machine_key, allow_machine_labels=allow_machine_labels)
        code, summary = backtest.run_backtest(bt)
        runs.append(_compact(summary))
        if code != config.EXIT_OK:
            return code, {"ok": False, **base, "runs": runs, "failed_run": r.run_id, "exit_code": code,
                          "failed_step": summary.get("failed_step"), "evidence": summary.get("evidence")}
    return config.EXIT_OK, {"ok": True, **base, "runs": runs}


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be an integer >= 1, got {value}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = config.ScriptParser(prog="python -m oracle.run_list",
                                 description="Run a CSV run list through oracle.backtest, forward in time only.")
    parser.add_argument("--list", required=True, help="run list CSV (run_seq, course, target_exam, cold_start)")
    parser.add_argument("--dry-run", action="store_true", help="validate against the ledger and print the plan")
    parser.add_argument("--from-seq", type=_positive_int, help="skip rows with a smaller run_seq (resume a list)")
    parser.add_argument("--allow-machine-key", action="store_true", help="passed to every backtest")
    parser.add_argument("--allow-machine-labels", action="store_true", help="passed to every backtest")
    args = parser.parse_args(argv)
    try:
        code, out = run_list(args.list, dry_run=args.dry_run, from_seq=args.from_seq,
                             allow_machine_key=args.allow_machine_key, allow_machine_labels=args.allow_machine_labels)
    except Exception as exc:  # unreadable list or ledger: a hard fault, one JSON object
        config.emit({"ok": False, "list": args.list, "error": f"{type(exc).__name__}: {exc}"})
        return config.EXIT_HARD
    config.emit(out)
    return code


if __name__ == "__main__":
    sys.exit(main())
