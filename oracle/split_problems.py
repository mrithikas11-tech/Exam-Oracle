"""Split exams, their solutions and problem sets into numbered problems with points.

Generic rules (no per-course code): problem headings are found by a sequential scan
(1, 2, 3, ...) over two heading styles — "Problem N" and "N. Title" — preferring headings
that print their points. Points come from the heading ("[20 points]", "(25 points)"), else
the sum of sub-part points or percentages. The printed total ("Total /100") and the real
exam date printed on the first page are captured when present.
Writes work/<course>/problems/<doc_id>.json.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from .config import OracleError, check_course, course_path, emit, save_report

HEAD_PROBLEM = re.compile(r"^\s{0,8}(?:Problem|PROBLEM|Question|QUESTION)\s*#?\s*(\d{1,2})(?:\.(\d{1,2}))?\b")
HEAD_NUMBER = re.compile(r"^\s{0,6}(\d{1,2})\.\s+\S")
POINTS = re.compile(r"[\(\[]\s*(\d{1,3}(?:\.\d+)?)\s*(?:points?|pts?)\s*[\)\]]|\b(\d{1,3}(?:\.\d+)?)\s+points?\b", re.I)
PERCENT = re.compile(r"\(\s*(\d{1,3}(?:\.\d+)?)\s*%\s*\)")
TOTAL = re.compile(r"\btotal\b\s*[:\-]?\s*/\s*(\d{2,3})\b|\btotal\b[^\n]{0,25}?\b(\d{2,3})\s*points\b", re.I)
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE = re.compile(rf"\b({MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})\b")
BOILERPLATE = re.compile(r"MIT OpenCourseWare|ocw\.mit\.edu|citing these materials|Terms of Use", re.I)


def add_args(p):
    p.add_argument("--course", required=True)


def _headings(lines: list[str], pattern: re.Pattern, with_points: bool) -> list[tuple[int, int]]:
    """Sequential scan: accept a heading only if it is the next expected number."""
    found, expected = [], 1
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if not m:
            continue
        groups = [g for g in m.groups() if g]
        number = int(groups[-1])
        if with_points and not POINTS.search(" ".join(lines[i:i + 2])):
            continue
        if number == expected:
            found.append((i, number))
            expected += 1
    return found


def raw_numbers(lines: list[str], pattern: re.Pattern, with_points: bool) -> list[int]:
    """Every heading number in the chosen style (not just sequential ones) — for validation."""
    out = []
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m and (not with_points or POINTS.search(" ".join(lines[i:i + 2]))):
            out.append(int([g for g in m.groups() if g][-1]))
    return out


def choose_style(lines: list[str]):
    best = None
    for pattern in (HEAD_PROBLEM, HEAD_NUMBER):
        for with_points in (True, False):
            heads = _headings(lines, pattern, with_points)
            if with_points and len(heads) < 2:
                continue
            if best is None or len(heads) > len(best[0]):
                best = (heads, pattern, with_points)
        if best and best[2]:
            break  # a points-bearing style wins outright
    return best or ([], HEAD_NUMBER, False)


def _points(block: str, head_line: str) -> float | None:
    m = POINTS.search(head_line)
    if m:
        return float(m.group(1) or m.group(2))
    subs = [float(a or b) for a, b in POINTS.findall(block)]
    if subs:
        return sum(subs)
    pct = [float(x) for x in PERCENT.findall(block)]
    return sum(pct) if pct else None


def printed_date(text: str, term: str) -> str | None:
    head = "\n".join(text.splitlines()[:80])
    for month, day, year in DATE.findall(head):
        if year == term[:4]:
            try:
                return dt.datetime.strptime(f"{month} {day} {year}", "%B %d %Y").date().isoformat()
            except ValueError:
                continue
    return None


def split_doc(meta: dict, text: str) -> dict:
    lines = [ln for ln in text.replace("\f", "\n").splitlines() if not BOILERPLATE.search(ln)]
    heads, pattern, with_points = choose_style(lines)
    kind = "exam" if meta["doctype"] == "exam" else "homework"
    problems = []
    for k, (start, number) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        block = "\n".join(lines[start:end]).strip()
        label = f"p{number}"
        if kind == "exam":
            item_id = f"{meta['exam_id']}-{label}"
        else:
            item_id = f"{meta['course']}-{meta['term']}-ps{meta['n'] if meta['n'] is not None else 'x'}-{label}"
        problems.append({"number": number, "label": label, "item_id": item_id,
                         "points": _points(block, " ".join(lines[start:start + 2])), "text": block})
    if not problems and kind == "homework":
        body = "\n".join(lines).strip()
        problems.append({"number": 0, "label": "all", "item_id": f"{meta['course']}-{meta['term']}-ps"
                         f"{meta['n'] if meta['n'] is not None else 'x'}-all", "points": None, "text": body})
    total = None
    m = TOTAL.search(text)
    if m:
        total = float(m.group(1) or m.group(2))
    pdf_date = printed_date(text, meta["term"]) if kind == "exam" else None
    return {
        "doc_id": meta["doc_id"], "kind": kind, "course": meta["course"], "term": meta["term"],
        "exam_type": meta.get("exam_type"), "exam_id": meta.get("exam_id"), "n": meta.get("n"),
        "is_solution": meta["is_solution"], "sha256": meta["sha256"],
        "date": pdf_date or meta["date"], "date_source": "pdf" if pdf_date else meta["date_source"],
        "style": {"pattern": "problem" if pattern is HEAD_PROBLEM else "number", "with_points": with_points},
        "raw_numbers": raw_numbers(lines, pattern, with_points),
        "printed_total": total, "problems": problems,
    }


def run(args) -> int:
    course = check_course(args.course)
    work = course_path("work", course)
    out_dir = course_path("work", course, "problems")
    for stale in out_dir.glob("*.json"):
        stale.unlink()
    counts = {"exam": 0, "exam_sol": 0, "homework": 0, "problems": 0}
    for path in sorted((work / "docs").glob("*.json")):
        meta = json.loads(path.read_text())
        if meta["doctype"] not in ("exam", "homework") or (meta["doctype"] == "homework" and meta["is_solution"]):
            continue
        text_path = work / "text" / f"{meta['doc_id']}.txt"
        if not text_path.exists():
            raise OracleError(f"no text for {meta['doc_id']}; run extract first")
        doc = split_doc(meta, text_path.read_text(errors="replace"))
        (out_dir / f"{meta['doc_id']}.json").write_text(json.dumps(doc, sort_keys=True, indent=1))
        key = meta["doctype"] + ("_sol" if meta["is_solution"] else "")
        counts[key] = counts.get(key, 0) + 1
        counts["problems"] += len(doc["problems"])
    report = {"ok": True, "course": course, **counts}
    save_report(course, "split", report)
    emit(report)
    return 0
