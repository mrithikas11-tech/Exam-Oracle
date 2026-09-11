"""python -m oracle.backtest --course C --target-exam E --run-seq N [--cold-start] [--allow-machine-key]
                            [--allow-machine-labels] [--made-at ISO] [--seal-only | --frozen]
                            [--step NAME <step inputs>]

Play 2 `run-backtest` (kit/03-architecture/rote-plays.md) for run_id r<N>-<E>, as ONE command:

    make_run_db -> run_signals -> leakage_check -> read_lessons -> rank_and_seal -> score -> append_ledger
    -> refit_lessons -> store_lessons -> cognee_feedback -> drop_run_db

(notify_rocketride, the Play's last step, is Person A's script.) Every step runs that step's own CLI entry point,
oracle.<step>.main(argv), in-process, so its JSON and exit code are exactly what `python -m oracle.<step>` prints. An
earlier step's output reaches a later step as inline JSON in argv, the way a Rote step references @N. The chain
stops at the first non-zero exit, exits with THAT code and prints the failing step's JSON as `evidence`;
drop_run_db runs anyway. A degraded step (exit 0 with "warning" or "skipped") does not stop the chain.

Glue that lives only here:
  * preflight (make_run_db step): if another run_id already holds run_seq N in the ledger (`runs`, or the prefix
    r<N>- in `predictions`), exit 1. A lessons version IS a run_seq (prediction-model.md), so two runs sharing one
    seq would train one version on both.
  * read_lessons runs with --before-run-seq N and rank_and_seal takes its output (--lessons): the first run and any
    replay of run N use the same lessons, never ones learned from run N or later (sealing rule 5).
  * append_ledger gets leakage_ok from leakage_check and the measured run facts (kit/BUILDER-RULES.md §6):
      seconds           wall clock from the start of make_run_db to the end of score;
      tokens            0: no step before append calls a model. If refit_lessons' optional LLM rewording spends
                        tokens (LIVE [TEST], only with LLM_API_KEY), the runs row is re-upserted with them added;
      checks_first_try  true iff no earlier attempt of this run_id failed; every chain attempt is logged as one
                        JSON line in <LOCAL_DIR>/backtests/<run_id>.attempts.jsonl.
  * refit_lessons and store_lessons are skipped for a cold-start twin (--cold-start): the twin re-makes a prediction
    with the fixed blank weights as a comparison (prediction-model.md "Cold-start comparison"), is not evidence,
    and must not create a lessons version. (refit also leaves cold-start runs out of its training rows.)
  * cognee_feedback is a no-op offline: the module reports "skipped": "no LLM_API_KEY".
  * --seal-only stops after rank_and_seal (then drop_run_db): the reveal (D8) seals its main run AND its cold-start
    twin before either is scored. A later plain run of the same run_id is a replay: read_lessons --before-run-seq N
    and rank_and_seal's kept made_at give the same hash, and it goes on to score.
  * --frozen: beta is frozen once the reveal prediction is sealed (kit/05-build/staging-plan.md), so the run is
    scored and recorded but refit_lessons and store_lessons are skipped.

Where a variant of the run plugs in (one place each, so a new arm, feature set or signal transform needs no new glue):
  * runs that must not write a lessons version  Backtest.lessons_skip_reason() (today: a cold-start twin, and
                                                --frozen). An exam_history run (the D8 history side track) does
                                                write a version, but refit leaves it out of training (D4), so the
                                                version carries the weights the earlier full runs give;
  * the lessons a prediction is made with       step_read_lessons, handed to rank_and_seal as --lessons;
  * what the summary reports from the seal      SEAL_SUMMARY_KEYS (e.g. a standardisation tag the seal adds);
  * feature_set and any per-run transform       decided by the ordering rule inside make_run_db / run_signals /
                                                rank_and_seal (and refit_lessons for training), never in this glue.

--step NAME runs ONE step with the same parameters and prints that step's own JSON: what a Rote Play records, one
command per step. Earlier outputs are passed explicitly, each as inline JSON, a file path, or '-' (stdin, once):
  --run-db   make_run_db output, its run_db handle, or the database name   (run_signals, leakage_check, drop_run_db;
             default: the name run-<run_id>, which is ambiguous on hotdata, so pass the handle there)
  --signals  run_signals output (rank_and_seal)      --lessons  read_lessons output (rank_and_seal; optional)
  --leakage  leakage_check output, or true|false (append_ledger)
  --score    score output (append_ledger; default: the <run_id>.score.json that score wrote)
  --refit    refit_lessons output (store_lessons)
  --seconds, --tokens, --checks-first-try            append_ledger's run facts (NULL when absent)
Prints ONE JSON object. Exit 0, or the failing step's code: 1 hard fault, 3 invalid answer key, 4 leakage,
5 unsealed or tampered prediction, 6 no answer key.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import pandas as pd

from oracle import (append_ledger, cognee_feedback, config, leakage_check, make_run_db, rank_and_seal, read_lessons,
                    refit_lessons, run_signals, score, store_lessons)
from oracle.backend import DbHandle, DbRef, get_backend
from oracle.run_context import resolve_run_db

STEPS = ("make_run_db", "run_signals", "leakage_check", "read_lessons", "rank_and_seal", "score", "append_ledger",
         "refit_lessons", "store_lessons", "cognee_feedback", "drop_run_db")
SEAL_STEPS = STEPS[:STEPS.index("rank_and_seal") + 1]  # --seal-only: these, then drop_run_db
TWIN_SKIPPED = ("cold-start twin: it predicts with the blank weights and is not evidence, so no lessons version is "
                "written")
FROZEN_SKIPPED = "beta is frozen after the reveal prediction (--frozen): no lessons version is written"
SEAL_SUMMARY_KEYS = ("feature_set", "hash", "made_at", "k", "top_k", "lessons_run_seq",
                     "standardization")  # rank_and_seal -> summary
StepResult = tuple[int, dict[str, Any]]


@dataclass
class Backtest:
    """One backtest: the Play parameters, what earlier steps printed (`outputs`, by step name) and the run facts."""
    course: str
    target_exam: str
    run_seq: int
    cold_start: bool = False
    allow_machine_key: bool = False
    allow_machine_labels: bool = False
    made_at: str | None = None
    seal_only: bool = False
    frozen: bool = False
    run_db: DbRef | None = None
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    seconds: float | None = None
    tokens: int | None = None
    checks_first_try: bool | None = None

    def __post_init__(self) -> None:
        config.validate_id(self.course, what="course")
        config.validate_id(self.target_exam, what="target exam")
        if isinstance(self.run_seq, bool) or not isinstance(self.run_seq, int) or self.run_seq < 1:
            raise ValueError(f"run_seq must be an integer >= 1, got {self.run_seq!r}")

    @property
    def run_id(self) -> str:
        return f"r{self.run_seq}-{self.target_exam}"

    def run_args(self) -> list[str]:
        return ["--course", self.course, "--target-exam", self.target_exam, "--run-id", self.run_id]

    def run_db_args(self) -> list[str]:
        if isinstance(self.run_db, DbHandle):
            return ["--run-db", json.dumps(self.run_db.to_dict())]
        return ["--run-db", self.run_db] if self.run_db else []

    def lessons_skip_reason(self) -> str | None:
        """Why this run writes NO lessons version (refit_lessons and store_lessons are skipped), or None when it
        does. The one place that decides it: a run variant that must not train adds its reason here."""
        if self.cold_start:
            return TWIN_SKIPPED
        return FROZEN_SKIPPED if self.frozen else None


# ------------------------------------------------------------------ running one script in-process
def invoke(main: Callable[[list[str]], int | None], argv: Sequence[str]) -> StepResult:
    """Run a script's main(argv) in-process; return (its exit code, the ONE JSON object it printed)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            code = main(list(argv))
        except SystemExit as exc:  # a usage error: config.ScriptParser prints JSON, then exits 1
            code = exc.code if isinstance(exc.code, int) else config.EXIT_HARD
    text = buffer.getvalue()
    lines = [line for line in text.splitlines() if line.strip()]
    try:
        out = json.loads(lines[-1])
    except (IndexError, ValueError):
        out = None
    if not isinstance(out, dict):
        return (code or config.EXIT_HARD), {"ok": False, "error": "the step did not print a JSON object",
                                            "stdout_tail": text[-2000:]}
    return int(code or 0), out


