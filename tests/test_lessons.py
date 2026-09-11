"""Learning-loop tests: oracle.refit_lessons, oracle.store_lessons, oracle.read_lessons (Play 2 steps 4, 8, 9).

Offline only. Every ledger and lessons store lives under tmp_path (one guard test also reads the shared FX.101
fixture ledger). The optional LLM hook runs against a fake whose calls are bound to the real openai SDK
signature, so a wrong keyword fails here instead of on stage."""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import warnings
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from openai.resources.chat.completions import Completions
from sklearn.linear_model import LogisticRegression

from oracle import config, read_lessons, refit_lessons, store_lessons
from oracle.backend import LocalDuckDBBackend, get_backend
from oracle.lessons_store import (COLD_START_WEIGHTS, FEATURES, SIGNALS, LocalJSONLessonsStore, cold_start_record,
                                  make_record, to_ledger_rows)

TRUE_BETA = {"intercept": -2.5, "x3": 5.0}  # homework echo (x3) truly drives y; x1, x4 and x5 are pure noise
MADE_AT = "2026-09-11T09:00:00-07:00"
RECORD_KEYS = ("run_seq", "weights", "statements", "supporting_runs")


# ------------------------------------------------------------------ helpers
def _loop(tmp_path, name: str = "lb") -> SimpleNamespace:
    """A private local ledger + lessons store under tmp_path/<name>."""
    be = LocalDuckDBBackend(tmp_path / name, "ledger")
    return SimpleNamespace(be=be, ledger=be.ensure_ledger(),
                           store=LocalJSONLessonsStore(tmp_path / name / "lessons.jsonl"))


@pytest.fixture
def loop(tmp_path) -> SimpleNamespace:
    return _loop(tmp_path)


def _run(seq: int, rng: np.random.Generator, *, n_topics: int = 15, course: str = "FX.101",
         beta: dict = TRUE_BETA, cold_start: bool = False, key_source: str = "human",
         feature_set: str = "full") -> dict:
    """One synthetic scored run: signals for n_topics topics, labels drawn from `beta`. x2, x6, x7 stay 0."""
    X = np.zeros((n_topics, len(FEATURES)))
    X[:, 0] = rng.uniform(0, 1, n_topics)      # x1
    X[:, 2] = rng.uniform(0, 1, n_topics)      # x3
    X[:, 3] = rng.uniform(0, 0.2, n_topics)    # x4, on a lecture-share scale
    X[:, 4] = rng.integers(0, 2, n_topics)     # x5, a flag
    z = beta["intercept"] + sum(beta.get(f, 0.0) * X[:, i] for i, f in enumerate(FEATURES))
    y = (rng.uniform(size=n_topics) < 1.0 / (1.0 + np.exp(-z))).astype(int)
    exam_id = f"{course}-final-{2000 + seq}F"
    return {"run_id": f"r{seq}-{exam_id}", "run_seq": seq, "exam_id": exam_id, "course": course,
            "cold_start": cold_start, "key_source": key_source, "feature_set": feature_set, "X": X, "y": y}


def _seed(be, ledger, runs: list[dict]) -> None:
    """Load runs, predictions and run_labels rows (exact contract columns) for synthetic runs."""
    run_rows, pred_rows, label_rows = [], [], []
    for r in runs:
        run_rows.append({"run_id": r["run_id"], "run_seq": r["run_seq"], "course": r["course"],
                         "target_exam": r["exam_id"], "feature_set": r["feature_set"], "cold_start": r["cold_start"],
                         "k": 4, "model_pts": 0.5, "even_pts": 0.4, "lastexam_pts": 0.45, "recall_k": 0.5,
                         "brier": 0.2, "tokens": 0, "seconds": 1.0, "checks_first_try": True, "leakage_ok": True,
                         "key_source": r["key_source"], "lessons_run_seq": None, "hash": "a" * 64,
                         "made_at": MADE_AT})
        for i, (x, y) in enumerate(zip(r["X"], r["y"])):
            topic_id = f"T{i + 1:02d}"
            pred_rows.append({"run_id": r["run_id"], "exam_id": r["exam_id"], "topic_id": topic_id, "p": 0.5,
                              "rank": i + 1, "in_top_k": i < 4, **{f: float(v) for f, v in zip(FEATURES, x)},
                              "hash": "a" * 64, "made_at": MADE_AT, "feature_set": r["feature_set"]})
            label_rows.append({"run_id": r["run_id"], "topic_id": topic_id, "y": int(y),
                               "key_points": 0.1 * int(y), "key_source": r["key_source"]})
    be.load_table(ledger, "runs", run_rows, mode="upsert")
    be.load_table(ledger, "predictions", pred_rows, mode="upsert")
    be.load_table(ledger, "run_labels", label_rows, mode="upsert")


