"""Shared plumbing for the per-run steps: make_run_db -> run_signals -> leakage_check.

Spec: contracts/README.md ("The time-ordering rule"; "Ids": run_id = r<seq>-<exam_id>),
kit/03-architecture/rote-plays.md (Play 2 step order; every script prints ONE JSON object and uses the
documented exit codes), kit/02-product/data-sources-and-sealing.md (sealing rule 2: one database per backtest
holding only rows the target may see).

SQL files in sql/ follow the backend conventions ({{table}} placeholders, $name parameters) plus ONE macro,
expanded here before the query reaches a backend:

    @VISIBLE(alias.term_col, alias.session_col)   ->   ordering.visible_sql(term_col, session_col, alias=alias)

so the visibility rule lives in one place (oracle/ordering.py) and every signal, baseline and leakage query
applies the identical predicate, bound by visibility_params() of the target exam.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any

from oracle import config, ordering
from oracle.backend import Backend, DbHandle, DbRef, RUN_DB_PREFIX, run_db_name

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_VISIBLE_RE = re.compile(rf"@VISIBLE\(\s*(?:({_NAME})\.)?({_NAME})\s*,\s*(?:({_NAME})\.)?({_NAME})\s*\)")
_RUN_ID_RE = re.compile(r"r([0-9]{1,9})-(.+)")


class RunError(RuntimeError):
    """A hard fault in a run step (missing target, bad run id, missing database): exit code 1."""


# ------------------------------------------------------------------ SQL files
def expand_visible(sql: str) -> str:
    """Replace every @VISIBLE(a.term_col, a.session_col) with ordering.visible_sql(...). Both columns must use
    the same alias (or none). A malformed macro raises ValueError."""
    def expand(match: re.Match[str]) -> str:
        alias_term, term_col, alias_session, session_col = match.groups()
        if alias_term != alias_session:
            raise ValueError(f"@VISIBLE columns must share one alias: {match.group(0)}")
        return ordering.visible_sql(term_col, session_col, alias=alias_term)

    expanded = _VISIBLE_RE.sub(expand, sql)
    if "@VISIBLE" in expanded:
        raise ValueError("malformed @VISIBLE(...) macro in SQL")
    return expanded


def load_sql(name: str) -> str:
    """sql/<name>.sql with the @VISIBLE macro expanded (tables and $params are left to the backend)."""
    config.validate_id(name, what="SQL file name")
    path = config.SQL_DIR / f"{name}.sql"
    if not path.is_file():
        raise FileNotFoundError(f"SQL file not found: {path} (set ORACLE_REPO_ROOT?)")
    return expand_visible(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the target exam
def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if value != value:  # NaN
            return None
    except TypeError:  # pandas.NA
        return None
    return int(value)


def _opt_bool(value: Any) -> bool | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        return bool(value)
    except TypeError:  # pandas.NA
        return None


@dataclass(frozen=True)
class TargetExam:
    """Metadata of the target exam T, read from the LEDGER `exams` row (its metadata is course structure; its
    items and answer key are what is sealed). T's own row is never copied into the run database."""
    course: str
    exam_id: str
    exam_type: str
    term: str
    term_seq: int
    session: int | None                 # None = unknown -> end of term (ledger-schema.sql)
    coverage_from_session: int | None   # stated coverage window, None if not stated
    coverage_to_session: int | None
    cumulative: bool                    # stated flag; NULL -> True for finals (prediction-model.md x2)
    sealed: bool
    published_term_seq: int

    @property
    def feature_set(self) -> ordering.FeatureSet:
        return ordering.feature_set(self.term_seq, self.published_term_seq)

    def visibility_params(self) -> dict[str, int]:
        return ordering.visibility_params(self.term_seq, self.session)

    def params(self) -> dict[str, object]:
        """The $params every run query shares: course, target_exam, exam_type, target_term_seq, target_session."""
        return {"course": self.course, "target_exam": self.exam_id, "exam_type": self.exam_type,
                **self.visibility_params()}

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "feature_set": self.feature_set}


_TARGET_SQL = """
SELECT e.course, e.exam_id, e.exam_type, e.term, e.term_seq, e."session", e.coverage_from_session,
       e.coverage_to_session, e.cumulative, e.sealed, c.published_term_seq
FROM {{exams}} AS e
LEFT JOIN {{courses}} AS c ON c.course = e.course
WHERE e.exam_id = $exam_id
"""