def _flag(value: bool) -> str:
    return "true" if value else "false"


def _missing(step: str, what: str) -> StepResult:
    return config.EXIT_HARD, {"ok": False, "error": f"{step} needs {what}"}


# ------------------------------------------------------------------ the steps
def run_seq_clash(bt: Backtest) -> list[str]:
    """Other run_ids that already hold run_seq N in the ledger (runs.run_seq, or r<N>- in predictions.run_id)."""
    be = get_backend()
    ledger = be.ledger()
    try:
        runs = be.query(ledger, "SELECT DISTINCT run_id FROM {{runs}} WHERE run_seq = $run_seq", {"run_seq": bt.run_seq})
        preds = be.query(ledger, "SELECT DISTINCT run_id FROM {{predictions}}")
    except FileNotFoundError:  # the local ledger does not exist yet: make_run_db reports that
        return []
    prefix = f"r{bt.run_seq}-"
    ids = {str(r) for r in runs["run_id"]} | {str(r) for r in preds["run_id"] if str(r).startswith(prefix)}
    return sorted(ids - {bt.run_id})


def step_make_run_db(bt: Backtest) -> StepResult:
    clash = run_seq_clash(bt)
    if clash:
        return config.EXIT_HARD, {"ok": False, "run_id": bt.run_id, "clash": clash,
                                  "error": f"run_seq {bt.run_seq} is already used by {', '.join(clash)}; a lessons "
                                           f"version is a run_seq, so every run needs its own: use the next free one"}
    code, out = invoke(make_run_db.main, bt.run_args())
    if code == config.EXIT_OK:
        bt.run_db = DbHandle.from_dict(out["run_db"])
    return code, out