def _frame(run: dict) -> pd.DataFrame:
    """A synthetic run as refit() training rows."""
    frame = pd.DataFrame(run["X"], columns=list(FEATURES))
    frame.insert(0, "run_id", run["run_id"])
    frame.insert(1, "course", run["course"])
    frame["y"] = run["y"]
    return frame


def _refit(lp: SimpleNamespace, n: int, **kwargs) -> dict:
    return refit_lessons.run_refit(n, backend=lp.be, ledger=lp.ledger, store=lp.store, use_llm=False, **kwargs)


def _store(lp: SimpleNamespace, out: dict) -> dict:
    return store_lessons.store_lessons(out, store=lp.store, backend=lp.be, ledger=lp.ledger)


def _record(obj: dict) -> dict:
    return {k: obj[k] for k in RECORD_KEYS}


# ------------------------------------------------------------------ refit
@pytest.mark.parametrize("seed", [1, 4, 7])
def test_refit_moves_a_predictive_weight_up(loop, seed):
    rng = np.random.default_rng(seed)
    runs = [_run(seq, rng, n_topics=20) for seq in range(1, 9)]
    _seed(loop.be, loop.ledger, runs)
    out = _refit(loop, 8)
    w = out["weights"]
    assert out["ok"] and out["refit"] and out["reason"] is None and "warning" not in out
    assert out["evidence"]["n_runs"] == 8 and out["evidence"]["n_rows"] == 160
    assert out["evidence"]["rows_by_feature_set"] == {"full": 160}
    assert out["supporting_runs"] == [r["run_id"] for r in runs]
    assert w["x3"] > COLD_START_WEIGHTS["x3"] + 1.5              # the truly predictive signal moved up
    assert w["x3"] == max(w[f] for f in FEATURES)
    assert max(abs(w[f]) for f in ("x1", "x4", "x5")) < w["x3"] / 4   # pure-noise signals stay small
    assert out["kept_previous"] == ["x2", "x6", "x7"]            # never varied: no evidence either way
    assert all(w[f] == COLD_START_WEIGHTS[f] for f in out["kept_previous"])
    assert out["previous"] == {"run_seq": None, "source": "cold_start", "weights": COLD_START_WEIGHTS}
    assert out["statements"]["x3"].startswith("Homework echo (x3) is the strongest positive signal so far")
    assert "from 8 runs in 1 course" in out["statements"]["x3"]
    assert "carry no evidence" in out["statements"]["x2"]
    assert out["model"]["C"] == 0.5 and 0 < out["model"]["n_iter"] < refit_lessons.MAX_ITER


def test_logistic_model_is_the_spec_model():
    """C=0.5, L2, lbfgs, intercept: identical to penalty='l2', without scikit-learn >= 1.8's deprecation warning."""
    if "penalty" not in inspect.signature(LogisticRegression).parameters:
        pytest.skip("this scikit-learn no longer accepts penalty='l2' to compare against")
    rng = np.random.default_rng(3)
    X = rng.uniform(0, 1, (60, 3))
    y = (X[:, 0] + 0.3 * rng.standard_normal(60) > 0.5).astype(int)
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        ours = refit_lessons.logistic_model().fit(X, y)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        spec = LogisticRegression(C=0.5, penalty="l2", solver="lbfgs", fit_intercept=True).fit(X, y)
    assert (ours.C, ours.solver, ours.fit_intercept) == (0.5, "lbfgs", True)
    assert np.allclose(ours.coef_, spec.coef_) and np.allclose(ours.intercept_, spec.intercept_)


