"""hotdata ledger: create it (`ledger-init`), upsert a course's items (`load`), add topic tags (`tags`).

All writes are keyed upserts, so replaying a load never duplicates rows. Items carry the
sentinel topic "_untagged" until the Cognee step writes work/<course>/topic_tags.csv.
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from .config import OracleError, check_course, course_path, emit, save_report
from .hotdata_cli import LEDGER_NAME, LEDGER_TABLES, find_database, hd, ledger_catalog, load_file
from .textutil import clean

UNTAGGED = "_untagged"
MAX_TEXT = 20000
COLUMNS = {
    "exam_items": ["problem", "topic", "course", "term", "exam_type", "exam_id", "points", "points_source",
                   "date", "text", "text_clean", "source_sha256"],
    "homework_items": ["hw_id", "topic", "course", "term", "ps", "date", "text", "text_clean"],
    "homework_vec": ["hw_id", "course", "term", "text_clean"],
    "lectures": ["lecture_id", "topic", "course", "term", "n", "title", "date", "text_clean"],
}
KEYS = {"exam_items": "problem", "homework_items": "hw_id", "lectures": "lecture_id"}


def add_args(p):
    p.add_argument("--course", required=True)


def add_args_ledger_init(p):
    pass


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_ledger_init(args) -> int:
    catalog = ledger_catalog()
    db = find_database(catalog)
    created = db is None
    if created:
        db = json.loads(hd(["databases", "create", "--name", LEDGER_NAME, "--catalog", catalog, "-o", "json"]))["id"]
    declared, existing = [], []
    for table, keys in LEDGER_TABLES.items():
        argv = ["databases", "tables", "add", table, "--database", db]
        for key in keys:
            argv += ["--key", key]
        try:
            hd(argv)
            declared.append(table)
        except OracleError as err:
            if not re.search(r"exist|already|conflict|duplicate", str(err), re.I):
                raise
            existing.append(table)
    emit({"ok": True, "database_id": db, "catalog": catalog, "created": created,
          "declared": declared, "already_declared": existing})
    return 0


def build_rows(course: str) -> dict[str, list[dict]]:
    work = course_path("work", course)
    rows = defaultdict(list)
    for path in sorted((work / "problems").glob("*.json")):
        doc = json.loads(path.read_text())
        if doc.get("is_solution"):
            continue
        for prob in doc["problems"]:
            text = prob["text"][:MAX_TEXT]
            if doc["kind"] == "exam":
                rows["exam_items"].append({
                    "problem": prob["item_id"], "topic": UNTAGGED, "course": course, "term": doc["term"],
                    "exam_type": doc["exam_type"], "exam_id": doc["exam_id"], "points": prob["points"],
                    "points_source": doc["points_source"], "date": doc["date"], "text": text,
                    "text_clean": clean(text), "source_sha256": doc["sha256"]})
            else:
                base = {"hw_id": prob["item_id"], "course": course, "term": doc["term"], "text_clean": clean(text)}
                rows["homework_items"].append({**base, "topic": UNTAGGED, "ps": doc.get("n"),
                                               "date": doc["date"], "text": text})
                rows["homework_vec"].append(base)
    for path in sorted((work / "docs").glob("*.json")):
        meta = json.loads(path.read_text())
        if meta["doctype"] != "lecture":
            continue
        text_path = work / "text" / f"{meta['doc_id']}.txt"
        text = text_path.read_text(errors="replace") if text_path.exists() else ""
        rows["lectures"].append({"lecture_id": meta["doc_id"], "topic": UNTAGGED, "course": course,
                                 "term": meta["term"], "n": meta.get("n"), "title": meta.get("title", ""),
                                 "date": meta["date"], "text_clean": clean(text)[:MAX_TEXT]})
    return rows


def run(args) -> int:
    course = check_course(args.course)
    catalog = ledger_catalog()
    if find_database(catalog) is None:
        raise OracleError(f"ledger catalog {catalog} not found; run `exam-oracle ledger-init` first")
    load_dir = course_path("work", course, "load")
    counts = {}
    for table, rows in build_rows(course).items():
        if not rows:
            continue
        path = load_dir / f"{table}.csv"
        _write_csv(path, COLUMNS[table], rows)
        load_file(catalog, table, str(path), "upsert")
        counts[table] = len(rows)
    if not counts.get("exam_items"):
        raise OracleError(f"no exam items to load for {course}")
    report = {"ok": True, "course": course, "catalog": catalog, "rows": counts, "items": sum(counts.values())}
    save_report(course, "load", report)
    emit(report)
    return 0


def run_tags(args) -> int:
    """Apply topic tags (item_id, topic) from the Cognee step; split exam points across topics."""
    course = check_course(args.course)
    tags_path = course_path("work", course) / "topic_tags.csv"
    if not tags_path.exists():
        report = {"ok": True, "course": course, "available": False, "degraded": 1,
                  "warning": "no topic_tags.csv yet (Cognee tagging is role B); items stay _untagged"}
        save_report(course, "tags", report)
        emit(report)
        return 0
    tags = defaultdict(list)
    with tags_path.open() as f:
        for row in csv.DictReader(f):
            if row.get("item_id") and row.get("topic"):
                tags[row["item_id"]].append(row["topic"].strip())
    catalog = ledger_catalog()
    load_dir = course_path("work", course, "load")
    counts = {}
    for table, rows in build_rows(course).items():
        if table == "homework_vec":
            continue
        key = KEYS[table]
        tagged, untag = [], []
        for row in rows:
            topics = sorted(set(tags.get(row[key], [])))
            if not topics:
                continue
            for topic in topics:
                new = {**row, "topic": topic}
                if table == "exam_items" and row["points"] not in (None, ""):
                    new["points"] = round(float(row["points"]) / len(topics), 4)
                tagged.append(new)
            untag.append({key: row[key], "topic": UNTAGGED})
        if tagged:
            _write_csv(load_dir / f"{table}.tagged.csv", COLUMNS[table], tagged)
            load_file(catalog, table, str(load_dir / f"{table}.tagged.csv"), "upsert")
            _write_csv(load_dir / f"{table}.untag.csv", [key, "topic"], untag)
            load_file(catalog, table, str(load_dir / f"{table}.untag.csv"), "delete")
        counts[table] = len(tagged)
    report = {"ok": True, "course": course, "available": True, "degraded": 0, "tagged_rows": counts}
    save_report(course, "tags", report)
    emit(report)
    return 0


add_args_tags = add_args
