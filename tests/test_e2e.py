"""End to end, offline: fixture ledger -> echo_pairs -> the FX.101 run list through oracle.run_list and
oracle.backtest (Play 2), in LOCAL mode.

A spy (a Python audit hook on "open") records every open of a file in the sealed folder together with the run being
scored at that moment and whether that run's <run_id>.sha256 already existed, so "the answer key is read only after
the prediction hash is written" (contracts/README.md "Ground truth") is checked from outside the code under test.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pandas as pd
import pytest

from conftest import FIXTURE_SEALED_DIR, build_fixture_ledger
from oracle import backtest, config, echo, make_run_db, run_list
from oracle import rank_and_seal as rs
from oracle import score as sc
from oracle.backend import columns
from oracle.lessons_store import COLD_START_WEIGHTS, SIGNALS, get_lessons_store

COURSE = "FX.101"
FIXTURE_LIST = config.DATA_DIR / "run_lists" / "fixture.csv"
TEMPLATE_LIST = config.DATA_DIR / "run_lists" / "REAL_TEMPLATE.csv"
QUIZ21, FINAL21, QUIZ22, FINAL22 = (f"{COURSE}-quiz1-2021F", f"{COURSE}-final-2021F", f"{COURSE}-quiz1-2022F",
                                    f"{COURSE}-final-2022F")
RUN_IDS = [f"r1-{QUIZ21}", f"r2-{FINAL21}", f"r3-{QUIZ22}", f"r4-{QUIZ22}", f"r5-{FINAL22}"]
TWIN = f"r4-{QUIZ22}"
REVEAL_KEY = f"answer_key_{FINAL22}.csv"
METRICS = ("model_pts", "even_pts", "lastexam_pts", "recall_k", "brier")
MADE_AT = "2026-09-11T13:00:00-07:00"

# ------------------------------------------------------------------ the sealed-key spy
_SPY: dict = {"active": False, "busy": False, "run": None, "events": []}
_SEALED_REAL = os.path.realpath(FIXTURE_SEALED_DIR)


def _audit(event: str, args: tuple) -> None:
    if event != "open" or not _SPY["active"] or _SPY["busy"] or not args:
        return
    path = args[0]
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    if not isinstance(path, (str, os.PathLike)):
        return
    _SPY["busy"] = True
    try:
        real = os.path.realpath(os.fspath(path))
        if os.path.dirname(real) == _SEALED_REAL:
            run = _SPY["run"]
            hash_file = rs.prediction_paths(run)[1] if run else None
            _SPY["events"].append({"file": os.path.basename(real), "run": run,
                                   "hash_existed": bool(hash_file and hash_file.is_file())})
    finally:
        _SPY["busy"] = False


sys.addaudithook(_audit)  # audit hooks cannot be removed; it only records while _SPY["active"]


# ------------------------------------------------------------------ helpers
def _fresh_lab(monkeypatch, folder) -> tuple:
    """ORACLE_LOCAL_DIR=folder with the fixture ledger and freshly computed echo_pairs (made-up rows pruned)."""
    monkeypatch.setenv("ORACLE_LOCAL_DIR", str(folder))
    be, ledger = build_fixture_ledger()
    summary = echo.run(COURSE, prune=True)
    assert summary["ok"] and summary["load_mode"] == "replace" and summary["pairs"] > 0
    return be, ledger


@pytest.fixture
def lab(tmp_path, monkeypatch):
    return _fresh_lab(monkeypatch, tmp_path / "lb")


def cli(main, argv, capsys) -> tuple[int, dict]:
    try:
        code = main(list(argv))
    except SystemExit as exc:
        code = exc.code
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1, lines                     # ONE JSON object on stdout
    return code, json.loads(lines[0])


def _write(path, text: str):
    path.write_text(text, encoding="utf-8")
    return path


# ================================================================== the whole fixture run list
def test_fixture_run_list_end_to_end(lab, monkeypatch, capsys):
    be, ledger = lab
    real_score_run = sc.score_run

    def tracked_score_run(run_id, **kwargs):
        _SPY["run"] = run_id
        try:
            return real_score_run(run_id, **kwargs)
        finally:
            _SPY["run"] = None

    monkeypatch.setattr(sc, "score_run", tracked_score_run)
    _SPY.update(active=True, run=None, events=[])
    try:
        code, out = cli(run_list.main, ["--list", str(FIXTURE_LIST)], capsys)
        with open(FIXTURE_SEALED_DIR / REVEAL_KEY, encoding="utf-8"):  # control: the spy sees an unguarded open
            pass
    finally:
        _SPY["active"] = False
    assert code == config.EXIT_OK and out["ok"], out
    assert [r["run_id"] for r in out["runs"]] == RUN_IDS and all(r["exit_code"] == 0 for r in out["runs"])

    # sealing: every answer key was opened only while its own run was scored, after its hash file existed
    events = _SPY["events"]
    assert events[-1] == {"file": REVEAL_KEY, "run": None, "hash_existed": False}
    events = events[:-1]
    assert {e["run"] for e in events} == set(RUN_IDS), events
    for e in events:
        assert e["hash_existed"] and e["file"] == f"answer_key_{e['run'].split('-', 1)[1]}.csv", e
    assert REVEAL_KEY in {e["file"] for e in events}

    # the ledger tables have exactly the contract columns
    preds = be.query(ledger, "SELECT * FROM {{predictions}} ORDER BY run_id, rank")
    runs = be.query(ledger, "SELECT * FROM {{runs}} ORDER BY run_seq")
    labels = be.query(ledger, "SELECT * FROM {{run_labels}}")
    for table, frame in (("predictions", preds), ("runs", runs), ("run_labels", labels)):
        assert list(frame.columns) == [c.name for c in columns(table)], table
    assert len(preds) == len(labels) == 5 * 8 and set(labels["key_source"]) == {"human"}
    assert runs["run_id"].tolist() == RUN_IDS                      # the fixture's FAKEHASH rows were replaced

    # every prediction validates and its seal re-verifies; ledger rows carry the same hash
    by_run = runs.set_index("run_id")
    for run_id in RUN_IDS:
        obj, digest = rs.read_sealed(run_id)
        rs.validate_prediction(obj)
        assert rs.verify_hash(rs.prediction_paths(run_id)[0])
        assert by_run.loc[run_id, "hash"] == digest and set(preds.loc[preds.run_id == run_id, "hash"]) == {digest}
        assert (by_run.loc[run_id, "made_at"], int(by_run.loc[run_id, "k"])) == (obj["made_at"], obj["k"])
    assert runs["feature_set"].tolist() == ["exam_history", "exam_history", "full", "full", "full"]
    assert runs["leakage_ok"].astype(bool).all() and set(runs["key_source"]) == {"human"}
    for metric in METRICS:
        assert runs[metric].between(0.0, 1.0).all(), metric
    assert (runs["tokens"] == 0).all() and runs["checks_first_try"].astype(bool).all() and (runs["seconds"] > 0).all()

    # lessons: each prediction used the lessons of the runs before it; the twin used blank weights and wrote none
    twin = by_run.loc[TWIN]
    assert bool(twin["cold_start"]) and pd.isna(twin["lessons_run_seq"])
    twin_obj = rs.read_sealed(TWIN)[0]
    assert twin_obj["cold_start"] is True and twin_obj["weights"] == COLD_START_WEIGHTS
    evidence = runs[~runs["cold_start"].astype(bool)]["lessons_run_seq"]
    assert [None if pd.isna(v) else int(v) for v in evidence] == [None, 1, 2, 3]
    history = get_lessons_store().history()
    assert [h["run_seq"] for h in history] == [1, 2, 3, 5]
    mirror = be.query(ledger, "SELECT run_seq, count(*) AS n FROM {{lessons}} GROUP BY run_seq ORDER BY run_seq")
    assert mirror["run_seq"].tolist() == [1, 2, 3, 5] and set(mirror["n"]) == {len(SIGNALS)}
    assert history[0]["weights"] == COLD_START_WEIGHTS              # one scored run is not enough to refit
    assert history[-1]["weights"] != history[0]["weights"] and history[-1]["weights"] != history[1]["weights"]
    twin_steps = {s["step"]: s for s in out["runs"][3]["steps"]}
    assert twin_steps["refit_lessons"]["skipped"] == twin_steps["store_lessons"]["skipped"] == backtest.TWIN_SKIPPED
    assert out["runs"][3]["lessons_written"] is None and out["runs"][4]["lessons_written"] == 5
    assert not list((config.get_settings().local_dir / "runs").glob("*.duckdb"))   # every run database dropped

    # resuming from run 3 replays runs 3-5: the same seals, no new rows, and lessons still forward in time
    code, again = cli(run_list.main, ["--list", str(FIXTURE_LIST), "--from-seq", "3"], capsys)
    assert code == config.EXIT_OK and [r.get("skipped") is not None for r in again["runs"]] == [True, True] + [False] * 3
    assert [r["hash"] for r in again["runs"][2:]] == [by_run.loc[r, "hash"] for r in RUN_IDS[2:]]
    assert [r["lessons_run_seq"] for r in again["runs"][2:]] == [2, None, 3]
    assert len(be.query(ledger, "SELECT * FROM {{runs}}")) == 5


# ================================================================== the chain == its steps, one command each
def test_chain_equals_the_step_by_step_commands(tmp_path, monkeypatch, capsys):
    _fresh_lab(monkeypatch, tmp_path / "chain")
    chained = [backtest.run_backtest(backtest.Backtest(COURSE, exam, seq, made_at=MADE_AT))
               for seq, exam in ((1, QUIZ21), (2, FINAL21))]
    assert [code for code, _ in chained] == [0, 0]
    chain_lessons = [h["weights"] for h in get_lessons_store().history()]

    _fresh_lab(monkeypatch, tmp_path / "steps")
    for seq, exam in ((1, QUIZ21), (2, FINAL21)):
        base = ["--course", COURSE, "--target-exam", exam, "--run-seq", str(seq), "--made-at", MADE_AT]

        def step(name: str, *extra: str) -> str:
            code, out = cli(backtest.main, [*base, "--step", name, *extra], capsys)
            assert code == config.EXIT_OK, (name, out)
            return json.dumps(out)                        # handed on inline, as a Rote step references @N

        made = step("make_run_db")
        signals, leak, lessons = step("run_signals", "--run-db", made), step("leakage_check", "--run-db", made), \
            step("read_lessons")
        sealed = json.loads(step("rank_and_seal", "--signals", signals, "--lessons", lessons))
        scored = step("score")
        step("append_ledger", "--leakage", leak, "--score", scored, "--tokens", "0", "--checks-first-try", "true")
        step("store_lessons", "--refit", step("refit_lessons"))
        step("cognee_feedback")
        assert json.loads(step("drop_run_db", "--run-db", made))["dropped"] == f"run-r{seq}-{exam}"
        assert sealed["hash"] == chained[seq - 1][1]["hash"]
    assert [h["weights"] for h in get_lessons_store().history()] == chain_lessons

    code, out = cli(backtest.main, [*base, "--step", "append_ledger"], capsys)   # a missing input: hard, with JSON
    assert code == config.EXIT_HARD and "leakage_ok" in out["error"]
    code, out = cli(backtest.main, [*base, "--signals", "{}"], capsys)           # step inputs without --step
    assert code == config.EXIT_HARD and out["error"].startswith("usage")


# ================================================================== failures stop the chain with evidence
def test_leakage_stops_the_chain_with_evidence(lab, monkeypatch):
    with monkeypatch.context() as m:  # a broken filter that copies the target's future lectures into the run db
        m.setitem(make_run_db.ROW_FILTERS, "lectures", "course = $course")
        bt = backtest.Backtest(COURSE, QUIZ22, 3)
        code, out = backtest.run_backtest(bt)
    assert code == config.EXIT_LEAKAGE and out["failed_step"] == "leakage_check" and not out["ok"]
    lectures = [v for v in out["evidence"]["violations"] if v["table"] == "lectures"]
    assert lectures == [{"table": "lectures", "check": "not_visible", "n": 11,
                         "sample": ["FX.101|2022F|10", "FX.101|2022F|9"]}]  # sessions 9..19, after the quiz (8)
    assert [s["step"] for s in out["steps"]] == ["make_run_db", "run_signals", "leakage_check", "drop_run_db"]
    assert not rs.prediction_paths(bt.run_id)[1].exists() and not os.path.exists(bt.run_db.path)

    code, out = backtest.run_backtest(backtest.Backtest(COURSE, QUIZ22, 3))       # fixed: runs, but not first try
    assert code == config.EXIT_OK and out["checks_first_try"] is False
    be, ledger = lab
    row = be.query(ledger, "SELECT * FROM {{runs}} WHERE run_id = $r", {"r": bt.run_id}).iloc[0]
    assert not bool(row["checks_first_try"]) and int(row["tokens"]) == 0
    assert backtest.add_run_tokens(bt.run_id, 17) and backtest.add_run_tokens(bt.run_id, 3)   # refit's LLM tokens
    assert int(be.query(ledger, "SELECT tokens FROM {{runs}} WHERE run_id = $r", {"r": bt.run_id})["tokens"][0]) == 20
    assert not backtest.add_run_tokens(f"r9-{FINAL22}", 5)
    assert [a["exit_code"] for a in backtest.read_attempts(bt.run_id)] == [config.EXIT_LEAKAGE, config.EXIT_OK]


def test_a_taken_run_seq_is_refused_before_anything_runs(lab):
    code, out = backtest.run_backtest(backtest.Backtest(COURSE, FINAL22, 3))  # the fixture ledger has r3 (FAKEHASH3)
    assert code == config.EXIT_HARD and out["failed_step"] == "make_run_db"
    assert out["evidence"]["clash"] == [f"r3-{QUIZ22}"]
    assert [(s["step"], s.get("skipped")) for s in out["steps"]] == [("make_run_db", None),
                                                                     ("drop_run_db", "no run database was created")]


def test_rank_and_seal_takes_the_read_lessons_output(lab, capsys):
    bt = backtest.Backtest(COURSE, FINAL22, 7)
    assert backtest.run_step(bt, "make_run_db")[0] == 0 and backtest.run_step(bt, "run_signals")[0] == 0
    base = ["--run-id", bt.run_id, "--course", COURSE, "--target-exam", FINAL22, "--made-at", MADE_AT,
            "--signals", json.dumps(bt.outputs["run_signals"])]
    fitted = {**COLD_START_WEIGHTS, "x1": 1.4, "intercept": -0.9}
    for lessons, want in (({"run_seq": None, "weights": fitted}, config.EXIT_HARD),      # null seq = cold start only
                          ({"ok": False, "error": "store down"}, config.EXIT_HARD),
                          ({"run_seq": 7, "weights": fitted}, config.EXIT_LEAKAGE)):      # learned from this run
        code, out = cli(rs.main, [*base, "--lessons", json.dumps(lessons)], capsys)
        assert code == want and not out["ok"], out
    code, out = cli(rs.main, [*base, "--lessons", json.dumps({"ok": True, "run_seq": 2, "weights": fitted,
                                                               "source": "ledger"})], capsys)
    assert code == 0 and out["lessons_run_seq"] == 2 and rs.read_sealed(bt.run_id)[0]["weights"] == fitted
    backtest.run_step(bt, "drop_run_db")


# ================================================================== run list validation
def test_run_list_refuses_out_of_order_and_inconsistent_lists(lab, tmp_path, capsys):
    be, ledger = lab
    header = "run_seq,course,target_exam,cold_start,feature_set\n"
    cases = {
        "backwards": (f"10,{COURSE},{FINAL22},false,\n11,{COURSE},{QUIZ22},false,\n", "runs go forward in time only"),
        "same seq": (f"10,{COURSE},{FINAL22},false,\n10,{COURSE},{FINAL22},true,\n", "is not greater than 10"),
        "lonely twin": (f"10,{COURSE},{FINAL22},true,\n", "needs an earlier non-twin run"),
        "two twins": (f"10,{COURSE},{FINAL22},false,\n11,{COURSE},{FINAL22},true,\n12,{COURSE},{FINAL22},true,\n",
                      "already has a cold-start twin"),
        "annotation": (f"10,{COURSE},{FINAL22},false,exam_history\n", "the ordering rule gives full"),
        "unknown exam": (f"10,{COURSE},{COURSE}-final-2030F,false,\n", "not in the ledger's exams table"),
        "other course": (f"10,XX.1,{FINAL22},false,\n", "belongs to course FX.101"),
        "behind ledger": (f"10,{COURSE},{FINAL21},false,\n", f"not later than {QUIZ22} (ledger run r3-{QUIZ22})"),
        "seq taken": (f"3,{COURSE},{FINAL22},false,\n", f"run_seq 3 is already used by ledger run r3-{QUIZ22}"),
        "twin flag": (f"3,{COURSE},{QUIZ22},true,\n", "cold_start differs from the ledger's run"),
    }
    for name, (rows, message) in cases.items():
        path = _write(tmp_path / f"{name.replace(' ', '_')}.csv", "# a comment line\n" + header + rows)
        code, out = cli(run_list.main, ["--list", str(path)], capsys)
        assert code == config.EXIT_HARD and not out["ok"], (name, out)
        assert any(message in p for p in out["problems"]), (name, out["problems"])
    for bad in ("run_seq,course,target_exam\n1,FX.101,x\n", header + "one,FX.101,x,false,\n",
                header + f"10,{COURSE},{FINAL22},maybe,\n"):
        code, out = cli(run_list.main, ["--list", str(_write(tmp_path / "bad.csv", bad))], capsys)
        assert code == config.EXIT_HARD and "RunListError" in out["error"], out
    assert be.query(ledger, "SELECT count(*) AS n FROM {{predictions}}")["n"][0] == 0   # nothing ran
    code, out = cli(run_list.main, ["--list", str(FIXTURE_LIST), "--dry-run"], capsys)
    assert code == 0 and [p["run_id"] for p in out["plan"]] == RUN_IDS


def test_real_template_is_a_forward_list():
    """The template's rows are in order and annotated per the ordering rule, with exam times from the 6.641 scout
    data (data/courses/6.641) and, for 6.003 (no scout data yet), the scout's synthetic in-term ordinals."""
    text = TEMPLATE_LIST.read_text(encoding="utf-8")
    assert "REVISE IT once the OCW scout confirms" in text
    rows = run_list.read_run_list(TEMPLATE_LIST)
    scout = pd.read_csv(config.DATA_DIR / "courses" / "6.641" / "exams.csv")
    published = {"6.641": json.loads((config.DATA_DIR / "courses" / "6.641" / "course.json").read_text())
                 ["published_term_seq"], "6.003": 20113}
    ordinal = {"quiz1": 1000, "quiz2": 2000, "quiz3": 3000, "final": 9000}   # course.json assumption S-SYNTH
    exams = {}
    for r in rows:
        if r.course == "6.641":
            hit = scout[scout.exam_id == r.target_exam].iloc[0]          # every 6.641 target is in the scout's list
            exams[r.target_exam] = run_list.ExamTime(r.course, int(hit.term_seq), int(hit.session), published[r.course])
        else:
            _, exam_type, term = r.target_exam.rsplit("-", 2)
            exams[r.target_exam] = run_list.ExamTime(r.course, config_term_seq(term), ordinal[exam_type],
                                                     published[r.course])
    assert run_list.validate(rows, exams) == []
    assert all(r.feature_set is not None for r in rows)
    assert [r.run_seq for r in rows] == [1, 2, 3, 7, 8, 9, 10, 11, 12, 13, 14]
    assert rows[-1].target_exam == "6.003-final-2011F" and rows[4].cold_start and rows[4].target_exam == rows[3].target_exam