def test_a_constant_signal_keeps_its_previous_weight_and_the_training_log_odds():
    rng = np.random.default_rng(11)
    rows = pd.concat([_frame(_run(seq, rng)) for seq in (1, 2, 3)], ignore_index=True)
    rows["x2"] = 1.0                                             # e.g. cumulative finals: every topic in the window
    previous = {**cold_start_record(), "weights": {**COLD_START_WEIGHTS, "x2": 0.7}}
    result = refit_lessons.refit(rows, previous)
    assert result["refit"] and result["kept_previous"] == ["x2", "x6", "x7"] and result["weights"]["x2"] == 0.7
    varying = [f for f in FEATURES if f not in result["kept_previous"]]
    reduced = refit_lessons.logistic_model().fit(rows[varying].to_numpy(), rows["y"].to_numpy())
    beta = np.array([result["weights"][f] for f in FEATURES])
    z_ours = result["weights"]["intercept"] + rows[list(FEATURES)].to_numpy() @ beta
    z_reduced = reduced.intercept_[0] + rows[varying].to_numpy() @ reduced.coef_[0]
    assert np.allclose(z_ours, z_reduced)                        # the same fit; x2's prior weight is not erased


def test_guards_keep_the_previous_weights_and_say_why(tmp_path, fixture_ledger):
    be, fx = fixture_ledger                                      # the FX.101 fixture: runs exist, none scored yet
    out = refit_lessons.run_refit(3, backend=be, ledger=fx, store=LocalJSONLessonsStore(tmp_path / "l.jsonl"),
                                  use_llm=False)
    assert not out["refit"] and out["weights"] == COLD_START_WEIGHTS
    assert out["reason"] == "no scored runs to learn from yet" and out["warning"].startswith("weights kept")
    assert out["statements"]["x3"] == "Homework echo (x3) kept at +0.50: no scored runs to learn from yet."

    rng = np.random.default_rng(5)
    one = _loop(tmp_path, "one")
    _seed(one.be, one.ledger, [_run(1, rng)])
    out = _refit(one, 1)
    assert not out["refit"] and out["reason"].startswith("only 1 scored run") and out["weights"] == COLD_START_WEIGHTS

    single = _loop(tmp_path, "single")
    earlier = single.store.write(2, {**COLD_START_WEIGHTS, "x1": 0.9}, {}, ["r1-FX.101-final-2001F"])
    runs = [_run(seq, rng) for seq in (1, 2, 3)]
    for r in runs:
        r["y"][:] = 1
    _seed(single.be, single.ledger, runs)
    out = _refit(single, 3)
    assert not out["refit"] and "y=1" in out["reason"] and out["previous"]["run_seq"] == 2
    assert out["weights"] == earlier["weights"] and out["supporting_runs"] == earlier["supporting_runs"]

    flat = _loop(tmp_path, "flat")
    runs = [_run(seq, rng) for seq in (1, 2)]
    for r in runs:
        r["X"][:] = 0.0
        r["y"][:2] = [0, 1]
    _seed(flat.be, flat.ledger, runs)
    out = _refit(flat, 2)
    assert not out["refit"] and out["reason"] == "no signal varies across the training rows"


