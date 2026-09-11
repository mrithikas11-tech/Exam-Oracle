"""Reading role B's course-structure files (data/courses/<course>/) and shaping rows to the ledger contract."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from oracle import config as structure_config
from oracle.backend import columns

from .loader_config import OracleError, check_course

TRUE = {"true", "1", "yes"}
FALSE = {"false", "0", "no"}


def course_dir(course: str) -> Path:
    path = structure_config.DATA_DIR / "courses" / check_course(course)
    if not path.is_dir():
        raise OracleError(f"no course structure at {path}: add data/courses/{course}/ "
                          "(or set ORACLE_REPO_ROOT to the repository when running from elsewhere)")
    return path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(line for line in f if not line.lstrip().startswith("#")))


def read_course_json(course: str) -> dict[str, Any]:
    info = json.loads((course_dir(course) / "course.json").read_text(encoding="utf-8"))
    if info.get("course") != course:
        raise OracleError(f"course.json names {info.get('course')!r}, expected {course!r}", 3)
    return info


def as_bool(value: Any) -> bool | None:
    if isinstance(value, bool) or value is None:
        return value
    text = str(value).strip().lower()
    if text == "":
        return None
    if text in TRUE:
        return True
    if text in FALSE:
        return False
    raise ValueError(f"not a boolean: {value!r}")


def cast(value: Any, dtype: str) -> Any:
    if value is None or (isinstance(value, float) and value != value) or (isinstance(value, str) and value.strip() == ""):
        return None
    if dtype == "INTEGER":
        return int(float(value))
    if dtype == "DOUBLE":
        return float(value)
    if dtype == "BOOLEAN":
        return as_bool(value)
    return str(value).strip() if isinstance(value, str) else str(value)


def contract_rows(table: str, records: Iterable[Mapping[str, Any]],
                  rename: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Keep exactly the contract columns of `table` (contracts/ledger-schema.sql), cast to their types.
    `rename` maps a contract column to the source column that holds it."""
    rename = rename or {}
    rows = []
    for n, rec in enumerate(records, 1):
        row = {}
        for col in columns(table):
            try:
                row[col.name] = cast(rec.get(rename.get(col.name, col.name)), col.type)
            except (TypeError, ValueError) as err:
                raise OracleError(f"{table} row {n}, column {col.name}: {err}", 3)
        rows.append(row)
    return rows


def exams_by_id(course: str) -> dict[str, dict[str, str]]:
    return {r["exam_id"]: r for r in read_csv(course_dir(course) / "exams.csv")}
