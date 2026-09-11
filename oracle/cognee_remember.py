"""Hand pre-split items to Cognee (dataset course-<code>) for topic tagging.

Always writes work/<course>/cognee_items.jsonl: one item per exam problem (+ its solution),
homework problem and lecture, with node_set = [course, term, doctype] and metadata set in
code (the LLM only maps items to topics). The Cognee call itself belongs to role B: if a
module `oracle.cognee_ingest` exposing `remember_items(items, dataset_name, dry_run)` exists
and COGNEE is configured, it is called; otherwise this step is degraded (exit 0 + warning).
A service error from a configured Cognee is a hard fault (exit 1).
"""
from __future__ import annotations

import importlib
import json
import os

from .config import OracleError, check_course, course_path, emit, save_report

MAX_ITEM_CHARS = 8000


def add_args(p):
    p.add_argument("--course", required=True)
    p.add_argument("--dry-run", action="store_true", help="price the ingest (Cognee dry_run=True)")


def build_items(course: str) -> list[dict]:
    work = course_path("work", course)
    items = []
    solutions = {}
    for path in sorted((work / "problems").glob("*.json")):
        doc = json.loads(path.read_text())
        if doc.get("is_solution"):
            key = doc["doc_id"].removesuffix("-sol")
            solutions[key] = {p["label"]: p["text"] for p in doc["problems"]}
    for path in sorted((work / "problems").glob("*.json")):
        doc = json.loads(path.read_text())
        if doc.get("is_solution"):
            continue
        sols = solutions.get(doc["doc_id"], {})
        for prob in doc["problems"]:
            text = prob["text"]
            if prob["label"] in sols:
                text += "\n\nSOLUTION:\n" + sols[prob["label"]]
            items.append({"item_id": prob["item_id"], "doctype": doc["kind"], "course": course,
                          "term": doc["term"], "exam_id": doc.get("exam_id"), "points": prob.get("points"),
                          "date": doc["date"], "node_set": [course, doc["term"], doc["kind"]],
                          "text": text[:MAX_ITEM_CHARS]})
    for path in sorted((work / "docs").glob("*.json")):
        meta = json.loads(path.read_text())
        if meta["doctype"] != "lecture":
            continue
        text_path = work / "text" / f"{meta['doc_id']}.txt"
        text = text_path.read_text(errors="replace") if text_path.exists() else ""
        items.append({"item_id": meta["doc_id"], "doctype": "lecture", "course": course, "term": meta["term"],
                      "exam_id": None, "points": None, "date": meta["date"], "title": meta.get("title"),
                      "node_set": [course, meta["term"], "lecture"], "text": text[:MAX_ITEM_CHARS]})
    return items


def run(args) -> int:
    course = check_course(args.course)
    items = build_items(course)
    if not items:
        raise OracleError(f"no items for {course}; run split first")
    out = course_path("work", course) / "cognee_items.jsonl"
    out.write_text("".join(json.dumps(i, sort_keys=True) + "\n" for i in items))
    dataset = "course-" + course.replace(".", "-")
    report = {"ok": True, "course": course, "items": len(items), "dataset": dataset, "dry_run": args.dry_run}
    try:
        ingest = importlib.import_module("oracle.cognee_ingest")
    except ModuleNotFoundError:
        ingest = None
    if ingest is None or not (os.environ.get("COGNEE_BASE_URL") or os.environ.get("LLM_API_KEY")):
        report.update(available=False, degraded=1,
                      warning="Cognee ingest not wired/configured yet (role B); items written for later ingest")
    else:
        try:
            result = ingest.remember_items(items, dataset_name=dataset, dry_run=args.dry_run)
        except Exception as err:
            raise OracleError(f"Cognee remember failed: {type(err).__name__}: {err}")
        report.update(available=True, degraded=0, cognee=result)
    save_report(course, "cognee", report)
    emit(report)
    return 0