def test_cold_start_twins_and_machine_keys_are_not_evidence(tmp_path):
    rng = np.random.default_rng(9)
    base = [_run(seq, rng) for seq in (1, 2, 3, 4)]
    flipped = {"intercept": 2.0, "x3": -6.0}                     # would drag x3 down if it were used
    twin = _run(5, rng, beta=flipped, cold_start=True)
    machine = _run(6, rng, beta=flipped, key_source="machine")
    clean, mixed = _loop(tmp_path, "clean"), _loop(tmp_path, "mixed")
    _seed(clean.be, clean.ledger, base)
    _seed(mixed.be, mixed.ledger, base + [twin, machine])
    reference, out = _refit(clean, 6), _refit(mixed, 6)
    assert out["weights"] == pytest.approx(reference["weights"])
    assert out["supporting_runs"] == [r["run_id"] for r in base]
    assert out["left_out"] == {"cold_start_runs": [twin["run_id"]], "machine_label_runs": [machine["run_id"]]}
    assert "run_seq 6 contributed no training rows" in out["warning"]
    opted = _refit(mixed, 6, allow_machine_labels=True)
    assert opted["supporting_runs"] == [r["run_id"] for r in base] + [machine["run_id"]]
    assert opted["left_out"]["cold_start_runs"] == [twin["run_id"]]
    assert opted["weights"]["x3"] < out["weights"]["x3"]


# ------------------------------------------------------------------ store + read
def test_store_and_read_round_trip_local(loop):
    rng = np.random.default_rng(7)
    _seed(loop.be, loop.ledger, [_run(seq, rng) for seq in (1, 2, 3)])
    out = _refit(loop, 3)
    stored = _store(loop, out)
    assert stored["ok"] and stored["lessons_store"] == "local" and stored["ledger_rows"] == len(SIGNALS)
    got = read_lessons.resolve_lessons(store=loop.store, backend=loop.be, ledger=loop.ledger)
    assert got["source"] == "lessons_store" and _record(got) == _record(out) == _record(stored)
    mirror = loop.be.query(loop.ledger, "SELECT signal, weight, statement, supporting_runs FROM {{lessons}} "
                                        "WHERE run_seq = $n", {"n": 3})
    assert dict(zip(mirror.signal, mirror.weight)) == out["weights"]
    assert dict(zip(mirror.signal, mirror.statement)) == out["statements"]
    assert set(mirror.supporting_runs) == {";".join(out["supporting_runs"])}
    assert read_lessons.ledger_versions(loop.be, loop.ledger) == [_record(out)]  # the synchronous copy agrees
    _store(loop, out)                                                             # a Rote resume re-runs the step
    assert len(loop.be.query(loop.ledger, "SELECT * FROM {{lessons}}")) == len(SIGNALS)
    before = read_lessons.resolve_lessons(3, store=loop.store, backend=loop.be, ledger=loop.ledger)
    assert before["source"] == "cold_start" and before["weights"] == COLD_START_WEIGHTS
    assert read_lessons.resolve_lessons(4, store=loop.store, backend=loop.be, ledger=loop.ledger)["run_seq"] == 3


def test_weights_change_between_two_refits_and_a_replay_is_deterministic(loop):
    rng = np.random.default_rng(21)
    early = [_run(seq, rng) for seq in (1, 2, 3)]
    late = [_run(seq, rng, beta={"intercept": -3.0, "x1": 4.0, "x3": 5.0}) for seq in (4, 5, 6)]
    _seed(loop.be, loop.ledger, early + late)                     # refit through 3 must see only runs 1..3
    first = _refit(loop, 3)
    assert first["refit"] and first["supporting_runs"] == [r["run_id"] for r in early]
    _store(loop, first)
    second = _refit(loop, 6)
    assert (second["previous"]["run_seq"], second["previous"]["source"]) == (3, "lessons_store")
    assert second["evidence"]["n_runs"] == 6 and second["weights"] != first["weights"]
    assert second["weights"]["x1"] > first["weights"]["x1"]      # the later runs made track record matter
    assert f"(was {refit_lessons._num(first['weights']['x1'])})" in second["statements"]["x1"]
    _store(loop, second)
    assert read_lessons.resolve_lessons(store=loop.store, backend=loop.be, ledger=loop.ledger)["run_seq"] == 6
    replay = _refit(loop, 3)                                      # resume run 3 after version 6 exists
    assert _record(replay) == _record(first) and replay["previous"]["source"] == "cold_start"


