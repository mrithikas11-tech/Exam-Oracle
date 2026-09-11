"""Download one OCW document (resource page -> its PDF), saved as raw/<course>/<sha256>.pdf.

Refuses skip-listed page URLs, PDF URLs and content hashes (exit 2). Remote filenames are
never used as paths. A per-document sidecar (work/<course>/docs/<doc_id>.json) makes replays
cache hits, so a resumed or repeated run does not re-download.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin

from .config import (MAX_DOWNLOAD_BYTES, OracleError, SkipList, check_course, check_ocw_url,
                     course_path, emit, log, ocw_get, write_json)

PDF_HREF_RE = re.compile(r'href="([^"#?]+\.pdf)"', re.I)
DOC_ID_RE = re.compile(r"^[A-Za-z0-9._-]{3,120}$")


def add_args(p):
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--doc", help="one document as JSON (an element of index.docs)")
    src.add_argument("--index", metavar="COURSE", help="download every doc in work/<COURSE>/index.json")
    p.add_argument("--skip-list", default="builtin")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--force", action="store_true", help="ignore the cache")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_doc(doc: dict, skip: SkipList, force: bool = False) -> dict:
    course = check_course(doc.get("course", ""))
    doc_id = doc.get("doc_id", "")
    if not DOC_ID_RE.match(doc_id):
        raise OracleError(f"unsafe doc_id {doc_id!r}")
    page_url = check_ocw_url(doc.get("page_url", ""))
    skip.check_url(page_url)
    meta_path = course_path("work", course, "docs") / f"{doc_id}.json"
    raw_dir = course_path("raw", course)

    if meta_path.exists() and not force:
        meta = json.loads(meta_path.read_text())
        pdf = raw_dir / f"{meta['sha256']}.pdf"
        if pdf.exists() and _sha256_file(pdf) == meta["sha256"]:
            skip.check_hash(meta["sha256"])
            return {"doc_id": doc_id, "sha256": meta["sha256"], "bytes": meta["bytes"], "cached": True}

    page = ocw_get(page_url, skip)
    page.encoding = "utf-8"
    candidates = []
    for href in PDF_HREF_RE.findall(page.text):
        try:
            candidates.append(check_ocw_url(urljoin(page_url, href)))
        except OracleError:
            continue  # off-site links on the page are ignored, never fetched
    if not candidates:
        raise OracleError(f"no OCW PDF link on {page_url}")
    pdf_url = candidates[0]
    skip.check_url(pdf_url)

    resp = ocw_get(pdf_url, skip, stream=True)
    h, size, chunks = hashlib.sha256(), 0, []
    for chunk in resp.iter_content(1 << 16):
        size += len(chunk)
        if size > MAX_DOWNLOAD_BYTES:
            resp.close()
            raise OracleError(f"{pdf_url} exceeds {MAX_DOWNLOAD_BYTES} bytes")
        h.update(chunk)
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data.startswith(b"%PDF"):
        raise OracleError(f"{pdf_url} did not return a PDF")
    sha = h.hexdigest()
    skip.check_hash(sha)

    target = raw_dir / f"{sha}.pdf"
    if not target.exists():
        tmp = target.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(target)
    meta = {**doc, "pdf_url": pdf_url, "sha256": sha, "bytes": size,
            "downloaded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    write_json(meta_path, meta)
    return {"doc_id": doc_id, "sha256": sha, "bytes": size, "cached": False}


def run(args) -> int:
    skip = SkipList.load(args.skip_list)
    if args.doc:
        try:
            doc = json.loads(args.doc)
        except json.JSONDecodeError as err:
            raise OracleError(f"--doc is not JSON: {err}")
        emit({"ok": True, **download_doc(doc, skip, args.force)})
        return 0

    course = check_course(args.index)
    index = json.loads((course_path("work", course) / "index.json").read_text())
    results, failures = [], []
    with cf.ThreadPoolExecutor(max_workers=max(1, min(args.concurrency, 8))) as pool:
        futures = {pool.submit(download_doc, d, skip, args.force): d["doc_id"] for d in index["docs"]}
        for fut in cf.as_completed(futures):
            try:
                results.append(fut.result())
            except OracleError as err:
                failures.append({"doc_id": futures[fut], "error": str(err), "code": err.code})
    results.sort(key=lambda r: r["doc_id"])
    summary = {"ok": not failures, "course": course, "downloaded": sum(not r["cached"] for r in results),
               "cached": sum(r["cached"] for r in results), "failures": failures}
    emit(summary)
    if failures:
        codes = {f["code"] for f in failures}
        for f in failures:
            log(f"download failed: {f['doc_id']}: {f['error']}")
        raise OracleError(f"{len(failures)} download(s) failed", 2 if 2 in codes else 1)
    return 0
