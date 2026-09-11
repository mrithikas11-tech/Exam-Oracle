"""End to end, offline: fixture ledger -> echo_pairs -> the FX.101 run list through oracle.run_list and
oracle.backtest (Play 2), in LOCAL mode.

A spy (a Python audit hook on "open") records every open of a file in the sealed folder together with the run being
scored at that moment and whether that run's <run_id>.sha256 already existed, so "the answer key is read only after
the prediction hash is written" (contracts/README.md "Ground truth") is checked from outside the code under test.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import jsonschema
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
CHAIN_LIST = config.DATA_DIR / "run_lists" / "real_chain.csv"
SIDE_LIST = config.DATA_DIR / "run_lists" / "history_side_track.csv"
QUIZ21, FINAL21, QUIZ22, FINAL22 = (f"{COURSE}-quiz1-2021F", f"{COURSE}-final-2021F", f"{COURSE}-quiz1-2022F",
                                    f"{COURSE}-final-2022F")
FINAL20 = f"{COURSE}-final-2020F"
RUN_IDS =[f"r1-{QUIZ21}", f"r2-{FINAL21}", f"r3-{QUIZ22}", f"r4-{QUIZ22}", f"r5-{FINAL22}"]
TWIN = f"r4-{QUIZ22}"
REVEAL_KEY = f"answer_key_{FINAL22}.csv"
METRICS = ("model_pts", "even_pts", "lastexam_pts", "recall_k", "brier")
MADE_AT = "2026-09-11T13:00:00-07:00"
# the contract files themselves, read here so the checks do not go through the code under test
CONTRACT_VALIDATOR = jsonschema.Draft202012Validator(json.loads(config.PREDICTION_SCHEMA_PATH.read_text(encoding="utf-8")))
FIXTURE_PREDICTION = json.loads((config.FIXTURES_DIR / "prediction.json").read_text(encoding="utf-8"))

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
    predictable = sorted(t for t in be.query(ledger, "SELECT topic_id FROM {{topics}}")["topic_id"] if t != "T00")
    assert len(preds) == 5 * len(predictable) and set(labels["key_source"]) == {"human"}   # D6: T00 never predicted
    for run_id in RUN_IDS:                                            # a label for every predicted topic of every run
        assert sorted(preds.loc[preds.run_id == run_id, "topic_id"]) == predictable
        assert set(labels.loc[labels.run_id == run_id, "topic_id"]) >= set(predictable)
    assert runs["run_id"].tolist() == RUN_IDS                      # the fixture's FAKEHASH rows were replaced

    # every prediction validates and its seal re-verifies; ledger rows carry the same hash
    by_run = runs.set_index("run_id")
    for run_id in RUN_IDS:
        obj, digest = rs.read_sealed(run_id)
        rs.validate_prediction(obj)
        pred_file, hash_file = rs.prediction_paths(run_id)
        assert rs.verify_hash(pred_file)
        # ...and without the code under test: the contract's schema file, the fixture shape C builds against, and
        # the contract's canonical form recomputed from the file on disk, the way the reveal recomputes it
        on_disk = json.loads(pred_file.read_text(encoding="utf-8"))
        CONTRACT_VALIDATOR.validate(on_disk)
        assert set(on_disk) == set(FIXTURE_PREDICTION), run_id
        canonical = json.dumps(on_disk, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        assert hashlib.sha256(canonical).hexdigest() == hash_file.read_text(encoding="utf-8").split()[0] == digest
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
    assert out["runs"][0]["warnings"]["refit_lessons"].startswith("weights kept")
    # every version either kept the previous weights (refit_lessons said why) or moved beta, and at least one refit
    # moved it; WHICH runs train is the refit rule's business (decision D4: feature_set = full, cold_start = false)
    by_seq, previous, moved = {r["run_seq"]: r for r in out["runs"]}, COLD_START_WEIGHTS, 0
    for version in history:
        if "weights kept" in by_seq[version["run_seq"]]["warnings"].get("refit_lessons", ""):
            assert version["weights"] == pytest.approx(previous, abs=1e-12), version
        else:
            assert version["weights"] != pytest.approx(previous, abs=1e-9), version
            moved += 1
        previous = version["weights"]
    assert moved >= 1, history
    versions ={h["run_seq"]: h["weights"] for h in history}
    for run_id in RUN_IDS:                    # each prediction was made with exactly the lessons version it names
        obj = rs.read_sealed(run_id)[0]
        used = obj["lessons_run_seq"]
        assert obj["weights"] == pytest.approx(COLD_START_WEIGHTS if used is None else versions[used], abs=1e-6)
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
    for extra in (["--step", "score", "--seal-only"], ["--seal-only", "--frozen"]):   # modes that exclude each other
        code, out = cli(backtest.main, [*base, *extra], capsys)
        assert code == config.EXIT_HARD and out["error"].startswith("usage"), extra


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
                header + f"10,{COURSE},{FINAL22},maybe,\n", header + f"10,{COURSE},{FINAL22},false,history\n"):
        code, out = cli(run_list.main, ["--list", str(_write(tmp_path / "bad.csv", bad))], capsys)
        assert code == config.EXIT_HARD and "RunListError" in out["error"], out
    assert be.query(ledger, "SELECT count(*) AS n FROM {{predictions}}")["n"][0] == 0   # nothing ran
    code, out = cli(run_list.main, ["--list", str(FIXTURE_LIST), "--dry-run"], capsys)
    assert code == 0 and [p["run_id"] for p in out["plan"]] == RUN_IDS


def _check_real_list(rows) -> None:
    """A real list validates against the course-structure data every row names (data/courses/<course>/exams.csv
    for (term_seq, session), course.json for the published term), is annotated per the ordering rule, and ends
    with the sealed reveal, followed at most by the reveal's cold-start twin."""
    exams, roles = {}, {}
    for course in sorted({r.course for r in rows}):
        folder = config.DATA_DIR / "courses" / course
        published = int(json.loads((folder / "course.json").read_text(encoding="utf-8"))["published_term_seq"])
        for e in pd.read_csv(folder / "exams.csv").itertuples(index=False):
            session = None if pd.isna(e.session) else int(e.session)
            exams[e.exam_id] = run_list.ExamTime(course, int(e.term_seq), session, published)
            roles[e.exam_id] = e.role
    assert [r.target_exam for r in rows if r.target_exam not in exams] == []
    assert [r.target_exam for r in rows if roles[r.target_exam] == "excluded"] == []
    assert run_list.validate(rows, exams) == []
    assert all(r.feature_set is not None for r in rows)
    reveal = [r for r in rows if r.kind == "main"][-1]
    assert reveal.target_exam == "6.003-final-2011F" and roles[reveal.target_exam] == "sealed_reveal"
    assert reveal.feature_set == "full"
    assert all(r.kind == "twin" and r.target_exam == reveal.target_exam for r in rows if r.run_seq > reveal.run_seq)


