"""python -m oracle.structure --course <course> --check [--courses-dir DIR]

Load and validate one course-structure folder, data/courses/<course>/ (contract: contracts/course-structure.md).
Person B writes the folder; Person A's loader reads it through load_course() and gets:

  * ledger-shaped frames, typed from contracts/ledger-schema.sql: courses, topics, lectures (sessions.csv without
    the is_review rows), exams (without role = excluded) and guidelines;
  * exam_sources: the exams whose PDFs A may fetch for items (roles target_full, target_history, x1_input; never
    a sealed exam), with their problems/solutions URLs;
  * homework_sets + homework_due_session(term, set): the due session that becomes homework_items.session
    (None for a set without one, e.g. 6.641 'opt', which is not loaded).

A extracts ONLY problem numbers, points, sub-parts, problem text and homework text from the PDFs and joins them on
exam_id / (term, set). Everything about time, coverage, cumulative, sealing and topics comes from this folder.

--check prints ONE JSON object {ok, course, folder, counts, sealed_exams, non_ocw_urls, warnings, problems} and
exits 0 when the folder passes, 3 (EXIT_VALIDATION) when any check fails (files and headers, ids, term_seq,
sessions and the topic partition, D2 exam sessions, exam_type position, roles and sealing, skip list, sources,
no problem content), 1 on a usage error or an unexpected fault.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd
import pyarrow as pa

from oracle import config, ordering
from oracle.backend import to_contract_table

COURSES_DIR = config.DATA_DIR / "courses"

HEADERS: dict[str, tuple[str, ...]] = {
    "topics.csv": ("course", "topic_id", "topic", "first_session", "last_session", "lecture_ns",
                   "taught_in_published_term", "grounding"),
    "sessions.csv": ("course", "term", "term_seq", "session", "date", "lecture_n", "title", "topic_id", "is_review"),
    "exams.csv": ("course", "exam_id", "exam_type", "original_name", "term", "term_seq", "session", "session_source",
                  "date", "total_points", "coverage_from_session", "coverage_to_session", "cumulative",
                  "cumulative_basis", "sealed", "problems_url", "solutions_url", "url_verified", "role"),
    "homework_sets.csv": ("course", "term", "term_seq", "set", "issued_session", "due_session", "is_practice",
                          "problems_url", "solutions_url", "url_verified"),
    "guidelines.csv": ("course", "guideline_id", "kind", "applies_to_exam_type", "applies_to_term", "from_session",
                       "to_session", "share", "source_term", "source_term_seq", "source_session",
                       "source_session_basis", "text", "source_url"),
}
FILES = ("course.json", *HEADERS, "skip_list.txt")
COURSE_JSON_REQUIRED = frozenset({"course", "title", "ocw_url", "published_term", "published_term_seq",
                                  "assumptions", "sources_note", "license"})
COURSE_JSON_OPTIONAL = frozenset({"instructor", "calendar_has_real_dates", "packet_sections"})
# Keys that would carry exam content (or the research fields that describe it) - never allowed anywhere in course.json.
FORBIDDEN_JSON_KEYS = frozenset({"uncertainties", "checks", "problems", "problem_text", "questions", "items",
                                 "exam_items", "answers", "answer_key", "solution_text", "points", "sub_parts",
                                 "subparts", "topics_tagged", "tags"})
# Mathematical markup never appears in structure text; its presence means problem content was copied in.
MARKUP_RE = re.compile(r"\\[A-Za-z]{2,}|\\[()\[\]]|\$[^$\s][^$]*\$")

EXAM_TYPES = ("quiz1", "quiz2", "quiz3", "midterm", "final")
POSITION = {"quiz1": 1, "quiz2": 2, "quiz3": 3, "final": 9}          # A1; 'midterm' has no slot of its own
SYNTHETIC_SESSION = {"quiz1": 1000, "quiz2": 2000, "quiz3": 3000, "final": 9000}
SESSION_SOURCES = ("calendar_own_row", "calendar_shared_row", "synthetic")
ROLES = ("target_full", "target_history", "x1_input", "sealed_reveal", "excluded")
ITEM_ROLES = ("target_full", "target_history", "x1_input")           # exams whose items A may load
GUIDELINE_KINDS = ("cumulative", "emphasis_window", "coverage", "format", "homework_analogous")
TOPIC_ID_RE = re.compile(r"T\d{2}")
SET_RE = re.compile(r"[A-Za-z0-9]+")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
OFF_LIST = "T00"
# Documented, deliberate exceptions to the ocw.mit.edu-only sources rule (contracts/course-structure.md
# "Sources"). Listing a URL here does NOT let any fetcher use it: oracle.config.validate_ocw_url still refuses
# it; A must extend the fetch allowlist deliberately (a decisions-log line) before downloading from it.
SOURCE_EXCEPTIONS: dict[str, tuple[str, ...]] = {
    "18.06": ("https://web.mit.edu/18.06/www/",),
}


class StructureError(ValueError):
    """The course folder fails validation; .problems lists every failure."""

    def __init__(self, course: str, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__(f"course structure {course} is invalid ({len(problems)} problem(s)): "
                         + "; ".join(self.problems[:5]))


@dataclass
class CourseStructure:
    course: str
    folder: Path
    meta: dict[str, Any] = field(default_factory=dict)
    rows: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    skip_list: list[str] = field(default_factory=list)
    tables: dict[str, pa.Table] = field(default_factory=dict)     # ledger tables (contract types)
    exam_sources: pd.DataFrame = field(default_factory=pd.DataFrame)
    homework_sets: pd.DataFrame = field(default_factory=pd.DataFrame)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    non_ocw_urls: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def frames(self) -> dict[str, pd.DataFrame]:
        """Ledger-shaped DataFrames (nullable Arrow dtypes, contract column order)."""
        return {name: tbl.to_pandas(types_mapper=pd.ArrowDtype) for name, tbl in self.tables.items()}

    def homework_due_session(self, term: str, set_: str | int) -> int | None:
        """Due session of homework set (term, set); None when the set has none (never loaded). KeyError if absent."""
        for r in self.rows.get("homework_sets.csv", []):
            if r["term"] == term and r["set"] == str(set_):
                return _int(r["due_session"])
        raise KeyError(f"{self.course}: no homework set ({term}, {set_})")

    def summary(self) -> dict[str, Any]:
        exams = self.rows.get("exams.csv", [])
        return {
            "ok": self.ok, "course": self.course, "folder": str(self.folder),
            "counts": {**{name: tbl.num_rows for name, tbl in self.tables.items()},
                       "exams_excluded": sum(1 for r in exams if r.get("role") == "excluded"),
                       "exam_sources": len(self.exam_sources), "homework_sets": len(self.homework_sets),
                       "skip_list": len(self.skip_list)},
            "sealed_exams": [r["exam_id"] for r in exams if r.get("sealed") == "true"],
            "non_ocw_urls": len(self.non_ocw_urls),
            "warnings": self.warnings, "problems": self.problems,
        }


# ------------------------------------------------------------------ cell parsing
def _int(cell: str | None) -> int | None:
    return None if cell is None or cell.strip() == "" else int(cell)


def _float(cell: str | None) -> float | None:
    return None if cell is None or cell.strip() == "" else float(cell)


def _bool(cell: str | None) -> bool | None:
    if cell is None or cell.strip() == "":
        return None
    if cell not in ("true", "false"):
        raise ValueError(f"boolean must be 'true' or 'false', got {cell!r}")
    return cell == "true"


def _none(cell: str | None) -> str | None:
    return None if cell is None or cell.strip() == "" else cell


class _Checker:
    def __init__(self, s: CourseStructure):
        self.s = s

    def fail(self, where: str, msg: str) -> None:
        self.s.problems.append(f"{where}: {msg}")

    def cell(self, where: str, fn, value: str | None, what: str):
        try:
            return fn(value)
        except (TypeError, ValueError) as exc:
            self.fail(where, f"{what}: {exc}")
            return None

    def term(self, where: str, term: str, term_seq: str, what: str = "term") -> int | None:
        try:
            seq = ordering.term_to_seq(term)
        except ValueError as exc:
            self.fail(where, f"{what}: {exc}")
            return None
        if term_seq != str(seq):
            self.fail(where, f"{what}_seq {term_seq!r} != {seq} (term_seq = year*10 + 1 spring | 2 summer | 3 fall)")
        return seq


# ------------------------------------------------------------------ reading
def _read_csv(path: Path, name: str, ck: _Checker) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        body = [row for row in reader if any(c.strip() for c in row)]
    want = HEADERS[name]
    if tuple(header) != want:
        extra = [c for c in header if c not in want]
        missing = [c for c in want if c not in header]
        ck.fail(name, f"header must be exactly {','.join(want)} (extra={extra}, missing={missing}; extra "
                      f"columns are refused because structure files carry no problem content)")
        return []
    rows = []
    for n, row in enumerate(body, 2):
        if len(row) != len(want):
            ck.fail(f"{name}:{n}", f"{len(row)} cells, header has {len(want)}")
            continue
        rows.append(dict(zip(want, row)))
    return rows


def _walk_json(value: Any, path: str) -> Iterable[tuple[str, str | None, Any]]:
    """(path, key, value) for every node of a JSON tree."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield f"{path}.{k}", str(k), v
            yield from _walk_json(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield f"{path}[{i}]", None, v
            yield from _walk_json(v, f"{path}[{i}]")


# ------------------------------------------------------------------ checks
def _check_url(s: CourseStructure, ck: _Checker, where: str, url: str, *, required: bool = False) -> None:
    if not url.strip():
        if required:
            ck.fail(where, "URL is required")
        return
    if url.rstrip("/") in {u.rstrip("/") for u in s.skip_list}:
        ck.fail(where, f"{url} is on skip_list.txt (a sealed URL may appear nowhere else)")
    if config.is_ocw_url(url):
        return
    if any(url.startswith(prefix) for prefix in SOURCE_EXCEPTIONS.get(s.course, ())):
        s.non_ocw_urls.append(url)
        return
    ck.fail(where, f"{url} is not https://ocw.mit.edu/ and not a documented source exception for {s.course}")


def _check_course_json(s: CourseStructure, ck: _Checker) -> tuple[str | None, int | None]:
    meta = s.meta
    keys = set(meta)
    if COURSE_JSON_REQUIRED - keys:
        ck.fail("course.json", f"missing keys {sorted(COURSE_JSON_REQUIRED - keys)}")
    if keys - COURSE_JSON_REQUIRED - COURSE_JSON_OPTIONAL:
        ck.fail("course.json", f"unknown keys {sorted(keys - COURSE_JSON_REQUIRED - COURSE_JSON_OPTIONAL)}")
    for path, key, value in _walk_json(meta, "course.json"):
        if key is not None and key.lower() in FORBIDDEN_JSON_KEYS:
            ck.fail(path, f"key {key!r} is forbidden (no problem content in structure files)")
        if isinstance(value, str) and MARKUP_RE.search(value):
            ck.fail(path, "contains mathematical markup (problem content?)")
    if meta.get("course") != s.course:
        ck.fail("course.json", f"course {meta.get('course')!r} != folder name {s.course!r}")
    pub_term = meta.get("published_term")
    pub_seq = ck.term("course.json", str(pub_term), str(meta.get("published_term_seq")), "published_term")
    _check_url(s, ck, "course.json ocw_url", str(meta.get("ocw_url", "")), required=True)
    packet = meta.get("packet_sections")
    if isinstance(packet, dict):
        _check_url(s, ck, "course.json packet_sections.url", str(packet.get("url", "")), required=True)
    ids = [a.get("id") for a in meta.get("assumptions", []) if isinstance(a, dict)]
    if len(ids) != len(meta.get("assumptions", [])) or not all(isinstance(i, str) and i for i in ids):
        ck.fail("course.json", "every assumption must be an object with a non-empty 'id' and 'text'")
    if len(set(ids)) != len(ids):
        ck.fail("course.json", f"duplicate assumption ids {sorted({i for i in ids if ids.count(i) > 1})}")
    return (str(pub_term) if pub_seq is not None else None), pub_seq


def _check_common(s: CourseStructure, ck: _Checker) -> None:
    for name in HEADERS:
        for n, r in enumerate(s.rows.get(name, []), 2):
            if r["course"] != s.course:
                ck.fail(f"{name}:{n}", f"course {r['course']!r} != {s.course!r}")
            for col, value in r.items():
                if MARKUP_RE.search(value):
                    ck.fail(f"{name}:{n} {col}", "contains mathematical markup (problem content?)")


def _check_topics_sessions(s: CourseStructure, ck: _Checker, pub_term: str | None) -> set[int]:
    topics = s.rows.get("topics.csv", [])
    sessions = s.rows.get("sessions.csv", [])
    ids = [t["topic_id"] for t in topics]
    for n, t in enumerate(topics, 2):
        if not TOPIC_ID_RE.fullmatch(t["topic_id"]):
            ck.fail(f"topics.csv:{n}", f"topic_id {t['topic_id']!r} must be T + two digits")
        if not t["topic"].strip():
            ck.fail(f"topics.csv:{n}", "topic name is empty")
    if len(set(ids)) != len(ids):
        ck.fail("topics.csv", f"duplicate topic_id {sorted({i for i in ids if ids.count(i) > 1})}")
    if OFF_LIST not in ids:
        ck.fail("topics.csv", "T00 'Off-list' row is missing (D6)")

    by_topic: dict[str, list[tuple[int, int | None]]] = {}
    seen: set[int] = set()
    lecture_sessions: set[int] = set()
    for n, r in enumerate(sessions, 2):
        where = f"sessions.csv:{n}"
        if pub_term is not None and r["term"] != pub_term:
            ck.fail(where, f"term {r['term']!r}: sessions are published-term ({pub_term}) rows only")
        ck.term(where, r["term"], r["term_seq"])
        session = ck.cell(where, _int, r["session"], "session")
        review = ck.cell(where, _bool, r["is_review"], "is_review")
        lecture_n = ck.cell(where, _int, r["lecture_n"], "lecture_n")
        if session is None or review is None:
            ck.fail(where, "session and is_review are required")
            continue
        if session < 1 or session in seen:
            ck.fail(where, f"session {session} must be >= 1 and unique")
        seen.add(session)
        if r["date"] and not DATE_RE.fullmatch(r["date"]):
            ck.fail(where, f"date {r['date']!r} must be YYYY-MM-DD or blank")
        if review:
            if r["topic_id"] or r["lecture_n"]:
                ck.fail(where, "an is_review row carries no topic_id and no lecture_n")
            continue
        lecture_sessions.add(session)
        if lecture_n is None or lecture_n < 1:
            ck.fail(where, "a lecture row needs lecture_n >= 1")
        if r["topic_id"] not in ids or r["topic_id"] == OFF_LIST:
            ck.fail(where, f"topic_id {r['topic_id']!r} must be one listed topic other than T00 (partition)")
            continue
        by_topic.setdefault(r["topic_id"], []).append((session, lecture_n))

    lecture_owner: dict[int, str] = {}
    for n, t in enumerate(topics, 2):
        where = f"topics.csv:{n} ({t['topic_id']})"
        taught = ck.cell(where, _bool, t["taught_in_published_term"], "taught_in_published_term")
        first = ck.cell(where, _int, t["first_session"], "first_session")
        last = ck.cell(where, _int, t["last_session"], "last_session")
        lecture_ns = set()
        for part in filter(None, t["lecture_ns"].split(";")):
            value = ck.cell(where, _int, part, "lecture_ns")
            if value is not None:
                lecture_ns.add(value)
                if value in lecture_owner:
                    ck.fail(where, f"lecture {value} also belongs to {lecture_owner[value]} (partition)")
                lecture_owner[value] = t["topic_id"]
        members = by_topic.get(t["topic_id"], [])
        if t["topic_id"] == OFF_LIST:
            if taught or first is not None or last is not None or lecture_ns or members:
                ck.fail(where, "T00 has no sessions, no bounds, no lecture_ns and taught_in_published_term=false")
            continue
        if taught is None:
            continue
        if taught:
            if not members:
                ck.fail(where, "taught topic has no sessions.csv row (partition)")
                continue
            got = sorted(m[0] for m in members)
            if (first, last) != (got[0], got[-1]):
                ck.fail(where, f"first/last_session {first}/{last} != its sessions' bounds {got[0]}/{got[-1]}")
            lectures = {m[1] for m in members if m[1] is not None}
            if lecture_ns != lectures:
                ck.fail(where, f"lecture_ns {sorted(lecture_ns)} != lecture_n of its sessions {sorted(lectures)}")
        elif members or first is not None or last is not None:
            ck.fail(where, "an untaught topic has no sessions and blank bounds")
    return lecture_sessions


def _check_exams(s: CourseStructure, ck: _Checker, pub_term: str | None, lecture_sessions: set[int]) -> None:
    exams = s.rows.get("exams.csv", [])
    all_sessions = {_int(r["session"]) for r in s.rows.get("sessions.csv", []) if r["session"].strip().isdigit()}
    ids = [r["exam_id"] for r in exams]
    if len(set(ids)) != len(ids):
        ck.fail("exams.csv", f"duplicate exam_id {sorted({i for i in ids if ids.count(i) > 1})}")
    by_term: dict[str, list[tuple[int, str, str]]] = {}
    for n, r in enumerate(exams, 2):
        where = f"exams.csv:{n} ({r['exam_id']})"
        if not config.is_valid_id(r["exam_id"]):
            ck.fail(where, "exam_id has characters outside [A-Za-z0-9._-]")
        if r["exam_type"] not in EXAM_TYPES:
            ck.fail(where, f"exam_type {r['exam_type']!r} not in {EXAM_TYPES}")
        if r["exam_id"] != f"{s.course}-{r['exam_type']}-{r['term']}":
            ck.fail(where, f"exam_id must be <course>-<exam_type>-<term> = {s.course}-{r['exam_type']}-{r['term']}")
        ck.term(where, r["term"], r["term_seq"])
        session = ck.cell(where, _int, r["session"], "session")
        sealed = ck.cell(where, _bool, r["sealed"], "sealed")
        ck.cell(where, _bool, r["cumulative"], "cumulative")
        ck.cell(where, _bool, r["url_verified"], "url_verified")
        ck.cell(where, _float, r["total_points"], "total_points")
        cov = (ck.cell(where, _int, r["coverage_from_session"], "coverage_from_session"),
               ck.cell(where, _int, r["coverage_to_session"], "coverage_to_session"))
        if (cov[0] is None) != (cov[1] is None) or (cov[0] is not None and cov[0] > cov[1]):
            ck.fail(where, f"coverage window {cov} must be both blank or from <= to")
        if r["date"] and not DATE_RE.fullmatch(r["date"]):
            ck.fail(where, f"date {r['date']!r} must be YYYY-MM-DD or blank")
        role, source = r["role"], r["session_source"]
        if role not in ROLES:
            ck.fail(where, f"role {role!r} not in {ROLES}")
        if source not in SESSION_SOURCES:
            ck.fail(where, f"session_source {source!r} not in {SESSION_SOURCES}")
        if session is None or sealed is None:
            ck.fail(where, "session and sealed are required")
            continue
        in_pub = pub_term is not None and r["term"] == pub_term
        # D2: published-term sessions come from the calendar; other terms carry synthetic ordinals.
        if source == "synthetic":
            if in_pub:
                ck.fail(where, "a published-term exam has a calendar session, not a synthetic one (D2)")
            elif r["exam_type"] in SYNTHETIC_SESSION and session != SYNTHETIC_SESSION[r["exam_type"]]:
                ck.fail(where, f"synthetic session must be {SYNTHETIC_SESSION[r['exam_type']]} for "
                               f"{r['exam_type']} (D2)")
        elif pub_term is not None and not in_pub:
            ck.fail(where, f"{source}: only published-term ({pub_term}) exams have calendar sessions (D2)")
        elif source == "calendar_own_row" and session in all_sessions:
            ck.fail(where, f"calendar_own_row: sessions.csv must have no row at the exam's session {session}")
        elif source == "calendar_shared_row" and (session - 1) not in lecture_sessions:
            ck.fail(where, f"calendar_shared_row: session = last delivered session + 1, but there is no lecture "
                           f"row at session {session - 1} (D2)")
        # roles and sealing
        if (role == "sealed_reveal") != bool(sealed):
            ck.fail(where, "sealed = true exactly when role = sealed_reveal")
        if role == "sealed_reveal":
            if not in_pub:
                ck.fail(where, "the sealed reveal must be in the published term")
            if r["problems_url"] or r["solutions_url"] or r["total_points"] or r["url_verified"] != "false":
                ck.fail(where, "a sealed exam carries no problems_url, solutions_url or total_points, and "
                               "url_verified = false (nothing to fetch)")
        if role == "target_full" and not in_pub:
            ck.fail(where, "target_full is a published-term exam (feature_set full, D3)")
        if role == "target_history" and in_pub:
            ck.fail(where, "target_history is an earlier-term exam (feature_set exam_history, D3)")
        if role in ITEM_ROLES and not r["problems_url"]:
            ck.fail(where, f"role {role} needs a problems_url")
        for col in ("problems_url", "solutions_url"):
            _check_url(s, ck, f"{where} {col}", r[col])
        if role != "excluded":
            by_term.setdefault(r["term"], []).append((session, r["exam_type"], r["exam_id"]))
    # A1: exam_type by position within a term (one exam per slot; slots in session order).
    for term, rows in by_term.items():
        types = [t for _, t, _ in rows]
        if len(set(types)) != len(types):
            ck.fail("exams.csv", f"term {term}: two exams share an exam_type slot {types} (A1)")
        slotted = sorted((sess, POSITION[t], eid) for sess, t, eid in rows if t in POSITION)
        if [p for _, p, _ in slotted] != sorted(p for _, p, _ in slotted):
            ck.fail("exams.csv", f"term {term}: exam_type order {[e for *_, e in slotted]} does not follow session "
                                 f"order (A1: quiz1 < quiz2 < quiz3 < final)")
    if any(r["sealed"] == "true" for r in exams) and not s.skip_list:
        ck.fail("skip_list.txt", "a sealed exam exists, so skip_list.txt must list its URLs")


def _check_homework(s: CourseStructure, ck: _Checker, pub_term: str | None) -> None:
    keys = []
    for n, r in enumerate(s.rows.get("homework_sets.csv", []), 2):
        where = f"homework_sets.csv:{n} (set {r['set']})"
        if pub_term is not None and r["term"] != pub_term:
            ck.fail(where, f"term {r['term']!r}: homework is published-term ({pub_term}) material only")
        ck.term(where, r["term"], r["term_seq"])
        if not SET_RE.fullmatch(r["set"]):
            ck.fail(where, "set must match [A-Za-z0-9]+ (it becomes part of hw_id)")
        keys.append((r["term"], r["set"]))
        issued = ck.cell(where, _int, r["issued_session"], "issued_session")
        due = ck.cell(where, _int, r["due_session"], "due_session")
        practice = ck.cell(where, _bool, r["is_practice"], "is_practice")
        ck.cell(where, _bool, r["url_verified"], "url_verified")
        if due is None and practice is not True:
            ck.fail(where, "a set without due_session must be is_practice = true (it is not loaded)")
        if issued is not None and due is not None and issued > due:
            ck.fail(where, f"issued_session {issued} > due_session {due}")
        for col in ("problems_url", "solutions_url"):
            _check_url(s, ck, f"{where} {col}", r[col])
    if len(set(keys)) != len(keys):
        ck.fail("homework_sets.csv", "duplicate (term, set)")


def _check_guidelines(s: CourseStructure, ck: _Checker) -> None:
    ids = []
    for n, r in enumerate(s.rows.get("guidelines.csv", []), 2):
        where = f"guidelines.csv:{n} ({r['guideline_id']})"
        ids.append(r["guideline_id"])
        if not re.fullmatch(re.escape(s.course) + r"-G\d{2}", r["guideline_id"]):
            ck.fail(where, f"guideline_id must be {s.course}-G<nn> (the ledger key is guideline_id alone)")
        if r["kind"] not in GUIDELINE_KINDS:
            ck.fail(where, f"kind {r['kind']!r} not in {GUIDELINE_KINDS}")
        if r["applies_to_exam_type"] not in EXAM_TYPES:
            ck.fail(where, f"applies_to_exam_type {r['applies_to_exam_type']!r} not in {EXAM_TYPES}")
        try:
            ordering.term_to_seq(r["applies_to_term"])
        except ValueError as exc:
            ck.fail(where, f"applies_to_term: {exc}")
        ck.term(where, r["source_term"], r["source_term_seq"], "source_term")
        lo = ck.cell(where, _int, r["from_session"], "from_session")
        hi = ck.cell(where, _int, r["to_session"], "to_session")
        share = ck.cell(where, _float, r["share"], "share")
        if ck.cell(where, _int, r["source_session"], "source_session") is None:
            ck.fail(where, "source_session is required (visibility follows the ordering rule)")
        if lo is not None and hi is not None and lo > hi:
            ck.fail(where, f"from_session {lo} > to_session {hi}")
        if share is not None and not 0.0 <= share <= 1.0:
            ck.fail(where, f"share {share} outside [0, 1]")
        if not r["text"].strip() or not r["source_session_basis"].strip():
            ck.fail(where, "text (verbatim) and source_session_basis are required")
        _check_url(s, ck, f"{where} source_url", r["source_url"], required=True)
    if len(set(ids)) != len(ids):
        ck.fail("guidelines.csv", f"duplicate guideline_id {sorted({i for i in ids if ids.count(i) > 1})}")


# ------------------------------------------------------------------ ledger frames
def _build_tables(s: CourseStructure) -> None:
    m, c = s.meta, s.course
    rows = s.rows
    tables: dict[str, list[dict[str, Any]]] = {
        "courses": [{"course": c, "title": m.get("title"), "ocw_url": m.get("ocw_url"),
                     "published_term": m.get("published_term"), "published_term_seq": m.get("published_term_seq")}],
        "topics": [{"course": c, "topic_id": t["topic_id"], "topic": t["topic"],
                    "first_lecture": _int(t["first_session"]), "last_lecture": _int(t["last_session"])}
                   for t in rows["topics.csv"]],
        "lectures": [{"course": c, "term": r["term"], "term_seq": int(r["term_seq"]), "session": int(r["session"]),
                      "date": _none(r["date"]), "lecture_n": _int(r["lecture_n"]), "title": r["title"],
                      "topic_id": r["topic_id"]}
                     for r in rows["sessions.csv"] if r["is_review"] == "false"],
        "exams": [{"course": c, "exam_id": r["exam_id"], "exam_type": r["exam_type"], "term": r["term"],
                   "term_seq": int(r["term_seq"]), "session": _int(r["session"]), "date": _none(r["date"]),
                   "total_points": _float(r["total_points"]),
                   "coverage_from_session": _int(r["coverage_from_session"]),
                   "coverage_to_session": _int(r["coverage_to_session"]), "cumulative": _bool(r["cumulative"]),
                   "sealed": r["sealed"] == "true"}
                  for r in rows["exams.csv"] if r["role"] != "excluded"],
        "guidelines": [{"course": c, "guideline_id": r["guideline_id"], "kind": r["kind"],
                        "applies_to_exam_type": r["applies_to_exam_type"], "from_session": _int(r["from_session"]),
                        "to_session": _int(r["to_session"]), "share": _float(r["share"]),
                        "source_term": r["source_term"], "source_term_seq": int(r["source_term_seq"]),
                        "source_session": int(r["source_session"]), "text": r["text"],
                        "source_url": r["source_url"]}
                       for r in rows["guidelines.csv"]],
    }
    s.tables = {name: to_contract_table(name, recs) for name, recs in tables.items()}
    s.exam_sources = pd.DataFrame(
        [{"exam_id": r["exam_id"], "role": r["role"], "term": r["term"], "exam_type": r["exam_type"],
          "problems_url": r["problems_url"], "solutions_url": _none(r["solutions_url"]),
          "same_file": r["problems_url"] == r["solutions_url"]}
         for r in rows["exams.csv"] if r["role"] in ITEM_ROLES and r["sealed"] == "false"],
        columns=["exam_id", "role", "term", "exam_type", "problems_url", "solutions_url", "same_file"])
    s.homework_sets = pd.DataFrame(
        [{"term": r["term"], "term_seq": int(r["term_seq"]), "set": r["set"], "due_session": _int(r["due_session"]),
          "is_practice": r["is_practice"] == "true", "problems_url": r["problems_url"],
          "solutions_url": _none(r["solutions_url"]), "loadable": _int(r["due_session"]) is not None}
         for r in rows["homework_sets.csv"]],
        columns=["term", "term_seq", "set", "due_session", "is_practice", "problems_url", "solutions_url",
                 "loadable"])


# ------------------------------------------------------------------ entry points
def check_course(course: str, courses_dir: str | Path | None = None) -> CourseStructure:
    """Read and validate data/courses/<course>/; the returned structure lists every problem (never raises for a
    bad folder). Tables are built only when the folder passes."""
    base = Path(courses_dir) if courses_dir is not None else COURSES_DIR
    s = CourseStructure(course=course, folder=base / course)
    ck = _Checker(s)
    if not config.is_valid_id(course):
        ck.fail("course", f"invalid course code {course!r}")
        return s
    if not s.folder.is_dir():
        ck.fail("folder", f"{s.folder} does not exist")
        return s
    for name in FILES:
        if not (s.folder / name).is_file():
            ck.fail(name, "file is missing")
    if s.problems:
        return s
    try:
        s.meta = json.loads((s.folder / "course.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        ck.fail("course.json", f"not valid JSON: {exc}")
        return s
    if not isinstance(s.meta, dict):
        ck.fail("course.json", "must be a JSON object")
        return s
    s.skip_list = [line.strip() for line in (s.folder / "skip_list.txt").read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.lstrip().startswith("#")]
    for url in s.skip_list:
        if not url.startswith("https://"):
            ck.fail("skip_list.txt", f"{url!r} is not an https URL")
    for name in HEADERS:
        s.rows[name] = _read_csv(s.folder / name, name, ck)
    pub_term, _ = _check_course_json(s, ck)
    _check_common(s, ck)
    lecture_sessions = _check_topics_sessions(s, ck, pub_term)
    _check_exams(s, ck, pub_term, lecture_sessions)
    _check_homework(s, ck, pub_term)
    _check_guidelines(s, ck)
    if not s.problems:
        try:
            _build_tables(s)
        except ValueError as exc:
            ck.fail("ledger", str(exc))
    return s


def load_course(course: str, courses_dir: str | Path | None = None) -> CourseStructure:
    """check_course, raising StructureError when the folder fails (A's loader entry point)."""
    s = check_course(course, courses_dir)
    if s.problems:
        raise StructureError(course, s.problems)
    return s


def main(argv: list[str] | None = None) -> int:
    parser = config.ScriptParser(prog="python -m oracle.structure",
                                 description="Validate a course-structure folder (contracts/course-structure.md).")
    parser.add_argument("--course", required=True, help="course code, e.g. 6.003 (folder data/courses/<course>)")
    parser.add_argument("--check", action="store_true", help="validate and print the summary (the only mode)")
    parser.add_argument("--courses-dir", help="folder holding the course folders [data/courses]")
    args = parser.parse_args(argv)
    try:
        s = check_course(args.course, args.courses_dir)
    except Exception as exc:  # unreadable folder: a hard fault, one JSON object
        config.emit({"ok": False, "course": args.course, "error": f"{type(exc).__name__}: {exc}"})
        return config.EXIT_HARD
    config.emit(s.summary())
    return config.EXIT_OK if s.ok else config.EXIT_VALIDATION


if __name__ == "__main__":
    sys.exit(main())