class PointerOnlyStore:
    """Serves only its latest version, as HydraDBLessonsStore does (no history)."""

    def __init__(self, record: dict) -> None:
        self.record = record

    def read_latest(self) -> dict:
        return self.record

    def write(self, run_seq, weights, statements, supporting_runs):
        raise AssertionError("read-only in this test")


def test_read_lessons_takes_the_newest_version_from_store_or_ledger(loop, tmp_path):
    v2 = make_record(2, {**COLD_START_WEIGHTS, "x1": 0.7}, {"x1": "store v2"}, [])
    v3 = make_record(3, {**COLD_START_WEIGHTS, "x1": 0.9}, {"x1": "ledger v3"}, [])
    loop.be.load_table(loop.ledger, "lessons", to_ledger_rows(v3), mode="upsert")
    lagging = PointerOnlyStore(v2)                                # HydraDB still serving v2 (ingest is 202-accepted)
    got = read_lessons.resolve_lessons(store=lagging, backend=loop.be, ledger=loop.ledger)
    assert (got["run_seq"], got["source"], got["statements"]) == (3, "ledger", {"x1": "ledger v3"})
    got = read_lessons.resolve_lessons(3, store=lagging, backend=loop.be, ledger=loop.ledger)
    assert (got["run_seq"], got["source"], got["weights"]["x1"]) == (2, "lessons_store", 0.7)
    tie = read_lessons.resolve_lessons(store=PointerOnlyStore({**v3, "statements": {"x1": "store v3"}}),
                                       backend=loop.be, ledger=loop.ledger)
    assert (tie["source"], tie["statements"]) == ("lessons_store", {"x1": "store v3"})
    missing = LocalDuckDBBackend(tmp_path / "no-ledger-yet", "ledger")   # never created: holds no versions
    assert read_lessons.resolve_lessons(store=lagging, backend=missing)["run_seq"] == 2
    partial = [row for row in to_ledger_rows(make_record(4, COLD_START_WEIGHTS, {}, [])) if row["signal"] != "x7"]
    loop.be.load_table(loop.ledger, "lessons", partial, mode="upsert")
    with pytest.raises(ValueError, match="inconsistent"):         # a corrupt mirror fails loudly
        read_lessons.resolve_lessons(store=lagging, backend=loop.be, ledger=loop.ledger)


# ------------------------------------------------------------------ optional LLM hook (fake client)
class FakeCompletions:
    """Stands in for openai chat.completions; each call is bound against the real Completions.create first."""

    def __init__(self, reply: str | None = None, error: Exception | None = None) -> None:
        self.reply, self.error, self.calls = reply, error, []

    def create(self, **kwargs):
        inspect.signature(Completions.create).bind(self, **kwargs)
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))],
                               usage=SimpleNamespace(total_tokens=42))


