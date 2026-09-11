"""Extract text from every downloaded PDF of a course (poppler `pdftotext -layout`).

Empty text (a scanned PDF) is a degraded result, not a failure: exit 0 with a warning.
`--ocr auto` repairs those with pdftoppm + tesseract. Also writes raw/<course>/manifest.csv
(hash -> original URL, type, term, date) — the only file from raw/ that is committed.
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .loader_config import OracleError, check_course, course_path, emit, save_report

MIN_CHARS = 200
MANIFEST_FIELDS = ["sha256", "doc_id", "doctype", "is_solution", "exam_id", "exam_type", "term", "n",
                   "date", "date_source", "title", "page_url", "pdf_url", "bytes", "text_chars", "ocr"]


def add_args(p):
    p.add_argument("--course", required=True)
    p.add_argument("--ocr", choices=["off", "auto"], default="off",
                   help="auto = OCR PDFs whose text layer is empty (repair step)")
    p.add_argument("--force", action="store_true")


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise OracleError(f"{name} not found on PATH (brew install poppler tesseract)")
    return path


def _pdftotext(pdf: Path, out: Path) -> None:
    proc = subprocess.run([_tool("pdftotext"), "-layout", "-enc", "UTF-8", str(pdf), str(out)],
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise OracleError(f"pdftotext failed on {pdf.name}: {proc.stderr.strip()[-300:]}")


def _ocr(pdf: Path, out: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="oracle-ocr-") as tmp:
        subprocess.run([_tool("pdftoppm"), "-r", "200", "-png", str(pdf), str(Path(tmp) / "page")],
                       check=True, capture_output=True, timeout=600)
        pages = []
        for img in sorted(Path(tmp).glob("page*.png")):
            proc = subprocess.run([_tool("tesseract"), str(img), "-"], capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                raise OracleError(f"tesseract failed on {img.name}: {proc.stderr.strip()[-300:]}")
            pages.append(proc.stdout)
        out.write_text("\f".join(pages))


def _chars(path: Path) -> int:
    return sum(not c.isspace() for c in path.read_text(errors="replace")) if path.exists() else 0


def run(args) -> int:
    course = check_course(args.course)
    docs_dir = course_path("work", course, "docs")
    text_dir = course_path("work", course, "text")
    raw_dir = course_path("raw", course)
    metas = sorted((json.loads(p.read_text()) for p in docs_dir.glob("*.json")), key=lambda m: m["doc_id"])
    if not metas:
        raise OracleError(f"no downloaded documents for {course}; run download first")
    extracted = cached = ocred = 0
    empty, rows = [], []
    for meta in metas:
        pdf = raw_dir / f"{meta['sha256']}.pdf"
        if not pdf.exists():
            raise OracleError(f"missing file for {meta['doc_id']}: {pdf.name}")
        out = text_dir / f"{meta['doc_id']}.txt"
        marker = text_dir / f"{meta['doc_id']}.ocr"
        if out.exists() and not args.force and (_chars(out) >= MIN_CHARS or args.ocr == "off" or marker.exists()):
            cached += 1
        else:
            _pdftotext(pdf, out)
            extracted += 1
            if _chars(out) < MIN_CHARS and args.ocr == "auto":
                _ocr(pdf, out)
                marker.write_text("tesseract\n")
                ocred += 1
        chars = _chars(out)
        if chars < MIN_CHARS:
            empty.append(meta["doc_id"])
        rows.append({**{k: meta.get(k) for k in MANIFEST_FIELDS}, "text_chars": chars,
                     "ocr": marker.exists()})
    with (raw_dir / "manifest.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    report = {"ok": True, "course": course, "documents": len(metas), "extracted": extracted,
              "cached": cached, "ocr_repaired": ocred, "empty_text": empty, "degraded": len(empty)}
    if empty:
        report["warning"] = f"{len(empty)} document(s) have no text layer (scanned?); rerun with --ocr auto"
    save_report(course, "extract", report)
    emit(report)
    return 0
