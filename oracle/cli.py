"""`exam-oracle <command>` — one entry point so Rote Plays call a single PATH tool."""
from __future__ import annotations

import argparse
import importlib
import sys

from .config import OracleError, log

COMMANDS = {
    "index": ("oracle.ocw_index", "List a course's OCW documents with type, term and date"),
    "download": ("oracle.ocw_download", "Download one document (or a whole index) hash-named"),
    "extract": ("oracle.extract_text", "Extract text from downloaded PDFs; write the manifest"),
    "split": ("oracle.split_problems", "Split exams and problem sets into numbered problems with points"),
    "validate": ("oracle.validate", "Check numbering and point totals (exit 3 on failure)"),
    "ledger-init": ("oracle.load_hotdata", "Create the hotdata ledger database and keyed tables"),
    "load": ("oracle.load_hotdata", "Upsert a course's items into the hotdata ledger"),
    "cognee": ("oracle.cognee_remember", "Write pre-split items for Cognee and remember them if configured"),
    "tags": ("oracle.load_hotdata", "Upsert topic tags written by the Cognee step into the ledger"),
    "notify": ("oracle.notify_rocketride", "POST a course-loaded event to the RocketRide webhook"),
    "summary": ("oracle.summary", "Summarise a load and append it to ledger.course_loads"),
}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="exam-oracle", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="command")
    for name, (_, help_text) in COMMANDS.items():
        sub.add_parser(name, help=help_text, add_help=False)
    if not argv or argv[0] not in COMMANDS:
        parser.parse_args(argv)  # prints usage / error
        return 1
    name = argv[0]
    module = importlib.import_module(COMMANDS[name][0])
    cmd_parser = argparse.ArgumentParser(prog=f"exam-oracle {name}", description=COMMANDS[name][1])
    getattr(module, "add_args_" + name.replace("-", "_"), module.add_args)(cmd_parser)
    args = cmd_parser.parse_args(argv[1:])
    run = getattr(module, "run_" + name.replace("-", "_"), None) or module.run
    try:
        return int(run(args) or 0)
    except OracleError as err:
        log(f"exam-oracle {name}: {err}")
        return err.code
    except Exception as err:  # hard fault with evidence, never a silent success
        log(f"exam-oracle {name}: unexpected {type(err).__name__}: {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
