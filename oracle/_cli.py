"""Command-line helpers shared by Person B's model & memory scripts (cognee_tag, cognee_feedback, students,
ontology).

kit/03-architecture/rote-plays.md: every script prints ONE JSON object and uses the documented exit codes.
argparse exits 2 on a usage error, but 2 means "skip-listed (sealed) URL encountered" in that table, so a Rote
replay could mistake a typo for a sealing fault. ScriptParser (now config.ScriptParser, the one shared copy)
reports usage errors as {"ok": false, ...} and exits EXIT_HARD (1) instead.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from oracle import config

ScriptParser = config.ScriptParser  # kept under this name for the modules that import it from here


def read_json_file(path: str) -> Any:
    """Parse a JSON file given on the command line (json.JSONDecodeError / OSError propagate to the caller)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
