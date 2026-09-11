"""Thin wrapper over the hotdata CLI: argv lists only, never a shell, never SQL built from input."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from .config import OracleError

LEDGER_NAME = "ledger"
# Keyed tables make every load an idempotent upsert (hotdata SQL is read-only; writes are loads).
LEDGER_TABLES = {
    "exam_items": ["problem", "topic"],
    "homework_items": ["hw_id", "topic"],
    "homework_vec": ["hw_id"],
    "lectures": ["lecture_id", "topic"],
    "course_loads": ["load_id"],
    # role B / C tables from kit/03-architecture/data-model.md, declared here so keys agree
    "guidelines": ["guideline_id"],
    "guideline_tests": ["guideline_id", "run_id"],
    "students": ["student_id"],
    "predictions": ["run_id", "topic"],
    "runs": ["run_id"],
}


def ledger_catalog() -> str:
    catalog = os.environ.get("HOTDATA_LEDGER_CATALOG") or "exam_oracle_ledger"
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", catalog):
        raise OracleError(f"bad HOTDATA_LEDGER_CATALOG {catalog!r}")
    return catalog


def hd(args: list[str], timeout: int = 600) -> str:
    exe = shutil.which("hotdata")
    if not exe:
        raise OracleError("hotdata CLI not found on PATH (brew install hotdata-dev/tap/cli)")
    proc = subprocess.run([exe, "--no-input", *args], capture_output=True, text=True, timeout=timeout)
    if proc.returncode not in (0, 3):  # 3 = truncated preview, not a failure
        detail = (proc.stderr.strip() or proc.stdout.strip())[-600:]
        raise OracleError(f"hotdata {' '.join(args[:2])} failed (exit {proc.returncode}): {detail}")
    return proc.stdout


def find_database(catalog: str) -> str | None:
    listing = json.loads(hd(["databases", "list", "-o", "json"]) or "[]")
    items = listing if isinstance(listing, list) else listing.get("databases", [])
    for db in items:
        if db.get("default_catalog") == catalog:
            return db["id"]
    return None


def load_file(catalog: str, table: str, path: str, mode: str = "upsert") -> None:
    hd(["databases", "load", "--catalog", catalog, "--table", table, "--file", path, "--mode", mode])