def step_run_signals(bt: Backtest) -> StepResult:
    return invoke(run_signals.main, bt.run_args() + bt.run_db_args())


def step_leakage_check(bt: Backtest) -> StepResult:
    return invoke(leakage_check.main, bt.run_args() + bt.run_db_args())


def step_read_lessons(bt: Backtest) -> StepResult:
    return invoke(read_lessons.main, ["--before-run-seq", str(bt.run_seq)])


def step_rank_and_seal(bt: Backtest) -> StepResult:
    signals = bt.outputs.get("run_signals")
    if signals is None:
        return _missing("rank_and_seal", "the run_signals output (--signals)")
    argv = ["--run-id", bt.run_id, "--course", bt.course, "--target-exam", bt.target_exam,
            "--signals", json.dumps(signals)]
    if "read_lessons" in bt.outputs:
        argv += ["--lessons", json.dumps(bt.outputs["read_lessons"])]
    if bt.cold_start:
        argv.append("--cold-start")
    if bt.made_at:
        argv += ["--made-at", bt.made_at]
    return invoke(rank_and_seal.main, argv)


def step_score(bt: Backtest) -> StepResult:
    return invoke(score.main, ["--run-id", bt.run_id] + (["--allow-machine-key"] if bt.allow_machine_key else []))


def step_append_ledger(bt: Backtest) -> StepResult:
    leakage_ok = (bt.outputs.get("leakage_check") or {}).get("leakage_ok")
    if not isinstance(leakage_ok, bool):
        return _missing("append_ledger", "leakage_ok from the leakage_check step (--leakage)")
    argv = ["--run-id", bt.run_id, "--leakage-ok", _flag(leakage_ok)]
    if "score" in bt.outputs:
        argv += ["--score-json", json.dumps(bt.outputs["score"])]
    if bt.tokens is not None:
        argv += ["--tokens", str(bt.tokens)]
    if bt.seconds is not None:
        argv += ["--seconds", repr(float(bt.seconds))]
    if bt.checks_first_try is not None:
        argv += ["--checks-first-try", _flag(bt.checks_first_try)]
    return invoke(append_ledger.main, argv)


