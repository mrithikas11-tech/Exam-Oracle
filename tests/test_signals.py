"""Signals x1..x7, the per-run database and the leakage check, on the synthetic FX.101 fixture.

Expected values are hand-computed from contracts/fixtures (story in make_fixtures.py): 8 topics; quiz 1 at
session 8 and final at session 20 in 2020F, 2021F, 2022F; the published term 2022F has 18 lectures (sessions
1-7, 9-19; session 9 has no topic), psets due at sessions 3..18, and guidance G1 (final is cumulative) and G2
(final emphasizes sessions 13-19), both stated at 2022F session 1. Offline only (local DuckDB backend).
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from oracle import config, ordering
from oracle.backend import DbHandle, HotdataBackend, RUN_TABLES, columns, render_params, render_tables
from oracle.leakage_check import check_leakage, main as leakage_main
from oracle.make_run_db import build_run_db, main as make_main, select_sql
from oracle.run_context import RunError, expand_visible, load_sql, resolve_run_db, run_seq_of
from oracle.run_signals import SIGNAL_SQL, compute_signals, main as signals_main

C = "FX.101"
FINAL_22, FINAL_21, QUIZ1_22, QUIZ2_22 = (f"{C}-final-2022F", f"{C}-final-2021F", f"{C}-quiz1-2022F",
                                          f"{C}-quiz2-2022F")
TOPICS = [f"T0{i}" for i in range(1, 9)]


def _row(table: str, **values) -> dict:
    names = [c.name for c in columns(table)]
    assert not set(values) - set(names), set(values) - set(names)
    return {name: values.get(name) for name in names}


def _upsert(be, db, table: str, *rows: dict) -> None:
    be.load_table(db, table, [_row(table, **r) for r in rows], mode="upsert")


def _col(out: dict, signal: str) -> dict:
    return {t["topic_id"]: t["signals"][signal] for t in out["topics"]}


def _only(values: dict, default: float = 0.0) -> dict:
    return {t: values.get(t, default) for t in TOPICS}


def _run(be, target: str, seq: int, **kw) -> dict:
    run_id = f"r{seq}-{target}"
    build_run_db(be, C, target, run_id)
    return compute_signals(be, C, target, run_id, **kw)


def _cli(module: str, *args: str) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, "-m", f"oracle.{module}", *args], cwd=config.REPO_ROOT,
                          capture_output=True, text=True, timeout=120)
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 1, proc.stdout + proc.stderr  # ONE JSON object on stdout
    return proc.returncode, json.loads(lines[0])


# ================================================================== run database
def test_make_run_db_copies_only_visible_rows(fixture_ledger):
    be, _ = fixture_ledger
    out = build_run_db(be, C, FINAL_22, f"r4-{FINAL_22}")
    assert out["feature_set"] == "full" and out["excluded_sealed_exams"] == [FINAL_22]
    # all 5 earlier exams (not the sealed target) and their 19 items; every lecture/pset is before session 20
    assert out["row_counts"] == {"courses": 1, "topics": 8, "exams": 5, "exam_items": 19, "lectures": 18,
                                 "homework_items": 12, "homework_vec": 12, "echo_pairs": 4, "guidelines": 2}
    handle = DbHandle.from_dict(out["run_db"])
    assert FINAL_22 not in set(be.query(handle, "SELECT exam_id FROM {{exams}}")["exam_id"])
    hist = build_run_db(be, C, FINAL_21, f"r2-{FINAL_21}")
    # 2021F final (session 20) sees 2020F quiz (3 items) + final (6) and 2021F quiz 1 (2); nothing of 2022F
    assert hist["feature_set"] == "exam_history"
    assert hist["row_counts"] == {"courses": 1, "topics": 8, "exams": 3, "exam_items": 11, "lectures": 0,
                                  "homework_items": 0, "homework_vec": 0, "echo_pairs": 0, "guidelines": 0}


def test_make_run_db_excludes_every_sealed_exam(fixture_ledger):
    be, ledger = fixture_ledger
    _upsert(be, ledger, "exams", dict(course=C, exam_id=FINAL_21, exam_type="final", term="2021F", term_seq=20213,
                                      session=20, total_points=100, coverage_from_session=1, coverage_to_session=19,
                                      cumulative=True, sealed=True))
    out = build_run_db(be, C, FINAL_22, f"r4-{FINAL_22}")
    assert out["excluded_sealed_exams"] == [FINAL_21, FINAL_22]
    # final-2021F's 5 item rows and the 2 echo pairs pointing at it are gone
    assert (out["row_counts"]["exams"], out["row_counts"]["exam_items"], out["row_counts"]["echo_pairs"]) == (4, 14, 2)
    sig = compute_signals(be, C, FINAL_22, f"r4-{FINAL_22}")
    # prior finals = {2020F} only, so d(2020F) = 0 and w = 1: x1 = 1 on T02, T04, T05, T06, T07
    assert _col(sig, "x1") == _only(dict.fromkeys(["T02", "T04", "T05", "T06", "T07"], 1.0))


# ================================================================== signals
def test_signals_full_final_2022F(fixture_ledger):
    be, _ = fixture_ledger
    out = _run(be, FINAL_22, 4)
    assert (out["feature_set"], out["computed"], out["zeroed"]) == ("full", list(SIGNAL_SQL), [])
    assert out["window"] == {"from": 1, "to": 19, "source": "stated"}
    # x1: prior finals 2021F (d=0, w=1) and 2020F (d=1, w=0.7); sum w = 1.7
    assert _col(out, "x1") == pytest.approx(_only({"T02": 0.7 / 1.7, "T04": 1, "T05": 1, "T06": 1, "T07": 1,
                                                   "T08": 1 / 1.7}), abs=1e-6)
    assert _col(out, "x2") == _only({}, 1.0)  # stated window 1..19 holds every visible lecture
    # x3 (tau 0.02): T06 has 2 echoed psets (0.031, 0.029), T05 has 1 (0.027); ps6-p1 (0.012) is below tau
    assert _col(out, "x3") == _only({"T05": 0.5, "T06": 1.0})
    # x4: 18 lectures in the window (incl. session 9 with no topic)
    assert _col(out, "x4") == pytest.approx({"T01": 2 / 18, "T02": 3 / 18, "T03": 2 / 18, "T04": 2 / 18,
                                             "T05": 2 / 18, "T06": 3 / 18, "T07": 2 / 18, "T08": 1 / 18}, abs=1e-6)
    # x5: last quiz at session 8; T04..T08 taught after it; T01-T03 were on quiz 1
    assert _col(out, "x5") == _only(dict.fromkeys(["T04", "T05", "T06", "T07", "T08"], 1.0))
    # x6: quiz1-2022F points 15 (T02) + 7.5 (T03) + 7.5 (T01) of 30
    assert _col(out, "x6") == _only({"T01": 0.25, "T02": 0.5, "T03": 0.25})
    assert _col(out, "x7") == _only({}, 0.5)  # G1 cumulative covers all; no tests yet -> trust 0.5
    assert out["baselines"] == {"even": ["T02", "T06", "T01", "T03", "T04", "T05", "T07", "T08"],
                                "last_exam": ["T04", "T05", "T06", "T07", "T08", "T02", "T01", "T03"],
                                "last_exam_id": FINAL_21}
    lower = compute_signals(be, C, FINAL_22, f"r4-{FINAL_22}", tau=0.01)  # ps6-p1 (T07) now echoes too
    assert _col(lower, "x3") == _only({"T05": 0.5, "T06": 1.0, "T07": 0.5})


def test_signals_exam_history_final_2021F(fixture_ledger):
    be, _ = fixture_ledger
    out = _run(be, FINAL_21, 2)
    assert (out["feature_set"], out["zeroed"], out["window"]) == ("exam_history", ["x2", "x3", "x4", "x5", "x6"], None)
    # only prior final is 2020F (w = 1): T02, T04, T05, T06, T07
    assert _col(out, "x1") == _only(dict.fromkeys(["T02", "T04", "T05", "T06", "T07"], 1.0))
    for signal in ("x2", "x3", "x4", "x5", "x6", "x7"):  # x7: G1/G2 are stated in 2022F, after the target
        assert _col(out, signal) == _only({}), signal
    # last final 2020F: T06 30 pts, T02/T04/T07 20, T05 10; no lecture data -> no even baseline, no padding
    assert out["baselines"] == {"even": [], "last_exam": ["T06", "T02", "T04", "T07", "T05"],
                                "last_exam_id": f"{C}-final-2020F"}


def test_cumulative_final_without_stated_coverage(fixture_ledger):
    be, ledger = fixture_ledger
    # same target, but no stated window and cumulative NULL: a final counts as cumulative -> sessions 1..19
    _upsert(be, ledger, "exams", dict(course=C, exam_id=FINAL_22, exam_type="final", term="2022F", term_seq=20223,
                                      session=20, total_points=100, sealed=True))
    out = _run(be, FINAL_22, 4)
    assert out["window"] == {"from": 1, "to": 19, "source": "cumulative"}
    assert _col(out, "x2") == _only({}, 1.0) and _col(out, "x4")["T06"] == pytest.approx(3 / 18, abs=1e-6)


def test_signals_quiz_targets_and_since_previous_exam_window(fixture_ledger):
    be, ledger = fixture_ledger
    q1 = _run(be, QUIZ1_22, 3)
    assert q1["window"] == {"from": 1, "to": 7, "source": "stated"}
    assert _col(q1, "x1") == pytest.approx(_only({"T01": 0.7 / 1.7, "T02": 1, "T03": 1}), abs=1e-6)
    assert _col(q1, "x2") == _only({"T01": 1.0, "T02": 1.0, "T03": 1.0})
    assert _col(q1, "x4") == pytest.approx(_only({"T01": 2 / 7, "T02": 3 / 7, "T03": 2 / 7}), abs=1e-6)
    for signal in ("x3", "x5", "x6", "x7"):  # no visible echo, not a final, no earlier quiz, guidance is for finals
        assert _col(q1, signal) == _only({}), signal
    _upsert(be, ledger, "exams", dict(course=C, exam_id=QUIZ2_22, exam_type="quiz2", term="2022F", term_seq=20223,
                                      session=14, total_points=30, cumulative=False, sealed=False))
    q2 = _run(be, QUIZ2_22, 5)
    assert q2["window"] == {"from": 9, "to": 13, "source": "since_previous_exam"}  # after quiz 1 (session 8)
    assert _col(q2, "x2") == _only({"T04": 1.0, "T05": 1.0})   # T01-T03 taught before the window
    assert _col(q2, "x4") == _only({"T04": 0.4, "T05": 0.4})   # sessions 9 (no topic), 10-11, 12-13
    assert _col(q2, "x6") == _only({"T01": 0.25, "T02": 0.5, "T03": 0.25})
    assert _col(q2, "x3") == _only({"T05": 1.0})               # ps4-p2 (due 12) echoes final-2021F
    assert _col(q2, "x1") == _only({}) and _col(q2, "x5") == _only({})
    assert q2["baselines"] == {"even": ["T04", "T05"], "last_exam": ["T04", "T05"], "last_exam_id": None}


def _test_run(run_id: str, seq: int, course: str, target: str) -> dict:
    return dict(run_id=run_id, run_seq=seq, course=course, target_exam=target, feature_set="full",
                cold_start=False, k=4, leakage_ok=True, key_source="human", hash="h", made_at="m")


def test_x7_trust_learned_only_from_earlier_visible_tests(fixture_ledger):
    be, ledger = fixture_ledger
    _upsert(be, ledger, "runs", _test_run(f"r4-{FINAL_22}", 4, C, FINAL_22),          # T's own earlier twin
            _test_run(f"r6-{QUIZ1_22}", 6, C, QUIZ1_22),                               # a later run
            _test_run("r1-ZZ.200-final-2030F", 1, "ZZ.200", "ZZ.200-final-2030F"))     # another course
    _upsert(be, ledger, "guidelines", dict(course="ZZ.200", guideline_id="H1", kind="emphasis_window",
                                           applies_to_exam_type="final", from_session=1, to_session=5,
                                           source_term="2030F", source_term_seq=20303, source_session=1))
    tests = [("G2", f"r2-{FINAL_21}", "match"), ("G2", f"r3-{QUIZ1_22}", "partial"),
             ("H1", "r1-ZZ.200-final-2030F", "match"), ("G2", f"r6-{QUIZ1_22}", "miss"),
             ("G1", f"r2-{FINAL_21}", "miss"), ("G1", f"r4-{FINAL_22}", "match")]
    _upsert(be, ledger, "guideline_tests", *[dict(guideline_id=g, run_id=r, verdict=v) for g, r, v in tests])
    out = _run(be, FINAL_22, 5)
    # emphasis_window: match, partial, match (other course) = 2.5/3; r6 is later. cumulative: miss = 0; the
    # r4 twin targeted T itself. coverage: no tests -> 0.5
    assert out["trust"] == {"cumulative": {"trust": 0.0, "n_tests": 1},
                            "emphasis_window": {"trust": round(2.5 / 3, 6), "n_tests": 3},
                            "coverage": {"trust": 0.5, "n_tests": 0}}
    # G2 (sessions 13-19) covers T05 (taught at sessions 12-13) .. T08; G1 now carries trust 0
    assert _col(out, "x7") == _only(dict.fromkeys(["T05", "T06", "T07", "T08"], round(2.5 / 3, 6)))


def test_x7_exam_history_uses_topic_lecture_range(fixture_ledger):
    be, ledger = fixture_ledger
    _upsert(be, ledger, "guidelines", dict(course=C, guideline_id="G3", kind="coverage", applies_to_exam_type="final",
                                           from_session=13, to_session=19, source_term="2021F",
                                           source_term_seq=20213, source_session=5))
    out = _run(be, FINAL_21, 2)
    # no visible lectures -> topic ranges T06 13-15, T07 16-17, T08 18-19 overlap 13..19; T05 (11-12) does not
    assert _col(out, "x7") == _only(dict.fromkeys(["T06", "T07", "T08"], 0.5))
    assert all(_col(out, s) == _only({}) for s in ("x2", "x3", "x4", "x5", "x6"))


# ================================================================== leakage
def test_clean_chain_through_the_clis(fixture_ledger, capsys):
    args = ["--course", C, "--target-exam", FINAL_22, "--run-id", f"r4-{FINAL_22}"]
    assert make_main(args) == config.EXIT_OK
    made = json.loads(capsys.readouterr().out)
    assert made["run_db"]["name"] == f"run-r4-{FINAL_22}"
    assert signals_main(args + ["--run-db", json.dumps(made)]) == config.EXIT_OK
    signals = json.loads(capsys.readouterr().out)
    assert len(signals["topics"]) == 8 and signals["run_db"]["name"] == made["run_db"]["name"]
    assert leakage_main(args) == config.EXIT_OK
    result = json.loads(capsys.readouterr().out)
    assert result["leakage_ok"] and result["violations"] == [] and len(result["checks"]) == 17
    assert all(c["n"] == 0 for c in result["checks"])


def test_leakage_check_exits_4_on_injected_future_rows(fixture_ledger):
    be, _ = fixture_ledger
    args = ["--course", C, "--target-exam", FINAL_22, "--run-id", f"r4-{FINAL_22}"]
    code, made = _cli("make_run_db", *args)
    assert code == 0 and made["ok"]
    run_db = DbHandle.from_dict(made["run_db"])
    be.load_table(run_db, "lectures", [_row("lectures", course=C, term="2022F", term_seq=20223, session=20,
                                            lecture_n=19, title="future", topic_id="T08")], mode="append")
    be.load_table(run_db, "exam_items", [_row("exam_items", course=C, exam_id=FINAL_22, exam_type="final",
                                              term="2022F", term_seq=20223, session=20, problem="1", points=20,
                                              topic_id="T06", points_share=20, tag_source="human")], mode="append")
    code, result = _cli("leakage_check", *args, "--run-db", json.dumps(made))
    assert code == config.EXIT_LEAKAGE and not result["ok"] and not result["leakage_ok"]
    found = {(v["table"], v["check"]): v for v in result["violations"]}
    assert found[("lectures", "not_visible")]["sample"] == ["FX.101|2022F|20"]
    for check in ("not_visible", "target_rows", "exam_not_in_run_db", "sealed_in_ledger"):
        assert found[("exam_items", check)]["n"] == 1, check
    assert found[("exam_items", "target_rows")]["sample"] == [f"{FINAL_22}|1|T06"]
    assert set(found) == {("lectures", "not_visible"), ("exam_items", "not_visible"), ("exam_items", "target_rows"),
                          ("exam_items", "exam_not_in_run_db"), ("exam_items", "sealed_in_ledger")}


def test_leakage_check_reads_the_ledger_sealed_list(fixture_ledger):
    be, ledger = fixture_ledger
    build_run_db(be, C, FINAL_22, f"r4-{FINAL_22}")
    _upsert(be, ledger, "exams", dict(course=C, exam_id=FINAL_21, exam_type="final", term="2021F", term_seq=20213,
                                      session=20, total_points=100, cumulative=True, sealed=True))
    result = check_leakage(be, C, FINAL_22, f"r4-{FINAL_22}")
    found = {(v["table"], v["check"]): (v["n"], v["sample"]) for v in result["violations"]}
    assert found == {("exams", "sealed_in_ledger"): (1, [FINAL_21]),
                     ("exam_items", "sealed_in_ledger"): (5, [FINAL_21]),
                     ("echo_pairs", "sealed_in_ledger"): (2, [FINAL_21])}


def test_hard_faults_are_exit_1_with_one_json_object(fixture_ledger, capsys):
    bad_run = ["--course", C, "--target-exam", FINAL_22, "--run-id", f"r4-{FINAL_21}"]
    assert make_main(bad_run) == config.EXIT_HARD
    assert "must be r<seq>-" in json.loads(capsys.readouterr().out)["error"]
    assert make_main(["--course", C, "--target-exam", f"{C}-final-1999F", "--run-id", f"r1-{C}-final-1999F"]) == 1
    assert "not in the ledger" in json.loads(capsys.readouterr().out)["error"]
    assert make_main(["--course", "ZZ.200", "--target-exam", FINAL_22, "--run-id", f"r4-{FINAL_22}"]) == 1
    capsys.readouterr()
    missing = ["--course", C, "--target-exam", FINAL_22, "--run-id", f"r9-{FINAL_22}"]  # never built
    assert leakage_main(missing) == config.EXIT_HARD and signals_main(missing) == config.EXIT_HARD
    for line in capsys.readouterr().out.strip().splitlines():
        assert json.loads(line)["ok"] is False
    with pytest.raises(SystemExit) as exc:
        make_main([])
    assert exc.value.code == config.EXIT_HARD and json.loads(capsys.readouterr().out)["ok"] is False


# ================================================================== SQL plumbing
def test_every_sql_file_renders_for_hotdata():
    hot = HotdataBackend(client=object(), ledger_name="ledger")  # table_ref only; never touches the client
    params = {"course": C, "target_exam": FINAL_22, "exam_type": "final", "target_term_seq": 20223,
              "target_session": 20, "tau": 0.02, "win_from": 1, "win_to": 19, "stated_from": None, "stated_to": 19,
              "cumulative": True, "trust_cumulative": 0.5, "trust_emphasis_window": 0.5, "trust_coverage": 0.5,
              "run_seq": 4}
    names = [*SIGNAL_SQL.values(), "coverage_window", "baseline_even", "baseline_last_exam", "leakage",
             "guideline_trust"]
    queries = [load_sql(n) for n in names] + [select_sql(t) for t in RUN_TABLES]
    for sql in queries:
        rendered = render_params(render_tables(sql, hot.table_ref), params)
        assert '"default"."public".' in rendered and "{{" not in rendered and "@VISIBLE" not in rendered


def test_visible_macro_run_ids_and_run_db_refs():
    assert expand_visible("@VISIBLE(e.term_seq, e.session)") == ordering.visible_sql("term_seq", "session", alias="e")
    assert expand_visible("@VISIBLE(term_seq,session)") == ordering.visible_sql()
    for bad in ("@VISIBLE(a.term_seq, b.session)", "@VISIBLE(term_seq)", "@VISIBLE(x; y, z)"):
        with pytest.raises(ValueError):
            expand_visible(bad)
    assert run_seq_of(f"r12-{FINAL_22}", FINAL_22) == 12
    for bad in (f"r4-{FINAL_21}", f"x4-{FINAL_22}", f"r-{FINAL_22}"):
        with pytest.raises(RunError):
            run_seq_of(bad, FINAL_22)
    run_id = f"r4-{FINAL_22}"
    assert resolve_run_db(None, run_id) == resolve_run_db(f"run-{run_id}", run_id) == f"run-{run_id}"
    handle = DbHandle(name=f"run-{run_id}", backend="local")
    assert resolve_run_db(json.dumps({"ok": True, "run_db": handle.to_dict()}), run_id) == handle
    for other in (f"run-r5-{FINAL_22}", json.dumps(DbHandle(name="ledger", backend="local").to_dict())):
        with pytest.raises(RunError):
            resolve_run_db(other, run_id)