def test_real_lists_are_forward_lists():
    """D8: real_chain.csv alone, and real_chain.csv + history_side_track.csv merged by run_seq (the side track is a
    set of ordinary exam_history runs interleaved in time: no exemption), both validate against data/courses/*."""
    chain, side = run_list.read_run_list(CHAIN_LIST), run_list.read_run_list(SIDE_LIST)
    _check_real_list(chain)
    merged = sorted([*chain, *side], key=lambda r: r.run_seq)
    _check_real_list(merged)
    assert [r.target_exam for r in chain if r.kind == "twin"] == ["6.003-quiz1-2011F", "6.003-final-2011F"]
    assert {r.feature_set for r in chain} == {"full"} and {r.feature_set for r in side} == {"exam_history"}
    assert not any(r.cold_start for r in side)            # the side track is evidence-shaped, never a twin
    # moving a side-track run after a later-term run of another course breaks the cross-course term order
    late = run_list.Row(999, 95, "6.641", "6.641-final-2008S", False, "exam_history")
    bad = sorted([*chain, late], key=lambda r: r.run_seq)
    exams = {}
    for course in ("6.641", "18.06", "6.003"):
        folder = config.DATA_DIR / "courses" / course
        published = int(json.loads((folder / "course.json").read_text(encoding="utf-8"))["published_term_seq"])
        for e in pd.read_csv(folder / "exams.csv").itertuples(index=False):
            exams[e.exam_id] = run_list.ExamTime(course, int(e.term_seq), None if pd.isna(e.session) else int(e.session),
                                                 published)
    problems = run_list.validate(bad, exams)
    assert problems and all("6.641-final-2008S" in p for p in problems), problems