def add_run_tokens(run_id: str, tokens: int) -> bool:
    """Add model tokens spent after append_ledger (refit's optional rewording) to the run's `runs` row, by upsert.
    False when the run has no row yet."""
    be = get_backend()
    ledger = be.ledger()
    rows = be.query(ledger, "SELECT * FROM {{runs}} WHERE run_id = $run_id", {"run_id": run_id})
    if rows.empty:
        return False
    row = rows.iloc[[0]].copy()
    current = row["tokens"].iloc[0]
    row["tokens"] = (0 if pd.isna(current) else int(current)) + int(tokens)
    be.load_table(ledger, "runs", row, mode="upsert")
    return True


def step_refit_lessons(bt: Backtest) -> StepResult:
    skip = bt.lessons_skip_reason()
    if skip:
        return config.EXIT_OK, {"ok": True, "skipped": skip, "run_id": bt.run_id}
    argv = ["--through-run-seq", str(bt.run_seq)] + (["--allow-machine-labels"] if bt.allow_machine_labels else [])
    code, out = invoke(refit_lessons.main, argv)
    tokens = (out.get("llm") or {}).get("tokens") if code == config.EXIT_OK else None
    if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0:  # LIVE [TEST]: needs LLM_API_KEY
        add_run_tokens(bt.run_id, tokens)
        bt.tokens = (bt.tokens or 0) + tokens
    return code, out


def step_store_lessons(bt: Backtest) -> StepResult:
    skip = bt.lessons_skip_reason()
    if skip:
        return config.EXIT_OK, {"ok": True, "skipped": skip, "run_id": bt.run_id}
    refit = bt.outputs.get("refit_lessons")
    if refit is None:
        return _missing("store_lessons", "the refit_lessons output (--refit)")
    return invoke(store_lessons.main, ["--lessons-json", json.dumps(refit)])


def step_cognee_feedback(bt: Backtest) -> StepResult:
    return invoke(cognee_feedback.main, ["--run-id", bt.run_id])


def step_drop_run_db(bt: Backtest) -> StepResult:
    """Delete the run database. Degraded, never hard: a hotdata run database also expires on its own."""
    if bt.run_db is None:
        return config.EXIT_OK, {"ok": True, "skipped": "no run database was created", "run_id": bt.run_id}
    name = bt.run_db.name if isinstance(bt.run_db, DbHandle) else bt.run_db
    try:
        get_backend().drop_run_db(bt.run_db)
    except Exception as exc:
        return config.EXIT_OK, {"ok": True, "run_db": name,
                                "warning": f"could not drop the run database: {type(exc).__name__}: {exc}"}
    return config.EXIT_OK, {"ok": True, "dropped": name}


STEP_FUNCS: dict[str, Callable[[Backtest], StepResult]] = {
    "make_run_db": step_make_run_db, "run_signals": step_run_signals, "leakage_check": step_leakage_check,
    "read_lessons": step_read_lessons, "rank_and_seal": step_rank_and_seal, "score": step_score,
    "append_ledger": step_append_ledger, "refit_lessons": step_refit_lessons, "store_lessons": step_store_lessons,
    "cognee_feedback": step_cognee_feedback, "drop_run_db": step_drop_run_db,
}
if tuple(STEP_FUNCS) != STEPS:
    raise RuntimeError("STEP_FUNCS must list STEPS in order")


def run_step(bt: Backtest, name: str) -> StepResult:
    """Run one step; its output is kept in bt.outputs[name]. A failure of the glue itself is a hard fault."""
    try:
        code, out = STEP_FUNCS[name](bt)
    except Exception as exc:  # e.g. the ledger is unreadable during preflight
        code, out = config.EXIT_HARD, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    bt.outputs[name] = out
    return code, out


