"""Upsert the run's row into the ledger `runs` table (key run_id) — the row the dashboard reads.

Spec: contracts/ledger-schema.sql (`runs`, every column), kit/03-architecture/rote-plays.md (append_ledger with an
idempotency key), kit/BUILDER-RULES.md §6 (every run records tokens, seconds, checks passed first try, leakage_ok and
the scores of the model and both baselines).

CLI:  python -m oracle.append_ledger --run-id r7-FX.101-final-2022F --leakage-ok true [--score-json <path|->]
          [--tokens N] [--seconds S] [--checks-first-try true|false]
Exit 0 ok | 1 hard fault (not scored yet, score of another prediction, bad values) | 5 the sealed prediction is
missing or does not verify.

Sources: the sealed prediction (verified) gives run_seq, course, target_exam, feature_set, cold_start, k,
lessons_run_seq, hash and made_at; the object oracle.score printed (--score-json, default the <run_id>.score.json
it wrote) gives the metrics and key_source, and must carry the same run_id and hash. tokens, seconds and
checks_first_try stay NULL unless given (never invented); leakage_ok is required (from the leakage_check step).
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Mapping

from oracle import config
from oracle.backend import Backend, get_backend
from oracle.rank_and_seal import JsonArgumentParser, parse_run_id, read_json_arg, read_sealed
from oracle.score import EXIT_UNSEALED, score_path

METRICS = ("model_pts", "even_pts", "lastexam_pts", "recall_k", "brier")


def run_row(prediction: Mapping[str, Any], digest: str, score: Mapping[str, Any], *, leakage_ok: bool,
            tokens: int | None = None, seconds: float | None = None,
            checks_first_try: bool | None = None) -> dict[str, Any]:
    """Every contract column of `runs` for one sealed + scored run."""
    run_id = prediction["run_id"]
    if score.get("run_id") != run_id or score.get("hash") != digest:
        raise ValueError(f"the score object is not the score of sealed run {run_id} (run_id/hash differ)")
    if score.get("key_source") not in ("human", "machine"):
        raise ValueError(f"score key_source must be human or machine, got {score.get('key_source')!r}")
    if tokens is not None and (isinstance(tokens, bool) or int(tokens) != tokens or tokens < 0):
        raise ValueError(f"tokens must be a non-negative integer, got {tokens!r}")
    if seconds is not None and not (math.isfinite(float(seconds)) and seconds >= 0):
        raise ValueError(f"seconds must be a finite number >= 0, got {seconds!r}")
    return {"run_id": run_id, "run_seq": parse_run_id(run_id, prediction["target_exam"]),
            "course": prediction["course"], "target_exam": prediction["target_exam"],
            "feature_set": prediction["feature_set"], "cold_start": bool(prediction.get("cold_start", False)),
            "k": prediction["k"], **{m: score[m] for m in METRICS}, "tokens": tokens, "seconds": seconds,
            "checks_first_try": checks_first_try, "leakage_ok": bool(leakage_ok), "key_source": score["key_source"],
            "lessons_run_seq": prediction["lessons_run_seq"], "hash": digest, "made_at": prediction["made_at"]}


def append_run(run_id: str, *, leakage_ok: bool, score: Mapping[str, Any] | None = None, tokens: int | None = None,
               seconds: float | None = None, checks_first_try: bool | None = None, backend: Backend | None = None,
               directory: str | os.PathLike | None = None) -> dict[str, Any]:
    """Build the row (see run_row) and upsert it into `runs`; returns the row."""
    prediction, digest = read_sealed(run_id, directory)
    if score is None:
        path = score_path(run_id, directory)
        if not path.is_file():
            raise FileNotFoundError(f"run {run_id} is not scored yet: run python -m oracle.score --run-id {run_id}")
        score = json.loads(path.read_text(encoding="utf-8"))
    row = run_row(prediction, digest, score, leakage_ok=leakage_ok, tokens=tokens, seconds=seconds,
                  checks_first_try=checks_first_try)
    be = backend or get_backend()
    be.load_table(be.ensure_ledger(), "runs", [row], mode="upsert")
    return row


def _bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    raise ValueError(f"expected true or false, got {value!r}")


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(prog="python -m oracle.append_ledger",
                                description="Upsert the run's row into the ledger runs table (key run_id).")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--leakage-ok", required=True, type=_bool, help="true|false, from the leakage_check step")
    parser.add_argument("--score-json", help="oracle.score output (path, or '-' for stdin); default <run_id>.score.json")
    parser.add_argument("--tokens", type=int, help="model tokens spent on this run (NULL if not given)")
    parser.add_argument("--seconds", type=float, help="wall-clock seconds of the run (NULL if not given)")
    parser.add_argument("--checks-first-try", type=_bool, help="true|false (NULL if not given)")
    args = parser.parse_args(argv)
    try:
        score = read_json_arg(args.score_json) if args.score_json else None
        row = append_run(args.run_id, leakage_ok=args.leakage_ok, score=score, tokens=args.tokens,
                         seconds=args.seconds, checks_first_try=args.checks_first_try)
    except config.SealViolation as exc:
        config.emit({"ok": False, "refused": True, "error": str(exc)})
        return EXIT_UNSEALED
    except Exception as exc:  # every other failure is a hard fault of this step
        config.emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return config.EXIT_HARD
    config.emit({"ok": True, "run_id": args.run_id, "row": row})
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
