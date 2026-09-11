"""Read the beta lessons the next prediction should use: `python -m oracle.read_lessons [--before-run-seq N]`.

Spec: kit/03-architecture/rote-plays.md (read_lessons.py -> "current beta from HydraDB"; Play 2 step 4),
kit/02-product/prediction-model.md "From signals to probability" (cold start; the beta weights are the lessons,
one version per run_seq), contracts/ledger-schema.sql `lessons` (the ledger mirror, key (run_seq, signal)),
kit/02-product/data-sources-and-sealing.md sealing rule 5 (runs go forward in time only).

Two sources hold lessons versions:
  * the lessons store (LESSONS_STORE: HydraDB `shared`, or the local JSONL) — the primary copy;
  * the ledger `lessons` mirror, written by store_lessons in the same step. HydraDB ingest answers 202
    (accepted, not finished), so right after a write the mirror can be one version ahead of what HydraDB serves.
"Latest" is the highest run_seq either source holds; a tie goes to the store. With --before-run-seq N only
versions with run_seq < N count, so a prediction (or a replay) of run N never uses lessons learned from run N
or later. No version found -> the cold-start weights (lessons_store.COLD_START_WEIGHTS).

Prints ONE JSON object: {"ok", "cold_start", "source", "run_seq", "weights", "statements", "supporting_runs"}.
Exit codes (rote-plays.md): 0 ok; 1 hard fault (store or ledger unreadable, bad arguments).

Also home of the small CLI helpers the learning-loop scripts share (ScriptArgumentParser, parse_cli,
non_negative_int).
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, NoReturn, Sequence

from oracle import config
from oracle.backend import Backend, DbRef, get_backend
from oracle.lessons_store import SIGNALS, LessonsStore, cold_start_record, get_lessons_store, make_record


# ------------------------------------------------------------------ CLI helpers (shared by the lessons scripts)
class ScriptArgumentParser(argparse.ArgumentParser):
    """argparse with the script conventions of rote-plays.md: a usage error prints ONE JSON object on stdout
    and exits EXIT_HARD. (argparse's own exit code for usage errors is 2, which the Rote lanes read as
    EXIT_SKIP_LIST.) The usage text still goes to stderr."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        config.emit({"ok": False, "error": f"invalid arguments: {message}"})
        raise SystemExit(config.EXIT_HARD)


def parse_cli(parser: argparse.ArgumentParser, argv: Sequence[str] | None) -> argparse.Namespace | int:
    """parse_args() that returns an exit code instead of raising SystemExit (usage error -> EXIT_HARD,
    --help -> EXIT_OK), so main() can always `return` its code."""
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else config.EXIT_OK


def non_negative_int(text: str) -> int:
    """argparse type for run_seq values (lessons_store.make_record requires run_seq >= 0)."""
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be a non-negative integer, got {value}")
    return value


# ------------------------------------------------------------------ versions
def ledger_versions(backend: Backend | None = None, ledger: DbRef | None = None, *,
                    before_run_seq: int | None = None) -> list[dict[str, Any]]:
    """Every complete lessons version in the ledger mirror (optionally only run_seq < before_run_seq), oldest
    first. A local ledger that does not exist yet holds none. A version with a missing or conflicting signal
    raises ValueError: a corrupt mirror must fail loudly, never silently fall back to older lessons."""
    be = backend if backend is not None else get_backend()
    sql, params = "SELECT run_seq, signal, weight, statement, supporting_runs FROM {{lessons}}", {}
    if before_run_seq is not None:
        sql, params = sql + " WHERE run_seq < $before", {"before": int(before_run_seq)}
    try:
        frame = be.query(ledger if ledger is not None else be.ledger(), sql + " ORDER BY run_seq, signal", params)
    except FileNotFoundError:  # local backend: the ledger file has not been created yet
        return []
    versions = []
    for run_seq, group in frame.drop_duplicates().groupby("run_seq", sort=True):
        signals = group["signal"].tolist()
        if len(signals) != len(set(signals)) or set(signals) != set(SIGNALS):
            raise ValueError(f"ledger lessons version {run_seq} is inconsistent (signals: {sorted(signals)})")
        runs = next((r for r in group["supporting_runs"] if isinstance(r, str) and r), "")
        versions.append(make_record(
            int(run_seq), dict(zip(signals, group["weight"].astype(float))),
            {s: t for s, t in zip(signals, group["statement"]) if isinstance(t, str)},
            [r for r in runs.split(";") if r]))
    return versions


def store_versions(store: LessonsStore) -> list[dict[str, Any]]:
    """The versions a lessons store can show: its whole history when it keeps one (the local JSONL), else only
    its latest (HydraDB serves the `lessons_latest` pointer)."""
    history = getattr(store, "history", None)
    if callable(history):
        return [make_record(r["run_seq"], r["weights"], r.get("statements", {}), r.get("supporting_runs", []))
                for r in history()]
    latest = store.read_latest()
    return [] if latest["run_seq"] is None else [latest]


def resolve_lessons(before_run_seq: int | None = None, *, store: LessonsStore | None = None,
                    backend: Backend | None = None, ledger: DbRef | None = None) -> dict[str, Any]:
    """The lessons record to use (module docstring), plus "source": lessons_store | ledger | cold_start.
    Ranking key (run_seq, source, write order): the highest run_seq wins, then the store over the ledger, then
    the most recent write (a replayed run_seq serves its newest write, as LocalJSONLessonsStore does)."""
    candidates = []
    for position, record in enumerate(store_versions(store if store is not None else get_lessons_store())):
        candidates.append(((record["run_seq"], 1, position), record, "lessons_store"))
    for position, record in enumerate(ledger_versions(backend, ledger, before_run_seq=before_run_seq)):
        candidates.append(((record["run_seq"], 0, position), record, "ledger"))
    if before_run_seq is not None:
        candidates = [c for c in candidates if c[0][0] < before_run_seq]
    if not candidates:
        return {**cold_start_record(), "source": "cold_start"}
    _, record, source = max(candidates, key=lambda c: c[0])
    return {**record, "source": source}


def main(argv: Sequence[str] | None = None) -> int:
    parser = ScriptArgumentParser(
        prog="python -m oracle.read_lessons",
        description="Print the lessons (beta weights) the next prediction should use, as one JSON object.")
    parser.add_argument("--before-run-seq", type=non_negative_int, default=None, metavar="N",
                        help="only lessons versions with run_seq < N (pass the run_seq being predicted)")
    args = parse_cli(parser, argv)
    if isinstance(args, int):
        return args
    try:
        record = resolve_lessons(args.before_run_seq)
    except Exception as exc:  # script boundary: every failure is reported as one JSON object (hard-fault lane)
        config.emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return config.EXIT_HARD
    config.emit({"ok": True, "cold_start": record["run_seq"] is None, **record})
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
