"""Cognee DataPoint models for Exam Oracle (the graph shapes Cognee extracts into and stores).

Spec: kit/03-architecture/data-model.md "Cognee — DataPoint models" (verified here against cognee 1.5.4);
contracts/student.md §1 (StudentProfile carries every contract field — the contract overrides the kit);
contracts/ledger-schema.sql (time inside a term is ordered by SESSION, so ExamWindow / StatedGuideline use
from_session / to_session like exams.coverage_*_session and guidelines.from/to_session, not the kit's weeks).

Identity (cognee/infrastructure/engine/models/DataPoint.py, 1.5.4): when a class's `metadata` default names
`identity_fields`, DataPoint.__init__ sets id = uuid5(NAMESPACE_OID, "<ClassName>:<v1>|<v2>...") where each
value is lower-cased, spaces -> "_" and apostrophes removed (DataPoint.id_for). The same identity therefore
always yields the same node id and duplicate mentions merge; without identity_fields the id is a random uuid4.
Every model below declares both `index_fields` (embedded for search) and `identity_fields`. A field typed as
another DataPoint (or a list of them) becomes a graph edge named after the field.

Importing this module imports cognee through import_cognee(): storage is pointed at the project folder first
(config.configure_cognee_env) and cognee's own import-time `.env` override is undone.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Literal, Mapping

from pydantic import Field

from oracle import config


def _dotenv_files_cognee_may_load() -> list[Path]:
    """The `.env` files that cognee's import-time `dotenv.load_dotenv(override=True)` can pick up: python-dotenv
    1.2 walks up from the calling file (cognee/__init__.py) — or from the cwd in a REPL or under a debugger —
    and takes the first `.env` it finds. The venv lives inside this repository, so that walk reaches
    <repo>/.env."""
    starts = [Path.cwd()]
    spec = importlib.util.find_spec("cognee")
    if spec is not None and spec.origin:
        starts.append(Path(spec.origin).resolve().parent)
    found: list[Path] = []
    for start in starts:
        for folder in (start, *start.parents):
            candidate = folder / ".env"
            if candidate.is_file():
                found.append(candidate)
                break
    return found


def _undo_dotenv(before: Mapping[str, str], dotenv_keys: set[str]) -> list[str]:
    """Restore os.environ after importing cognee. Variables that existed before get their previous value back
    (the environment wins over `.env`, as in config.load_dotenv); variables the import ADDED are removed only
    when a `.env` file defines them, so cognee's own import-time settings (LITELLM_LOG, TIKTOKEN_CACHE_DIR)
    stay. Returns the names that were touched."""
    touched: list[str] = []
    for key, value in before.items():
        if os.environ.get(key) != value:
            os.environ[key] = value
            touched.append(key)
    for key in [k for k in os.environ if k not in before and k in dotenv_keys]:
        del os.environ[key]
        touched.append(key)
    return sorted(touched)


def import_cognee() -> ModuleType:
    """Import cognee safely: configure its storage first (config.configure_cognee_env — its default is inside
    the installed package) and undo its `.env` override, so the test conftest's removal of LLM_API_KEY and
    ORACLE_SKIP_DOTENV keep working. Returns the cognee module; a no-op import if it is already loaded."""
    config.configure_cognee_env()
    if "cognee" in sys.modules:
        return sys.modules["cognee"]
    dotenv_keys: set[str] = set()
    for path in _dotenv_files_cognee_may_load():
        dotenv_keys |= set(config.parse_dotenv(path.read_text(encoding="utf-8")))
    before = dict(os.environ)
    try:
        import cognee
    finally:
        _undo_dotenv(before, dotenv_keys)
    return cognee


import_cognee()
from cognee.low_level import DataPoint  # noqa: E402  (after import_cognee: storage env + .env guard)


class Topic(DataPoint):
    """One entry of a course's FIXED topic list (prediction-model.md "Unit of prediction"); identity (course, name)."""

    course: str
    name: str
    topic_id: str | None = None          # 'T01'..'T20' from the topics table, when known
    metadata: dict = {"index_fields": ["name"], "identity_fields": ["course", "name"]}


class ExamProblem(DataPoint):
    """One pre-split exam problem; `topics` becomes edges to Topic nodes. problem_id = '<exam_id>-p<n>[<sub>]'."""

    problem_id: str
    exam_id: str
    points: float | None = None
    text: str
    topics: list[Topic] = Field(default_factory=list)
    metadata: dict = {"index_fields": ["text"], "identity_fields": ["problem_id"]}


class HomeworkProblem(DataPoint):
    """One homework problem; hw_id = '<course>-<term>-ps<set>-p<n>'."""

    hw_id: str
    text: str
    topics: list[Topic] = Field(default_factory=list)
    metadata: dict = {"index_fields": ["text"], "identity_fields": ["hw_id"]}


class Lecture(DataPoint):
    """One lecture/session of a term; identity (course, term, session) = the ledger key of `lectures`."""

    course: str
    term: str
    session: int
    title: str
    topics: list[Topic] = Field(default_factory=list)
    metadata: dict = {"index_fields": ["title"], "identity_fields": ["course", "term", "session"]}


class ExamWindow(DataPoint):
    """An exam's stated coverage window, in sessions (exams.coverage_from_session/coverage_to_session)."""

    course: str
    exam_type: str                       # quiz1 | quiz2 | quiz3 | midterm | final
    from_session: int | None = None
    to_session: int | None = None
    cumulative: bool = False
    metadata: dict = {"index_fields": [], "identity_fields": ["course", "exam_type"]}


class StatedGuideline(DataPoint):
    """A professor's stated guidance as a numeric claim (student-layer.md; the ledger `guidelines` columns)."""

    course: str
    kind: Literal["cumulative", "emphasis_window", "coverage", "format"]
    applies_to_exam_type: str | None = None
    from_session: int | None = None
    to_session: int | None = None
    share: float | None = None
    text: str                            # verbatim quote
    source_doc: str
    metadata: dict = {"index_fields": ["text"], "identity_fields": ["course", "kind", "text"]}


class Syllabus(DataPoint):
    """A course syllabus: its topic list, exam windows and stated guidelines."""

    course: str
    topics: list[Topic] = Field(default_factory=list)
    exams: list[ExamWindow] = Field(default_factory=list)
    guidelines: list[StatedGuideline] = Field(default_factory=list)
    metadata: dict = {"index_fields": [], "identity_fields": ["course"]}


class StudentProfile(DataPoint):
    """contracts/student.md §1 — stored in the student's private dataset `student-<n>`."""

    student_id: str
    display_name: str
    course: str
    exam_id: str
    exam_date: str                       # ISO date
    weekly_hours: float = Field(ge=0.5, le=60)
    style_order: Literal["examples_first", "proofs_first"]
    length: Literal["short", "detailed"]
    modality: Literal["visual", "verbal"]
    pace: Literal["slow", "normal", "fast"]
    metadata: dict = {"index_fields": [], "identity_fields": ["student_id"]}


MODELS: tuple[type[DataPoint], ...] = (Topic, ExamProblem, HomeworkProblem, Lecture, ExamWindow, StatedGuideline,
                                       Syllabus, StudentProfile)


def topics_from_rows(rows: list[Mapping[str, object]]) -> list[Topic]:
    """Topic DataPoints from `topics` rows (course, topic_id, topic) — the fixed list, created in code."""
    return [Topic(course=str(r["course"]), name=str(r["topic"]), topic_id=str(r["topic_id"])) for r in rows]