# ------------------------------------------------------------------ the chain
def attempts_path(run_id: str) -> Path:
    return config.get_settings().local_dir / "backtests" / f"{config.validate_id(run_id, what='run_id')}.attempts.jsonl"


def read_attempts(run_id: str) -> list[dict[str, Any]]:
    path = attempts_path(run_id)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _record_attempt(run_id: str, entry: dict[str, Any]) -> None:
    path = attempts_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def _note(out: dict[str, Any]) -> dict[str, Any]:
    return {key: out[key] for key in ("warning", "skipped") if out.get(key)}


def run_backtest(bt: Backtest) -> StepResult:
    """The whole chain (module docstring). Returns (exit code, summary with every step's output)."""
    bt.checks_first_try = all(a.get("exit_code") == config.EXIT_OK for a in read_attempts(bt.run_id))
    bt.tokens = 0
    started, started_at = time.monotonic(), datetime.now(timezone.utc).isoformat(timespec="seconds")
    steps: list[dict[str, Any]] = []
    failed: str | None = None
    exit_code = config.EXIT_OK

    def run(name: str) -> int:
        t0 = time.monotonic()
        code, out = run_step(bt, name)
        steps.append({"step": name, "exit_code": code, "seconds": round(time.monotonic() - t0, 3), **_note(out)})
        return code

    try:
        for name in (SEAL_STEPS if bt.seal_only else STEPS[:-1]):
            if name == "append_ledger":
                bt.seconds = round(time.monotonic() - started, 3)
            code = run(name)
            if code != config.EXIT_OK:
                failed, exit_code = name, code
                break
    finally:
        run("drop_run_db")

    seal = bt.outputs.get("rank_and_seal") or {}
    scored = bt.outputs.get("score") or {}
    stored = bt.outputs.get("store_lessons") or {}
    summary: dict[str, Any] = {
        "ok": failed is None, "run_id": bt.run_id, "run_seq": bt.run_seq, "course": bt.course,
        "target_exam": bt.target_exam, "cold_start": bt.cold_start, "seal_only": bt.seal_only, "frozen": bt.frozen,
        "exit_code": exit_code, "failed_step": failed,
        **{key: seal.get(key) for key in SEAL_SUMMARY_KEYS},
        "key_source": scored.get("key_source"),
        "scores": {m: scored[m] for m in append_ledger.METRICS if m in scored} or None,
        "lessons_written": stored.get("run_seq") if stored.get("ok") and not stored.get("skipped") else None,
        "seconds": bt.seconds, "total_seconds": round(time.monotonic() - started, 3), "tokens": bt.tokens,
        "checks_first_try": bt.checks_first_try, "steps": steps,
        "warnings": {s: o["warning"] for s, o in bt.outputs.items() if o.get("warning")},
        "outputs": bt.outputs,
    }
    if failed:
        summary["evidence"] = bt.outputs[failed]
    _record_attempt(bt.run_id, {"started_at": started_at, "exit_code": exit_code, "failed_step": failed,
                                "hash": seal.get("hash"), "seal_only": bt.seal_only})
    return exit_code, summary


# ------------------------------------------------------------------ CLI
STEP_INPUTS = ("run_db", "signals", "lessons", "leakage", "score", "refit", "seconds", "tokens", "checks_first_try")


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be an integer >= 1, got {value}")
    return value


