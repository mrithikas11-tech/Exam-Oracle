"""Check every split exam before it is loaded. Hard faults exit 3; soft issues are degraded.

Hard (exit 3): an exam with no problems; heading numbers that are not exactly 1..N;
problem points that do not add up to the printed total.
Degraded (exit 0 + warning): points not printed (equal split used); no solutions file.
Points are finalised here (points_source = printed | equal) and written back.
"""
from __future__ import annotations

import json

from .loader_config import EXIT_INVALID, OracleError, check_course, course_path, emit, log, read_report, save_report

TOLERANCE = 0.51


def add_args(p):
    p.add_argument("--course", required=True)


def check_exam(doc: dict, has_solution: bool) -> tuple[list[str], list[str]]:
    hard, soft = [], []
    probs = doc["problems"]
    n = len(probs)
    if n == 0:
        return [f"{doc['doc_id']}: no problems found (scanned PDF or unknown layout)"], soft
    if doc["style"]["with_points"] and doc["raw_numbers"] != list(range(1, n + 1)):
        hard.append(f"{doc['doc_id']}: problem numbering {doc['raw_numbers']} is not 1..{n}")
    known = [p["points"] for p in probs if p["points"] is not None]
    total = doc.get("printed_total")
    if len(known) == n:
        doc["points_source"] = "printed"
        if total is not None and abs(sum(known) - total) > TOLERANCE:
            hard.append(f"{doc['doc_id']}: points {known} sum to {sum(known):g}, printed total is {total:g}")
    else:
        base = total if total is not None else 100.0
        share = round(base / n, 4)
        for p in probs:
            p["points"] = share
        doc["points_source"] = "equal"
        soft.append(f"{doc['doc_id']}: points not printed for every problem; equal split of {base:g}")
    if not has_solution:
        soft.append(f"{doc['doc_id']}: no solutions file")
    return hard, soft


def run(args) -> int:
    course = check_course(args.course)
    problems_dir = course_path("work", course, "problems")
    index_path = course_path("work", course) / "index.json"
    if not index_path.exists():
        raise OracleError(f"no index for {course}; run index first")
    index = json.loads(index_path.read_text())
    previous = read_report(course, "validate") or {}
    attempts = previous.get("attempts", 0) + 1 if previous.get("load_started_at") == index["started_at"] else 1
    docs = {p.stem: json.loads(p.read_text()) for p in sorted(problems_dir.glob("*.json"))}
    hard, soft, exams = [], [], 0
    for doc_id, doc in docs.items():
        if doc["kind"] != "exam" or doc["is_solution"]:
            continue
        exams += 1
        h, s = check_exam(doc, f"{doc_id}-sol" in docs)
        hard += h
        soft += s
        (problems_dir / f"{doc_id}.json").write_text(json.dumps(doc, sort_keys=True, indent=1))
    if exams == 0:
        hard.append(f"{course}: no exams found; nothing to predict from")
    report = {"ok": not hard, "course": course, "exams": exams, "hard_faults": hard, "degraded": len(soft),
              "warnings": soft, "attempts": attempts, "load_started_at": index["started_at"]}
    if soft:
        report["warning"] = f"{len(soft)} degraded check(s); see warnings"
    save_report(course, "validate", report)
    emit(report)
    if hard:
        for fault in hard:
            log(f"VALIDATE HARD FAULT: {fault}")
        return EXIT_INVALID
    return 0
