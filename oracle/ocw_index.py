"""List a course's OCW documents (exams, solutions, problem sets, lecture notes).

Generic and input-driven: only --course-url and --course change between courses.
Skip-listed (sealed) resources are excluded here and refused again by `download`.
Output: {"ok": true, "docs": [...], ...} on stdout; also written to work/<course>/index.json.
"""
from __future__ import annotations

import datetime as dt
import html
import re
from urllib.parse import urljoin, urlparse

from .loader_config import (OracleError, SkipList, check_course, check_ocw_url, course_path, emit,
                     exam_date, homework_date, lecture_date, log, ocw_get, write_json)

PAGES = {"exams": "exam", "assignments": "homework", "lecture-notes": "lecture"}
LINK_RE = re.compile(r'<a\b[^>]*\bhref="([^"#?]*/resources/[^"#?]+)"[^>]*>(.*?)</a>', re.S | re.I)
NON_PDF_RE = re.compile(r"\((?:zip|jpe?g|png|gif|mp3|mp4|py|m|txt|xlsx?|csv|wav)\b|(?:zip|jpe?g|png)$", re.I)
SOLUTION_RE = re.compile(r"(?<![a-z])sol(?:s|n|ns|ution|utions)?(?![a-z])|answers?", re.I)
REVIEW_RE = re.compile(r"review|study|prac", re.I)
SEASON = {"fall": "F", "spring": "S"}


def add_args(p):
    p.add_argument("--course-url", required=True, help="https://ocw.mit.edu/courses/<slug>/ (any page of it)")
    p.add_argument("--course", required=True, help="course code, e.g. 6.003")
    p.add_argument("--skip-list", default="builtin", help="skip-list file, or 'builtin'")


def course_root(url: str) -> tuple[str, str]:
    m = re.match(r"^/courses/([a-z0-9-]+)(?:/|$)", urlparse(check_ocw_url(url)).path)
    if not m:
        raise OracleError(f"not an OCW course URL: {url}")
    return f"https://ocw.mit.edu/courses/{m.group(1)}/", m.group(1)


def edition_term(slug: str) -> str:
    m = re.search(r"-(fall|spring)-(\d{4})$", slug)
    if not m:
        raise OracleError(f"cannot read the term from course slug {slug!r}")
    return f"{m.group(2)}{SEASON[m.group(1)]}"


def doc_term(text: str, edition: str) -> str:
    low = text.lower()
    m = re.search(r"(fall|spring)[\s_-]*(\d{4})", low)
    if m:
        return f"{m.group(2)}{SEASON[m.group(1)]}"
    m = re.search(r"_([fs])(\d{2})_", low + "_")
    if m:
        return f"20{m.group(2)}{m.group(1).upper()}"
    m = re.search(r"(?<!\d)(19[89]\d|20[0-3]\d)(?!\d)", low)
    if m:
        return f"{m.group(1)}{edition[-1]}"
    return edition


def exam_type_of(low: str) -> str | None:
    if "midterm" in low:
        return "midterm"
    m = re.search(r"quiz[\s_-]*(\d)(?!\d)", low) or re.search(r"quiz(?:19|20)\d\d[_-](\d)(?!\d)", low)
    if m:
        return f"quiz{m.group(1)}"
    if "quiz" in low:
        return "quiz1"
    if re.search(r"final|finl|exam", low):
        return "final"
    return None


def number_of(low: str, words: str) -> int | None:
    m = re.search(rf"(?:{words})[\s_-]*(\d+)", low)
    return int(m.group(1)) if m else None


def classify(course: str, page_doctype: str, slug: str, title: str, edition: str) -> dict | None:
    if NON_PDF_RE.search(title) or NON_PDF_RE.search(slug):
        return None
    low = f"{slug} {title}".lower()
    term = doc_term(low, edition)
    is_sol = bool(SOLUTION_RE.search(low))
    doc = {"course": course, "term": term, "is_solution": is_sol, "title": title, "slug": slug,
           "exam_type": None, "exam_id": None, "n": None}
    if page_doctype == "exam" and REVIEW_RE.search(low):
        doc["doctype"] = "review"
        doc["doc_id"] = f"{course}-review-{term}-{re.sub(r'[^a-z0-9]+', '-', slug.lower()).strip('-')}"
        final_date, _ = exam_date(f"{course}-final-{term}", term, "final")
        doc["date"] = (dt.date.fromisoformat(final_date) - dt.timedelta(days=1)).isoformat()
        doc["date_source"] = "assumed"
    elif page_doctype == "exam":
        exam_type = exam_type_of(low)
        if exam_type is None:
            return None
        exam_id = f"{course}-{exam_type}-{term}"
        doc.update(doctype="exam", exam_type=exam_type, exam_id=exam_id,
                   doc_id=exam_id + ("-sol" if is_sol else ""))
        doc["date"], doc["date_source"] = exam_date(exam_id, term, exam_type)
    elif page_doctype == "homework":
        n = number_of(low, r"pset|problem[\s_-]*set|homework|hw|ps")
        doc.update(doctype="homework", n=n,
                   doc_id=f"{course}-{term}-ps{n if n is not None else 'x'}" + ("-sol" if is_sol else ""))
        doc["date"], doc["date_source"] = homework_date(term, n)
    else:
        n = number_of(low, r"lecture|lec|session")
        doc.update(doctype="lecture", n=n, doc_id=f"{course}-{term}-lec{n if n is not None else 'x'}")
        doc["date"], doc["date_source"] = lecture_date(term, n)
    return doc


def run(args) -> int:
    course = check_course(args.course)
    skip = SkipList.load(args.skip_list, course)
    root, slug = course_root(args.course_url)
    edition = edition_term(slug)
    started_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    docs, seen_urls, seen_ids = [], set(), {}
    sealed, ignored, pages_missing = 0, 0, []
    for page, page_doctype in PAGES.items():
        page_url = urljoin(root, f"pages/{page}/")
        try:
            resp = ocw_get(page_url, skip)
        except OracleError as err:
            if "HTTP 404" in str(err) or "too many redirects" in str(err):
                pages_missing.append(page)
                continue
            raise
        resp.encoding = "utf-8"
        for href, inner in LINK_RE.findall(resp.text):
            url = check_ocw_url(urljoin(page_url, href))
            url = url if url.endswith("/") else url + "/"
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if skip.url_sealed(url):
                sealed += 1
                continue
            title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", inner))).strip()
            doc = classify(course, page_doctype, url.rstrip("/").rsplit("/", 1)[-1], title, edition)
            if doc is None:
                ignored += 1
                continue
            base = doc["doc_id"]
            seen_ids[base] = seen_ids.get(base, 0) + 1
            if seen_ids[base] > 1:
                doc["doc_id"] = f"{base}-{seen_ids[base]}"
                log(f"duplicate doc id {base}; renamed {doc['doc_id']}")
            doc["page_url"] = url
            docs.append(doc)
    if not docs:
        raise OracleError(f"no documents found under {root}")
    docs.sort(key=lambda d: d["doc_id"])
    counts = {}
    for d in docs:
        key = d["doctype"] + ("_sol" if d["is_solution"] else "")
        counts[key] = counts.get(key, 0) + 1
    result = {"ok": True, "course": course, "course_url": root, "edition_term": edition,
              "started_at": started_at, "docs": docs, "counts": counts,
              "sealed_excluded": sealed, "ignored_non_pdf": ignored,
              "pages_missing": pages_missing, "skip_list": skip.source}
    write_json(course_path("work", course) / "index.json", result)
    emit(result)
    return 0