def config_term_seq(term: str) -> int:
    from oracle.ordering import term_to_seq
    return term_to_seq(term)


# ================================================================== as modules
def test_scripts_run_as_modules(lab, tmp_path):
    env = {**os.environ, "ORACLE_SKIP_DOTENV": "1"}  # ORACLE_LOCAL_DIR points at the lab's fixture ledger

    def run(*argv: str) -> tuple[int, dict]:
        done = subprocess.run([sys.executable, "-m", *argv], cwd=config.REPO_ROOT, env=env,
                              capture_output=True, text=True, timeout=180)
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        assert len(lines) == 1, done.stdout + done.stderr
        return done.returncode, json.loads(lines[0])

    code, out = run("oracle.backtest", "--course", COURSE, "--target-exam", QUIZ21, "--run-seq", "1")
    assert code == 0 and out["ok"] and len(out["steps"]) == len(backtest.STEPS) and out["key_source"] == "human"
    backwards = _write(tmp_path / "backwards.csv", "run_seq,course,target_exam,cold_start\n"
                                                   f"10,{COURSE},{FINAL22},false\n11,{COURSE},{QUIZ22},false\n")
    code, out = run("oracle.run_list", "--list", str(backwards), "--dry-run")
    assert code == config.EXIT_HARD and out["problems"]
    for argv in (("oracle.fixture_ledger",), ("oracle.config", "--bogus"), ("oracle.backtest", "--course", COURSE)):
        code, out = run(*argv)                          # usage errors: JSON and exit 1, never argparse's 2
        assert code == config.EXIT_HARD and out["error"].startswith("usage"), argv