# ================================================================== the reveal (D8): seal both, then score frozen
def test_reveal_seals_main_and_twin_before_either_is_scored(lab, tmp_path, monkeypatch, capsys):
    """--seal-only seals the reveal's main run and its cold-start twin with no answer key opened; on stage the same
    rows replay with --frozen: the same seals, then scored, and no lessons version (beta frozen)."""
    be, ledger = lab
    real_score_run = sc.score_run

    def tracked_score_run(run_id, **kwargs):
        _SPY["run"] = run_id
        try:
            return real_score_run(run_id, **kwargs)
        finally:
            _SPY["run"] = None

    monkeypatch.setattr(sc, "score_run", tracked_score_run)
    main, twin = f"r11-{FINAL22}", f"r12-{FINAL22}"
    reveal = _write(tmp_path / "reveal.csv", "run_seq,course,target_exam,cold_start,feature_set\n"
                                             f"11,{COURSE},{FINAL22},false,full\n"
                                             f"12,{COURSE},{FINAL22},true,full\n")
    _SPY.update(active=True, run=None, events=[])
    try:
        code, before = cli(run_list.main, ["--list", str(reveal), "--through-seq", "10"], capsys)  # up to the reveal
        assert code == config.EXIT_OK and all(r.get("skipped") for r in before["runs"]) and _SPY["events"] == []

        code, sealed = cli(run_list.main, ["--list", str(reveal), "--from-seq", "11", "--seal-only"], capsys)
        assert code == config.EXIT_OK and sealed["ok"] and _SPY["events"] == [], (sealed, _SPY["events"])
        seals = {}
        for run_id in (main, twin):
            pred_file, hash_file = rs.prediction_paths(run_id)
            assert rs.verify_hash(pred_file) and not sc.score_path(run_id).exists(), run_id
            seals[run_id] = hash_file.read_text(encoding="utf-8").split()[0]
        assert [s["step"] for s in sealed["runs"][0]["steps"]] == [*backtest.SEAL_STEPS, "drop_run_db"]
        assert sealed["runs"][1]["cold_start"] is True
        assert sealed["runs"][0]["standardization"] == rs.STANDARDIZATION                 # D5 tag in the summary
        assert be.query(ledger, "SELECT count(*) AS n FROM {{runs}} WHERE run_seq >= 11")["n"][0] == 0

        code, scored = cli(run_list.main, ["--list", str(reveal), "--from-seq", "11", "--frozen"], capsys)
    finally:
        _SPY["active"] = False
    assert code == config.EXIT_OK and scored["ok"], scored
    assert [r["hash"] for r in scored["runs"]] == [seals[main], seals[twin]]         # the replays kept the seals
    assert {e["run"] for e in _SPY["events"]} == {main, twin}
    assert all(e["hash_existed"] for e in _SPY["events"]), _SPY["events"]
    runs = be.query(ledger, "SELECT * FROM {{runs}} WHERE run_seq >= 10 ORDER BY run_seq")
    assert runs["run_id"].tolist() == [main, twin] and set(runs["key_source"]) == {"human"}
    assert runs["cold_start"].astype(bool).tolist() == [False, True]
    skipped = {r["run_id"]: {s["step"]: s.get("skipped") for s in r["steps"]} for r in scored["runs"]}
    assert skipped[main]["refit_lessons"] == skipped[main]["store_lessons"] == backtest.FROZEN_SKIPPED
    assert skipped[twin]["refit_lessons"] == skipped[twin]["store_lessons"] == backtest.TWIN_SKIPPED
    assert get_lessons_store().history() == []                       # beta frozen: no lessons version was written


def test_run_list_keeps_terms_forward_across_courses():
    """Lessons transfer across courses, so a run may not target an earlier term than an earlier run of another
    course. Sessions are per course, so two courses' runs in one term are not ordered; a twin stays exempt."""
    t = run_list.ExamTime
    exams = {"A.1-final-2009S": t("A.1", 20091, 26, 20091), "B.2-quiz1-2010S": t("B.2", 20101, 12, 20101),
             "B.2-final-2010S": t("B.2", 20101, 40, 20101), "C.3-quiz1-2010S": t("C.3", 20101, 1000, 20113),
             "C.3-quiz1-2011F": t("C.3", 20113, 9, 20113)}

    def row(seq: int, exam: str, twin: bool = False) -> run_list.Row:
        return run_list.Row(seq + 1, seq, exam.split("-", 1)[0], exam, twin)

    forward = [row(1, "A.1-final-2009S"), row(2, "B.2-quiz1-2010S"), row(3, "B.2-final-2010S"),
               row(4, "C.3-quiz1-2010S"),                    # same term as B.2's final (sessions are not compared)
               row(5, "C.3-quiz1-2011F"), row(6, "C.3-quiz1-2011F", True), row(7, "A.1-final-2009S", True)]
    assert run_list.validate(forward, exams) == []
    problems = run_list.validate([row(1, "B.2-final-2010S"), row(2, "A.1-final-2009S")], exams)
    assert len(problems) == 1 and "across courses" in problems[0] and "B.2-final-2010S" in problems[0], problems
    ledger = [run_list.LedgerRun("r1-B.2-final-2010S", 1, "B.2", "B.2-final-2010S", False)]
    problems = run_list.validate([row(2, "A.1-final-2009S")], exams, ledger)      # the ledger's runs count too
    assert len(problems) == 1 and "ledger run r1-B.2-final-2010S" in problems[0], problems


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