def _bool(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {text!r}")


def _json_object(value: str, flag: str) -> dict[str, Any]:
    data = rank_and_seal.read_json_arg(value)
    if not isinstance(data, dict):
        raise ValueError(f"{flag} must be one JSON object (an earlier step's output)")
    return data


def load_step_inputs(bt: Backtest, args: argparse.Namespace) -> None:
    """Put the explicitly passed earlier outputs where the steps look for them (module docstring)."""
    if sum(getattr(args, name) == "-" for name in STEP_INPUTS[:6]) > 1:
        raise ValueError("only one step input can be read from stdin ('-')")
    bt.run_db = resolve_run_db(args.run_db, bt.run_id)
    for flag, step in (("signals", "run_signals"), ("lessons", "read_lessons"), ("score", "score"),
                       ("refit", "refit_lessons")):
        if getattr(args, flag):
            bt.outputs[step] = _json_object(getattr(args, flag), f"--{flag}")
    if args.leakage:
        text = args.leakage.strip().lower()
        leak = {"leakage_ok": text == "true"} if text in ("true", "false") else _json_object(args.leakage, "--leakage")
        if not isinstance(leak.get("leakage_ok"), bool):
            raise ValueError("--leakage needs the leakage_check output (with leakage_ok) or true|false")
        bt.outputs["leakage_check"] = leak
    bt.seconds, bt.tokens, bt.checks_first_try = args.seconds, args.tokens, args.checks_first_try


def build_parser() -> config.ScriptParser:
    parser = config.ScriptParser(prog="python -m oracle.backtest",
                                 description="Play 2 run-backtest as one command, or one --step of it. "
                                             "Prints one JSON object.")
    parser.add_argument("--course", required=True, help="course code, e.g. 6.003")
    parser.add_argument("--target-exam", required=True, help="exam_id of the target, e.g. 6.003-final-2011F")
    parser.add_argument("--run-seq", required=True, type=_positive_int, help="global run order N; run_id = r<N>-<E>")
    parser.add_argument("--cold-start", action="store_true", help="the blank-weights twin (no refit, no store)")
    parser.add_argument("--allow-machine-key", action="store_true",
                        help="without a human key, score against the ledger's tags (key_source = machine)")
    parser.add_argument("--allow-machine-labels", action="store_true", help="let refit train on machine-keyed runs")
    parser.add_argument("--made-at", help="ISO-8601 seal timestamp with offset (default now; a replay keeps the sealed one)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--seal-only", action="store_true",
                      help="stop after rank_and_seal (the reveal: seal main and twin before scoring either)")
    mode.add_argument("--frozen", action="store_true", help="beta is frozen: score and record, write no lessons version")
    parser.add_argument("--step", choices=STEPS, help="run only this step and print its own JSON")
    inputs = parser.add_argument_group("step inputs (only with --step): inline JSON, a file path, or '-'")
    inputs.add_argument("--run-db", help="make_run_db output, its run_db handle, or the database name")
    inputs.add_argument("--signals", help="run_signals output (rank_and_seal)")
    inputs.add_argument("--lessons", help="read_lessons output (rank_and_seal)")
    inputs.add_argument("--leakage", help="leakage_check output, or true|false (append_ledger)")
    inputs.add_argument("--score", help="score output (append_ledger)")
    inputs.add_argument("--refit", help="refit_lessons output (store_lessons)")
    inputs.add_argument("--seconds", type=float, help="append_ledger: wall-clock seconds of the run")
    inputs.add_argument("--tokens", type=int, help="append_ledger: model tokens spent")
    inputs.add_argument("--checks-first-try", type=_bool, help="append_ledger: true|false")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.step is None and any(getattr(args, name) is not None for name in STEP_INPUTS):
        parser.error("step inputs (--run-db, --signals, ...) are only used with --step")
    if args.step is not None and args.seal_only:
        parser.error("--seal-only selects steps of the chain; with --step, run just the steps you want")
    try:
        bt = Backtest(args.course, args.target_exam, args.run_seq, cold_start=args.cold_start,
                      allow_machine_key=args.allow_machine_key, allow_machine_labels=args.allow_machine_labels,
                      made_at=args.made_at, seal_only=args.seal_only, frozen=args.frozen)
        if args.step:
            load_step_inputs(bt, args)
            code, out = run_step(bt, args.step)
        else:
            code, out = run_backtest(bt)
    except Exception as exc:  # bad parameters or step inputs: a hard fault, one JSON object
        config.emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return config.EXIT_HARD
    config.emit(out)
    return code


if __name__ == "__main__":
    sys.exit(main())
