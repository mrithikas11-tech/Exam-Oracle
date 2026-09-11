"""Write role B's tagger input: work/<course>/items.json for `oracle.cognee_tag --items`.

One item per problem of every validated, non-sealed exam registered in data/courses/<course>/exams.csv:
{"course", "exam_id", "problem", "points", "text", "text_clean"}. The problem's solution text (when the
solutions PDF split cleanly) is appended, so the tagger sees what the problem actually tests.
A sealed exam reaching this step is a hard fault (exit 2); an exam the structure file does not list is degraded.
"""
from __future__ import annotations

import json

from oracle.text_clean import clean_text

from .loader_config import EXIT_SEALED, OracleError, check_course, course_path, emit, save_report, write_json
from .structure_io import as_bool, exams_by_id

MAX_TEXT_CHARS = 40_000  # oracle.cognee_tag.MAX_TEXT_CHARS


def add_args(p):
    p.add_argument("--course", required=True)


def run(args) -> int:
    course = check_course(args.course)
    registered = exams_by_id(course)
    sealed = {exam_id for exam_id, row in registered.items() if as_bool(row.get("sealed"))}
    problems_dir = course_path("work", course, "problems")
    docs = {p.stem: json.loads(p.read_text()) for p in sorted(problems_dir.glob("*.json"))}
    items, unregistered, exams = [], set(), set()
    for doc_id, doc in docs.items():
        if doc["kind"] != "exam" or doc["is_solution"]:
            continue
        exam_id = doc["exam_id"]
        if exam_id in sealed:
            raise OracleError(f"SEALED: {exam_id} reached the items step", EXIT_SEALED)
        if exam_id not in registered:
            unregistered.add(exam_id)
            continue
        solutions = {p["label"]: p["text"] for p in docs.get(f"{doc_id}-sol", {}).get("problems", [])}
        for prob in doc["problems"]:
            if prob["number"] < 1 or not prob.get("points"):
                continue
            text = prob["text"]
            if prob["label"] in solutions:
                text += "\n\nSOLUTION:\n" + solutions[prob["label"]]
            items.append({"course": course, "exam_id": exam_id, "problem": str(prob["number"]),
                          "points": float(prob["points"]), "text": text[:MAX_TEXT_CHARS],
                          "text_clean": clean_text(prob["text"])})
            exams.add(exam_id)
    if not items:
        raise OracleError(f"no taggable exam problems for {course}; run split/validate and check exams.csv")
    out = course_path("work", course) / "items.json"
    write_json(out, {"items": items})
    report = {"ok": True, "course": course, "items": len(items), "exams": len(exams),
              "unregistered_exams": sorted(unregistered), "path": str(out)}
    if unregistered:
        report["degraded"] = 1
        report["warning"] = f"{len(unregistered)} exam(s) not in data/courses/{course}/exams.csv were skipped"
    save_report(course, "items", report)
    emit(report)
    return 0
