"""Ledger loads owned by the loader: `ledger-init` (create the ledger) and `load` (a course's homework).

homework_items gets one row per (problem, topic): a problem set covers the lectures from its issued session
(else the previous set's due session) up to, not including, its due session, and each problem is filed under
those lectures' topics (course structure, not a model; a set with no due session is skipped). homework_vec gets
one row per problem; it has no key, so it is replaced with every other course's rows kept.
"""
from __future__ import annotations

import json

from oracle.backend import get_backend
from oracle.ordering import term_to_seq
from oracle.text_clean import clean_text

from .loader_config import OracleError, check_course, course_path, emit, save_report
from .structure_io import as_bool, contract_rows, course_dir, read_course_json, read_csv


def add_args(p):
    p.add_argument("--course", required=True)


def add_args_ledger_init(p):
    pass


def run_ledger_init(args) -> int:
    backend = get_backend()
    handle = backend.ensure_ledger()
    emit({"ok": True, "backend": backend.name, "ledger": handle.to_dict()})
    return 0


def set_topics(course: str, published_term: str) -> dict[str, tuple[int, list[str]]]:
    """set -> (due_session, topic_ids of the lectures it covers)."""
    folder = course_dir(course)
    lectures = [r for r in read_csv(folder / "sessions.csv") if r["term"] == published_term]
    sets = [r for r in read_csv(folder / "homework_sets.csv") if r["term"] == published_term and r["due_session"]]
    sets.sort(key=lambda r: int(r["due_session"]))
    out, previous_due = {}, 1
    for row in sets:
        due = int(row["due_session"])
        start = int(row["issued_session"]) if row.get("issued_session") else previous_due
        topics = sorted({lec["topic_id"] for lec in lectures
                         if lec.get("topic_id") and start <= int(lec["session"]) < due
                         and not as_bool(lec.get("is_review"))})
        out[str(row["set"])] = (due, topics)
        previous_due = due
    return out


def run(args) -> int:
    course = check_course(args.course)
    published = read_course_json(course)["published_term"]
    windows = set_topics(course, published)
    problems_dir = course_path("work", course, "problems")
    items, vec, skipped_sets, no_topic = [], [], set(), set()
    for path in sorted(problems_dir.glob("*.json")):
        doc = json.loads(path.read_text())
        if doc["kind"] != "homework" or doc["is_solution"]:
            continue
        set_id = str(doc["n"]) if doc.get("n") is not None else "opt"
        if doc["term"] != published or set_id not in windows:
            skipped_sets.add(doc["doc_id"])
            continue
        due, topics = windows[set_id]
        if not topics:
            no_topic.add(set_id)
            continue
        for prob in doc["problems"]:
            text = clean_text(prob["text"])
            vec.append({"course": course, "hw_id": prob["item_id"], "text_clean": text})
            for topic in topics:
                items.append({"course": course, "term": doc["term"], "term_seq": term_to_seq(doc["term"]),
                              "session": due, "hw_id": prob["item_id"], "topic_id": topic, "text_clean": text})
    if not items:
        raise OracleError(f"no homework rows for {course}: check homework_sets.csv due sessions")
    backend = get_backend()
    ledger = backend.ensure_ledger()
    n_items = backend.load_table(ledger, "homework_items", contract_rows("homework_items", items), mode="upsert")
    others = backend.query(ledger, "SELECT course, hw_id, text_clean FROM {{homework_vec}} WHERE course <> $course",
                           {"course": course})
    vec_rows = contract_rows("homework_vec", others.to_dict("records") + vec)
    backend.load_table(ledger, "homework_vec", vec_rows, mode="replace")
    report = {"ok": True, "course": course, "backend": backend.name, "homework_problems": len(vec),
              "rows": {"homework_items": n_items, "homework_vec": len(vec)},
              "skipped_docs": sorted(skipped_sets), "sets_without_topics": sorted(no_topic)}
    if skipped_sets or no_topic:
        report["degraded"] = 1
        report["warning"] = f"{len(skipped_sets)} homework doc(s) outside the published term's sets skipped"
    save_report(course, "load", report)
    emit(report)
    return 0