def load_target(backend: Backend, course: str, exam_id: str) -> TargetExam:
    """Read T's exams row (+ its course's published term) from the ledger. RunError if it is missing, belongs
    to another course, or the course has no `courses` row."""
    config.validate_id(course, what="course")
    config.validate_id(exam_id, what="target exam")
    rows = backend.query(backend.ledger(), _TARGET_SQL, {"exam_id": exam_id})
    if rows.empty:
        raise RunError(f"target exam {exam_id!r} is not in the ledger's exams table")
    if len(rows) > 1:
        raise RunError(f"target exam {exam_id!r} has {len(rows)} exams rows (exam_id must be unique)")
    row = rows.iloc[0]
    if row["course"] != course:
        raise RunError(f"target exam {exam_id!r} belongs to course {row['course']!r}, not {course!r}")
    published = _opt_int(row["published_term_seq"])
    if published is None:
        raise RunError(f"course {course!r} has no courses row (published_term_seq is needed for feature_set)")
    stated_cumulative = _opt_bool(row["cumulative"])
    exam_type = config.validate_id(str(row["exam_type"]), what="exam_type")
    return TargetExam(
        course=course, exam_id=exam_id, exam_type=exam_type, term=str(row["term"]),
        term_seq=ordering.as_term_seq(int(row["term_seq"])), session=_opt_int(row["session"]),
        coverage_from_session=_opt_int(row["coverage_from_session"]),
        coverage_to_session=_opt_int(row["coverage_to_session"]),
        cumulative=stated_cumulative if stated_cumulative is not None else exam_type == "final",
        sealed=bool(_opt_bool(row["sealed"])), published_term_seq=ordering.as_term_seq(published))


# ------------------------------------------------------------------ run ids and run databases
def run_seq_of(run_id: str, target_exam: str) -> int:
    """'r4-FX.101-final-2022F' -> 4. The run id must be r<seq>-<target_exam> (contracts/README.md "Ids")."""
    config.validate_id(run_id, what="run_id")
    match = _RUN_ID_RE.fullmatch(run_id)
    if not match or match.group(2) != target_exam:
        raise RunError(f"run_id {run_id!r} must be r<seq>-{target_exam}")
    return int(match.group(1))


def resolve_run_db(run_db: str | None, run_id: str) -> DbRef:
    """The run database to use. `run_db` may be empty (-> run-<run_id> by name), a database name, a JSON
    DbHandle, a make_run_db output object (its "run_db" field is used), or a path to a .json file holding
    either. The database must be run-<run_id>: signals are never computed in another run's database."""
    expected = run_db_name(run_id)
    if not run_db or not run_db.strip():
        return expected
    text = run_db.strip()
    if not text.startswith("{"):
        if not text.endswith(".json"):
            if text != expected:
                raise RunError(f"run database {text!r} does not belong to run {run_id!r} (expected {expected!r})")
            return text
        with open(text, encoding="utf-8") as fh:
            text = fh.read()
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("run_db"), dict):
        data = data["run_db"]
    handle = DbHandle.from_dict(data)
    if handle.name != expected or not handle.name.startswith(RUN_DB_PREFIX):
        raise RunError(f"run database {handle.name!r} does not belong to run {run_id!r} (expected {expected!r})")
    return handle


def db_label(ref: DbRef) -> Any:
    """JSON-safe description of a database reference for script output."""
    return ref.to_dict() if isinstance(ref, DbHandle) else ref


# ------------------------------------------------------------------ CLI conventions
# argparse that reports usage errors as ONE JSON object and exit code 1 (rote-plays.md), instead of argparse's
# plain-text exit 2, which would read as EXIT_SKIP_LIST. One shared copy lives in oracle.config.
JsonArgumentParser = config.ScriptParser


def add_run_args(parser: argparse.ArgumentParser, *, run_db: bool = True) -> None:
    parser.add_argument("--course", required=True, help="course code, e.g. 6.003")
    parser.add_argument("--target-exam", required=True, help="exam_id of the target, e.g. 6.003-final-2011F")
    parser.add_argument("--run-id", required=True, help="r<seq>-<target_exam>")
    if run_db:
        parser.add_argument("--run-db", default=None,
                            help="run database: make_run_db's JSON output (or its run_db handle), a .json file "
                                 "holding it, or the name; default run-<run_id>")