def test_llm_hook_is_off_without_a_key_and_guarded_when_on(loop, monkeypatch):
    for name in ("LLM_MODEL", "LLM_PROVIDER", "LLM_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    rng = np.random.default_rng(7)
    _seed(loop.be, loop.ledger, [_run(seq, rng) for seq in (1, 2, 3)])

    def refit_with(fake: FakeCompletions) -> dict:
        return refit_lessons.run_refit(3, backend=loop.be, ledger=loop.ledger, store=loop.store, use_llm=True,
                                       complete=fake.create)

    never = FakeCompletions("{}")
    base = refit_with(never)                                      # LLM_API_KEY is unset in tests: hook stays off
    assert not never.calls and base["llm"]["used"] is False and "warning" not in base
    templates = base["statements"]

    monkeypatch.setenv("LLM_API_KEY", "test-key-not-real")
    fake = FakeCompletions(json.dumps({"x1": "In short: " + templates["x1"],
                                       "x3": templates["x3"] + " It is 99% certain.",   # adds a number
                                       "x4": "<b>" + templates["x4"] + "</b>"}))          # markup
    out = refit_with(fake)
    call = fake.calls[0]
    assert call["model"] == "gpt-5-mini"                          # Cognee's default model, provider prefix stripped
    assert json.loads(call["messages"][1]["content"]) == templates  # the LLM sees the template sentences only
    assert out["statements"] == {**templates, "x1": "In short: " + templates["x1"]}
    assert out["llm"]["accepted"] == ["x1"] and {"x3", "x4", "intercept"} <= set(out["llm"]["rejected"])
    assert out["llm"]["tokens"] == 42 and out["weights"] == base["weights"]   # the LLM never touches weights

    broken = refit_with(FakeCompletions(error=RuntimeError("service down")))
    assert broken["ok"] and broken["statements"] == templates and "LLM rewording failed" in broken["warning"]

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")               # unreachable without an OpenAI-compatible endpoint
    skipped = FakeCompletions("{}")
    out = refit_with(skipped)
    assert not skipped.calls and out["statements"] == templates and "LLM_ENDPOINT" in out["warning"]


# ------------------------------------------------------------------ CLI conventions
def test_cli_scripts_print_one_json_object_and_use_the_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ORACLE_LOCAL_DIR", str(tmp_path / "cli"))
    be = get_backend()
    _seed(be, be.ensure_ledger(), [_run(seq, np.random.default_rng(seq)) for seq in (1, 2, 3)])

    def run(main, *argv) -> tuple[int, dict]:
        code = main(list(argv))
        lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
        assert len(lines) == 1, lines
        return code, json.loads(lines[0])

    code, refit_out = run(refit_lessons.main, "--through-run-seq", "3", "--no-llm")
    assert code == config.EXIT_OK and refit_out["ok"] and refit_out["refit"] and refit_out["run_seq"] == 3
    code, stored = run(store_lessons.main, "--lessons-json", json.dumps(refit_out))
    assert code == config.EXIT_OK and stored["ledger_rows"] == len(SIGNALS)
    code, current = run(read_lessons.main)
    assert code == config.EXIT_OK and not current["cold_start"] and _record(current) == _record(refit_out)
    code, before = run(read_lessons.main, "--before-run-seq", "3")
    assert code == config.EXIT_OK and before["cold_start"] and before["weights"] == COLD_START_WEIGHTS
    saved = tmp_path / "refit.json"
    saved.write_text(json.dumps(refit_out), encoding="utf-8")
    assert run(store_lessons.main, "--input", str(saved))[0] == config.EXIT_OK

    usage_errors = [(refit_lessons.main, []), (refit_lessons.main, ["--through-run-seq", "-1"]),
                    (read_lessons.main, ["--before-run-seq", "x"]), (store_lessons.main, [])]
    for main, argv in usage_errors:                               # argparse's own 2 would read as EXIT_SKIP_LIST
        code, obj = run(main, *argv)
        assert code == config.EXIT_HARD and obj["ok"] is False and obj["error"].startswith("invalid arguments")
    for bad in ("[1, 2]", "not json", json.dumps({**refit_out, "run_seq": None}),
                json.dumps({"ok": False, "error": "refit failed"})):
        code, obj = run(store_lessons.main, "--lessons-json", bad)
        assert code == config.EXIT_HARD and obj["ok"] is False


def test_scripts_run_as_modules(tmp_path):
    env = {**os.environ, "ORACLE_SKIP_DOTENV": "1", "ORACLE_LOCAL_DIR": str(tmp_path)}

    def run(module: str, *argv: str) -> tuple[int, dict]:
        done = subprocess.run([sys.executable, "-m", module, *argv], cwd=config.REPO_ROOT, env=env,
                              capture_output=True, text=True, timeout=120)
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        assert len(lines) == 1, done.stdout + done.stderr
        return done.returncode, json.loads(lines[0])

    code, obj = run("oracle.read_lessons")
    assert code == config.EXIT_OK and obj["cold_start"] and obj["source"] == "cold_start"
    assert obj["weights"] == COLD_START_WEIGHTS
    code, obj = run("oracle.refit_lessons")                       # missing --through-run-seq
    assert code == config.EXIT_HARD and obj["ok"] is False
