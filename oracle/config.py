"""Shared configuration: environment, OCW allowlist, HTTP, paths, skip list, dates."""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import sys
from importlib import resources
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

OCW_HOST = "ocw.mit.edu"
USER_AGENT = "exam-oracle-loader/0.1 (+https://github.com/mrithikas11-tech/Exam-Oracle)"
HTTP_TIMEOUT = (10, 90)
MAX_REDIRECTS = 5
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024
COURSE_RE = re.compile(r"^\d{1,2}\.\d{2,3}[A-Za-z]?$")
TERM_RE = re.compile(r"^(\d{4})([FS])$")

EXIT_HARD = 1
EXIT_SEALED = 2
EXIT_INVALID = 3


class OracleError(Exception):
    """A hard fault: message goes to stderr, process exits with `code`."""

    def __init__(self, message: str, code: int = EXIT_HARD):
        super().__init__(message)
        self.code = code


def emit(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True))
    sys.stdout.flush()


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------- paths ----------

def data_dir() -> Path:
    """Root for raw downloads and work files. Never inside the sealed folder."""
    root = Path(os.environ.get("ORACLE_DATA_DIR") or "~/exam-oracle-data").expanduser().resolve()
    sealed = os.environ.get("SEALED_DIR")
    if sealed:
        sealed_path = Path(sealed).expanduser().resolve()
        if root == sealed_path or sealed_path in root.parents or root in sealed_path.parents:
            raise OracleError("ORACLE_DATA_DIR overlaps SEALED_DIR; refusing to run", EXIT_SEALED)
    root.mkdir(parents=True, exist_ok=True)
    return root


def check_course(course: str) -> str:
    if not COURSE_RE.match(course or ""):
        raise OracleError(f"bad course code {course!r} (expected e.g. 6.003)")
    return course


def course_path(kind: str, course: str, *parts: str) -> Path:
    """data_dir/<kind>/<course>/..., created on demand. kind is raw | work."""
    if kind not in ("raw", "work"):
        raise OracleError(f"unknown data kind {kind!r}")
    path = data_dir() / kind / check_course(course)
    for part in parts:
        if "/" in part or part in ("", ".", ".."):
            raise OracleError(f"unsafe path component {part!r}")
        path = path / part
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=1))
    tmp.replace(path)


# ---------- URLs and skip list ----------

def check_ocw_url(url: str) -> str:
    """Allow only https://ocw.mit.edu/... ; returns the URL without fragment."""
    p = urlparse(url)
    if p.scheme != "https" or p.hostname != OCW_HOST or p.username or p.password or p.port not in (None, 443):
        raise OracleError(f"refusing non-OCW URL: {url}")
    return urlunparse(p._replace(fragment=""))


def normalize_url(url: str) -> str:
    p = urlparse(url.strip())
    return f"{p.scheme.lower()}://{(p.hostname or '').lower()}{p.path.rstrip('/')}"


class SkipList:
    """Sealed exams: never downloaded, read, or loaded. Lines are URLs or SHA-256 hashes."""

    def __init__(self, urls: set[str], hashes: set[str], source: str):
        self.urls, self.hashes, self.source = urls, hashes, source

    @classmethod
    def load(cls, path: str | None) -> "SkipList":
        if path in (None, "", "builtin"):
            text = resources.files("oracle").joinpath("skip_list.txt").read_text()
            source = "builtin"
        else:
            text = Path(path).expanduser().read_text()
            source = str(path)
        urls, hashes = set(), set()
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if re.fullmatch(r"[0-9a-fA-F]{64}", line):
                hashes.add(line.lower())
            elif line.startswith("https://"):
                urls.add(normalize_url(line))
            else:
                raise OracleError(f"unrecognised skip-list line: {line!r}")
        if not urls and not hashes:
            raise OracleError("skip list is empty; refusing to run without sealing")
        return cls(urls, hashes, source)

    def url_sealed(self, url: str) -> bool:
        return normalize_url(url) in self.urls

    def check_url(self, url: str) -> None:
        if self.url_sealed(url):
            raise OracleError(f"SEALED: skip-listed URL encountered: {url}", EXIT_SEALED)

    def check_hash(self, sha256: str) -> None:
        if sha256.lower() in self.hashes:
            raise OracleError(f"SEALED: downloaded bytes match a skip-listed hash {sha256}", EXIT_SEALED)


# ---------- HTTP ----------

_session = None


def _get_session():
    global _session
    if _session is None:
        import requests

        _session = requests.Session()
        _session.headers["User-Agent"] = USER_AGENT
    return _session


def ocw_get(url: str, skip: SkipList, *, stream: bool = False):
    """GET an OCW URL, re-checking the allowlist and the skip list on every redirect hop."""
    session = _get_session()
    for _ in range(MAX_REDIRECTS + 1):
        url = check_ocw_url(url)
        skip.check_url(url)
        resp = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=False, stream=stream)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                raise OracleError(f"redirect without Location from {url}")
            url = urljoin(url, location)
            continue
        if resp.status_code != 200:
            resp.close()
            raise OracleError(f"HTTP {resp.status_code} for {url}")
        return resp
    raise OracleError(f"too many redirects starting at {url}")


# ---------- terms and dates ----------

def check_term(term: str) -> str:
    if not TERM_RE.match(term or ""):
        raise OracleError(f"bad term {term!r} (expected e.g. 2011F)")
    return term


def term_start(term: str) -> dt.date:
    year, season = TERM_RE.match(check_term(term)).groups()
    return dt.date(int(year), 9, 7) if season == "F" else dt.date(int(year), 2, 3)


# Assumed weeks after term start when a date is not in exam_dates.csv. Logged as "assumed".
EXAM_WEEK = {"quiz1": 5, "midterm": 7, "quiz2": 9, "quiz3": 12, "final": 15}


def _date_overrides() -> dict[str, tuple[str, str]]:
    text = resources.files("oracle").joinpath("exam_dates.csv").read_text()
    rows = csv.DictReader(line for line in text.splitlines() if not line.startswith("#"))
    return {r["exam_id"]: (r["date"], r.get("source") or "table") for r in rows if r.get("exam_id")}


def exam_date(exam_id: str, term: str, exam_type: str) -> tuple[str, str]:
    override = _date_overrides().get(exam_id)
    if override:
        return override
    weeks = EXAM_WEEK.get(exam_type, 15)
    return (term_start(term) + dt.timedelta(weeks=weeks)).isoformat(), "assumed"


def homework_date(term: str, n: int | None) -> tuple[str, str]:
    return (term_start(term) + dt.timedelta(days=7 * (n or 14))).isoformat(), "assumed"


def lecture_date(term: str, n: int | None) -> tuple[str, str]:
    k = (n or 1) - 1
    return (term_start(term) + dt.timedelta(days=(k // 2) * 7 + (k % 2) * 2)).isoformat(), "assumed"


def save_report(course: str, step: str, obj: dict) -> None:
    """Each step leaves a JSON report that `summary` rolls up."""
    write_json(course_path("work", course, "reports") / f"{step}.json", obj)


def read_report(course: str, step: str) -> dict | None:
    path = course_path("work", course, "reports") / f"{step}.json"
    return json.loads(path.read_text()) if path.exists() else None
