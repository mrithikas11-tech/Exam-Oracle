"""Command-line helpers shared by Person B's model & memory scripts (cognee_tag, cognee_feedback, students,
ontology).

kit/03-architecture/rote-plays.md: every script prints ONE JSON object and uses the documented exit codes.
argparse exits 2 on a usage error, but 2 means "skip-listed (sealed) URL encountered" in that table, so a Rote
replay could mistake a typo for a sealing fault. ScriptParser reports usage errors as {"ok": false, ...} and
exits EXIT_HARD (1) instead.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from oracle import config


class ScriptParser(argparse.ArgumentParser):
    """argparse.ArgumentParser whose usage errors print one JSON object and exit EXIT_HARD."""

    def error(self, message: str) -> NoReturn:
        config.emit({"ok": False, "error": f"usage: {message}"})
        sys.exit(config.EXIT_HARD)


def read_json_file(path: str) -> Any:
    """Parse a JSON file given on the command line (json.JSONDecodeError / OSError propagate to the caller)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
