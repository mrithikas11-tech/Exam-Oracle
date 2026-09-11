"""rank_and_seal, score and append_ledger: offline, on the synthetic fixture course FX.101.

Covers the seal (hash determinism, formatting independence, tamper detection, no re-sealing), the schema, K and both
baselines, the leakage guards, hand-computed metrics with multi-topic problems, the closed-form even baseline, the
refusal exits (5 = unsealed/tampered, 6 = no answer key) and the ledger rows (predictions, run_labels, runs)."""
from __future__ import annotations

import io
import json
import math
import os
import statistics
import subprocess
import sys

import pandas as pd
import pytest

from conftest import build_fixture_ledger
from oracle import append_ledger as al
from oracle import config
from oracle import rank_and_seal as rs
from oracle import score as sc
from oracle.backend import columns
from oracle.lessons_store import COLD_START_WEIGHTS, get_lessons_store, predict_p

MADE_AT = "2026-09-11T13:00:00-07:00"
FINAL22, FINAL21, QUIZ21, QUIZ20 = "FX.101-final-2022F", "FX.101-final-2021F", "FX.101-quiz1-2021F", "FX.101-quiz1-2020F"
X1 = {"T01": 0.2, "T02": 0.9, "T03": 0.1, "T04": 1.0, "T05": 0.8, "T06": 1.0, "T07": 0.7, "T08": 0.4}
LECTURES = {"T01": 2, "T02": 3, "T03": 2, "T04": 2, "T05": 2, "T06": 3, "T07": 2, "T08": 1}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A private LOCAL_DIR holding the fixture ledger (predictions, lessons and labels land there too)."""
    monkeypatch.setenv("ORACLE_LOCAL_DIR", str(tmp_path / "lb"))
    return build_fixture_ledger()


def full_signals(**overrides) -> dict:
    return {"course": "FX.101", "target_exam": FINAL22, "feature_set": "full",
            "signals": [{"topic_id": t, "x1": X1[t], "x2": 1.0, "x3": 0.0, "x4": LECTURES[t] / 17, "x5": 0, "x6": 0,
                         "x7": 0.0, **overrides.get(t, {})} for t in X1]}


def history_signals() -> dict:
    return {"signals": {t: {"x1": 0.3 if t < "T04" else 0.0, "x7": 0.0} for t in X1}}  # mapping form, x2..x6 omitted


def cli(module, args, capsys) -> tuple[int, dict]:
    code = module.main(args)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1, lines
    return code, json.loads(lines[0])


def seal_run(tmp_path, capsys, run_id, exam, signals, *extra) -> tuple[int, dict]:
    path = tmp_path / f"{run_id}.signals.json"
    path.write_text(json.dumps(signals))
    return cli(rs, ["--run-id", run_id, "--course", "FX.101", "--target-exam", exam, "--signals", str(path),
                    "--made-at", MADE_AT, *extra], capsys)


def hand_prediction(feature_set: str = "full") -> dict:
    """5 topics, k = 2, ranked by p; used with the hand-labeled key in test_metrics_*."""
    ps = [("T01", 0.9), ("T02", 0.6), ("T03", 0.3), ("T05", 0.2), ("T04", 0.1)]
    return {"k": 2, "feature_set": feature_set,
            "topics": [{"topic_id": t, "p": p, "rank": r, "in_top_k": r <= 2} for r, (t, p) in enumerate(ps, 1)],
            "baselines": {"even": ["T03", "T04"] if feature_set == "full" else [], "last_exam": ["T02", "T05"]}}


HAND_KEY = ("exam_id,problem,sub,points,topic_ids,labeled_by,labeled_at\n"
            "X-final-2020F,1,,10,T01,t,\n"
            "X-final-2020F,2,a,20,T02;T03;T02,t,\n"          # duplicate tag counts once: 10 + 10
            "X-final-2020F,2,b,10,T03;T04;T01,t,\n")         # three topics: 10/3 each


# ================================================================== the seal
def test_hash_is_independent_of_key_order_and_file_formatting(lab, tmp_path, capsys):
    assert rs.prediction_hash({"a": 1, "b": [1, {"c": 2.5, "d": "é"}]}) == \
        rs.prediction_hash({"b": [1, {"d": "é", "c": 2.5}], "a": 1})
    code, out = seal_run(tmp_path, capsys, "r7-" + FINAL22, FINAL22, full_signals())
    assert code == 0, out
    path, hash_path = rs.prediction_paths(out["run_id"])
    obj = json.loads(path.read_text())

    def reverse(value):
        if isinstance(value, dict):
            return {k: reverse(value[k]) for k in reversed(list(value))}
        return [reverse(v) for v in value] if isinstance(value, list) else value

    assert rs.prediction_hash(reverse(obj)) == rs.prediction_hash(obj) == out["hash"]
    assert hash_path.read_text() == out["hash"] + "\n"
    assert config.sha256_hex(path.read_bytes()) != out["hash"]          # the hash is of the canonical form, not the file
    path.write_text(json.dumps(reverse(obj)))                           # re-formatted, same values: still sealed
    assert rs.verify_hash(path)


def test_tamper_is_detected_and_scoring_refused(lab, tmp_path, capsys):
    run_id = "r7-" + FINAL22
    assert seal_run(tmp_path, capsys, run_id, FINAL22, full_signals())[0] == 0
    path, hash_path = rs.prediction_paths(run_id)
    obj = json.loads(path.read_text())
    obj["topics"][-1]["p"] = 0.99
    path.write_text(json.dumps(obj, indent=2))
    assert not rs.verify_hash(path)
    with pytest.raises(rs.SealBroken):
        rs.read_sealed(run_id)
    code, out = cli(sc, ["--run-id", run_id], capsys)
    assert code == sc.EXIT_UNSEALED and out["refused"] and not out["ok"]
    assert al.main(["--run-id", run_id, "--leakage-ok", "true"]) == sc.EXIT_UNSEALED
    hash_path.write_text("not-a-hash\n")
    assert not rs.verify_hash(path) and sc.main(["--run-id", run_id]) == sc.EXIT_UNSEALED
    hash_path.unlink()
    assert not rs.verify_hash(path) and sc.main(["--run-id", run_id]) == sc.EXIT_UNSEALED
    capsys.readouterr()


def test_score_cli_refuses_an_unsealed_run_as_a_module(tmp_path):
    env = {**os.environ, "ORACLE_SKIP_DOTENV": "1", "ORACLE_LOCAL_DIR": str(tmp_path / "lb")}
    done = subprocess.run([sys.executable, "-m", "oracle.score", "--run-id", "r9-" + FINAL22], cwd=config.REPO_ROOT,
                          env=env, capture_output=True, text=True, timeout=120)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    assert done.returncode == sc.EXIT_UNSEALED and len(lines) == 1, done.stdout + done.stderr
    assert json.loads(lines[0])["refused"] is True


def test_a_sealed_run_is_never_resealed(lab, tmp_path, capsys):
    run_id = "r7-" + FINAL22
    code, first = seal_run(tmp_path, capsys, run_id, FINAL22, full_signals())
    assert code == 0 and first["already_sealed"] is False
    signals_path = tmp_path / "same.json"
    signals_path.write_text(json.dumps(full_signals()))
    code, again = cli(rs, ["--run-id", run_id, "--course", "FX.101", "--target-exam", FINAL22, "--signals",
                           str(signals_path)], capsys)               # no --made-at: the sealed timestamp is reused
    assert code == 0 and again["already_sealed"] and (again["hash"], again["made_at"]) == (first["hash"], MADE_AT)
    code, out = seal_run(tmp_path, capsys, run_id, FINAL22, full_signals(T03={"x3": 1.0}))
    assert code == config.EXIT_HARD and "ResealRefused" in out["error"]
    assert rs.read_sealed(run_id)[1] == first["hash"]                 # the sealed file is untouched


# ================================================================== the prediction
def test_prediction_object_schema_ranks_k_and_baselines(lab, tmp_path, capsys):
    be, ledger = lab
    code, out = seal_run(tmp_path, capsys, "r7-" + FINAL22, FINAL22, full_signals())
    assert code == 0, out
    obj, digest = rs.read_sealed(out["run_id"])
    rs.validate_prediction(obj)
    rs.validate_prediction(json.loads((config.FIXTURES_DIR / "prediction.json").read_text()))
    assert (obj["k"], out["k_source"], obj["feature_set"], obj["lessons_run_seq"]) == (5, "course_same_type", "full", None)
    assert obj["weights"] == COLD_START_WEIGHTS and obj["cold_start"] is False
    assert obj["standardization"] == out["standardization"] == "zscore_per_run_v1"
    assert [t["rank"] for t in obj["topics"]] == list(range(1, 9))
    # D5, recomputed independently: z = (x - mean) / population std across the 8 topics; x2 (all 1.0) and x3, x5..x7
    # (all 0) have zero variance -> 0, so p = sigma(-1.2 + 0.5 z1 + 0.5 z4). The object keeps the RAW signals.
    ids = sorted(X1)
    z1 = [(X1[t] - statistics.fmean(X1.values())) / statistics.pstdev(X1.values()) for t in ids]
    x4 = [round(LECTURES[t] / 17, 6) for t in ids]
    z4 = [(v - statistics.fmean(x4)) / statistics.pstdev(x4) for v in x4]
    want = {t: 1 / (1 + math.exp(-(-1.2 + 0.5 * a + 0.5 * b))) for t, a, b in zip(ids, z1, z4)}
    got = {t["topic_id"]: t["p"] for t in obj["topics"]}
    assert got == pytest.approx(want, abs=2e-6) and rs.prediction_p(obj) == got
    assert got["T06"] == pytest.approx(0.517633, abs=2e-6)          # sigma(-1.2 + 0.5*1.0815 + 0.5*1.4596)
    for t in obj["topics"]:
        assert t["signals"]["x1"] == X1[t["topic_id"]] and t["in_top_k"] == (t["rank"] <= 5)
    assert predict_p(obj["weights"], obj["topics"][0]["signals"]) != obj["topics"][0]["p"]   # not the raw formula
    assert out["top_k"] == ["T06", "T02", "T04", "T05", "T07"]      # z1 + z4: 2.54, 2.24, 0.87, 0.28, -0.02
    assert obj["baselines"] == {"even": ["T02", "T06", "T01", "T03", "T04"],      # x4 desc, ties by topic_id
                                "last_exam": ["T04", "T05", "T06", "T07", "T08"]}  # final-2021F by points
    assert out["last_exam_copied"] == FINAL21
    rows = be.query(ledger, "SELECT * FROM {{predictions}} WHERE run_id = $r ORDER BY rank", {"r": out["run_id"]})
    assert len(rows) == 8 and set(rows["hash"]) == {digest} and int(rows["in_top_k"].sum()) == 5
    assert rows["topic_id"].tolist()[:5] == out["top_k"] and set(rows["feature_set"]) == {"full"}
    for bad in ({**obj, "extra": 1}, {**obj, "weights": {"intercept": 0}}, {**obj, "k": 0},
                {**obj, "topics": [{**obj["topics"][0], "p": 1.5}]}):
        with pytest.raises(ValueError):
            rs.validate_prediction(bad)


def test_k_choice_and_fallbacks(lab, tmp_path, capsys):
    counts = pd.DataFrame({"course": ["A", "A", "B", "B"], "exam_type": ["final", "final", "final", "quiz1"],
                           "n_topics": [3, 4, 9, 2]})
    assert rs.choose_k(counts, "A", "final", 20) == (4, "course_same_type")        # median 3.5 rounds half up
    assert rs.choose_k(counts, "C", "final", 20) == (4, "all_courses_same_type")   # median of 3, 4, 9
    assert rs.choose_k(counts, "C", "midterm", 20) == (4, "all_visible_exams")     # median of 2, 3, 4, 9 = 3.5
    assert rs.choose_k(counts, "A", "final", 2) == (2, "course_same_type")         # clamped to N
    assert rs.choose_k(counts.iloc[0:0], "A", "final", 8) == (1, "floor")
    code, out = seal_run(tmp_path, capsys, "r1-" + QUIZ20, QUIZ20, history_signals())
    assert code == 0 and (out["k"], out["k_source"]) == (1, "floor")              # nothing visible before 2020F
    assert out["baselines"] == {"even": [], "last_exam": ["T01"]}                  # padded by x4 (all 0), topic_id


# ================================================================== leakage guards and inputs
def test_leakage_guards_exit_4(lab, tmp_path, capsys):
    earlier = history_signals()
    earlier["signals"]["T02"]["x6"] = 0.5          # D3: no forced zeros on exam_history; run_signals' visibility decides
    code, out = seal_run(tmp_path, capsys, "r2-" + FINAL21, FINAL21, earlier)
    assert code == 0 and out["feature_set"] == "exam_history"
    sealed = {t["topic_id"]: t["signals"] for t in rs.read_sealed(out["run_id"])[0]["topics"]}
    assert sealed["T02"]["x6"] == 0.5 and sealed["T01"]["x6"] == 0.0
    weights = {**COLD_START_WEIGHTS, "x1": 1.5}
    get_lessons_store().write(7, weights, {}, ["r6-" + QUIZ21])
    code, out = seal_run(tmp_path, capsys, "r7-" + FINAL22, FINAL22, full_signals())
    assert code == config.EXIT_LEAKAGE and "run_seq 7" in out["error"]
    code, out = seal_run(tmp_path, capsys, "r8-" + FINAL22, FINAL22, full_signals())
    assert code == 0 and out["lessons_run_seq"] == 7
    assert rs.read_sealed(out["run_id"])[0]["weights"] == weights
    code, out = seal_run(tmp_path, capsys, "r5-" + FINAL22, FINAL22, full_signals(), "--cold-start")
    assert code == 0 and out["cold_start"] and out["lessons_run_seq"] is None      # the blank-weights twin
    assert not list(rs.predictions_dir().glob("r7-*"))                           # the refused run left nothing


def test_signal_shapes_and_bad_inputs(lab, tmp_path, capsys, monkeypatch):
    rows = [{"topic_id": "T01", "x1": 1}, {"topic_id": "T02", "signals": {"x4": 0.5, "x7": None}}]
    parsed, meta = rs.parse_signals({"rows": rows, "course": "FX.101"})
    assert parsed["T01"] == {**{f"x{i}": 0.0 for i in range(1, 8)}, "x1": 1.0} and parsed["T02"]["x4"] == 0.5
    assert meta == {"course": "FX.101"} and rs.parse_signals(rows)[0] == parsed
    for bad in ([{"topic_id": "T01"}, {"topic_id": "T01"}], [{"topic_id": "T01", "x1": float("nan")}],
                [{"topic_id": "T01", "x1": True}], [{"topic_id": "T 1"}], {"signals": [], "topics": []}, "x"):
        with pytest.raises(ValueError):
            rs.parse_signals(bad)
    missing = full_signals()
    missing["signals"] = missing["signals"][:-1]
    for signals, run_id, exam in ((missing, "r7-" + FINAL22, FINAL22),
                                  ({**full_signals(), "target_exam": FINAL21}, "r7-" + FINAL22, FINAL22),
                                  (full_signals(), "r7-" + FINAL21, FINAL22),          # run_id of another exam
                                  (full_signals(), "r7-FX.101-final-2030F", "FX.101-final-2030F")):
        code, out = seal_run(tmp_path, capsys, run_id, exam, signals)
        assert code == config.EXIT_HARD and not out["ok"], out
    code, out = seal_run(tmp_path, capsys, "r7-" + FINAL22, FINAL22, full_signals(),
                         "--made-at", "2026-09-11T13:00:00")                        # the last --made-at wins: no offset
    assert code == config.EXIT_HARD and "offset" in out["error"]
    with pytest.raises(SystemExit) as exited:                                      # usage error: JSON + exit 1, not 2
        rs.main(["--run-id", "r7-" + FINAL22])
    assert exited.value.code == config.EXIT_HARD
    assert json.loads(capsys.readouterr().out)["error"].startswith("usage")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(full_signals())))
    code, out = cli(rs, ["--run-id", "r7-" + FINAL22, "--course", "FX.101", "--target-exam", FINAL22,
                         "--signals", "-", "--made-at", MADE_AT], capsys)
    assert code == 0 and out["k"] == 5


# ================================================================== scoring
def test_metrics_on_a_hand_labeled_key_with_multi_topic_problems(tmp_path):
    key = tmp_path / "answer_key_X-final-2020F.csv"
    key.write_text(HAND_KEY)
    shares = sc.read_answer_key(key, "X-final-2020F", {"T01", "T02", "T03", "T04", "T05"})
    s = sc.compute_scores(hand_prediction(), shares)
    # points: T01 = 10 + 10/3, T02 = 10, T03 = 10 + 10/3, T04 = 10/3, T05 = 0; total 40
    assert s["total_points"] == 40.0 and s["topics_on_exam"] == ["T01", "T02", "T03", "T04"]
    assert s["key_points"] == pytest.approx({"T01": 1 / 3, "T02": 0.25, "T03": 1 / 3, "T04": 1 / 12, "T05": 0.0}, abs=1e-6)
    assert s["y"] == {"T01": 1, "T02": 1, "T03": 1, "T05": 0, "T04": 1}
    assert s["model_pts"] == pytest.approx(7 / 12, abs=1e-6)                   # T01 + T02
    assert s["recall_k"] == 0.5                                                # 2 of 4 exam topics
    assert s["brier"] == pytest.approx((0.01 + 0.16 + 0.49 + 0.04 + 0.81) / 5, abs=1e-6)
    assert s["even_pts"] == pytest.approx(5 / 12, abs=1e-6) and s["even_method"] == "baseline_list"
    assert s["lastexam_pts"] == pytest.approx(0.25, abs=1e-6)
    bad = tmp_path / "bad.csv"
    for text in (HAND_KEY.replace("T04;T01", "T09"), HAND_KEY.replace(",10,T01,", ",ten,T01,"),
                 HAND_KEY + "X-final-2020F,1,,5,T05,t,\n", HAND_KEY.replace("X-final-2020F,1,", "Y-final-2020F,1,"),
                 HAND_KEY.replace(",T01,t,", ",,t,")):
        bad.write_text(text)
        with pytest.raises(sc.KeyInvalid):
            sc.read_answer_key(bad, "X-final-2020F", {"T01", "T02", "T03", "T04", "T05"})


def test_closed_form_even_baseline_for_exam_history(lab, tmp_path, capsys):
    s = sc.compute_scores(hand_prediction("exam_history"), [("1", "T01", 10.0)])
    assert s["even_pts"] == pytest.approx(2 / 5) and s["even_method"] == "closed_form_k_over_n"
    assert seal_run(tmp_path, capsys, "r1-" + QUIZ21, QUIZ21, history_signals())[0] == 0
    code, out = cli(sc, ["--run-id", "r1-" + QUIZ21], capsys)
    assert code == 0 and out["feature_set"] == "exam_history" and out["k"] == 3
    assert out["even_pts"] == 0.375 and out["even_method"] == "closed_form_k_over_n" and "k/N" in out["even_note"]
    assert out["model_pts"] == 1.0 and out["lastexam_pts"] == 1.0            # top-3 {T01,T02,T03} covers T02 + T03


def test_full_chain_writes_labels_and_the_runs_row(lab, tmp_path, capsys):
    be, ledger = lab
    run_id = "r7-" + FINAL22
    assert seal_run(tmp_path, capsys, run_id, FINAL22, full_signals())[0] == 0
    code, out = cli(al, ["--run-id", run_id, "--leakage-ok", "true"], capsys)
    assert code == config.EXIT_HARD and "not scored" in out["error"]
    code, scored = cli(sc, ["--run-id", run_id], capsys)
    assert code == 0 and scored["key_source"] == "human" and "warning" not in scored
    assert (scored["model_pts"], scored["even_pts"], scored["lastexam_pts"]) == (0.8, 0.5, 0.8)
    assert scored["recall_k"] == pytest.approx(5 / 6, abs=1e-6) and scored["total_points"] == 100.0
    obj, digest = rs.read_sealed(run_id)
    y = {t: int(t in {"T02", "T04", "T05", "T06", "T07", "T08"}) for t in X1}
    assert scored["brier"] == pytest.approx(sum((t["p"] - y[t["topic_id"]]) ** 2 for t in obj["topics"]) / 8, abs=1e-6)
    assert sc.main(["--run-id", run_id]) == 0                                 # re-scoring is idempotent
    capsys.readouterr()
    labels = be.query(ledger, "SELECT * FROM {{run_labels}} WHERE run_id = $r", {"r": run_id})
    assert len(labels) == 8 and dict(zip(labels.topic_id, labels.y)) == y
    assert labels.key_points.sum() == pytest.approx(1.0) and set(labels.key_source) == {"human"}

    assert al.main(["--run-id", run_id, "--leakage-ok", "true", "--seconds", "2.5", "--checks-first-try", "yes"]) == 0
    capsys.readouterr()
    code, out = cli(al, ["--run-id", run_id, "--leakage-ok", "true", "--tokens", "0", "--score-json",
                         str(sc.score_path(run_id))], capsys)
    assert code == 0 and set(out["row"]) == {c.name for c in columns("runs")}
    runs = be.query(ledger, "SELECT * FROM {{runs}} WHERE run_id = $r", {"r": run_id})
    assert len(runs) == 1                                                     # upsert on run_id
    row = runs.iloc[0]
    assert (row.run_seq, row.k, row.tokens, row.hash, row.made_at, row.key_source) == (7, 5, 0, digest, MADE_AT, "human")
    assert bool(row.leakage_ok) and not bool(row.cold_start) and pd.isna(row.seconds) and pd.isna(row.lessons_run_seq)
    other = {**scored, "hash": "0" * 64}
    with pytest.raises(ValueError):
        al.append_run(run_id, leakage_ok=True, score=other)


def test_missing_key_exit_6_and_machine_key(lab, tmp_path, capsys, monkeypatch):
    be, ledger = lab
    assert seal_run(tmp_path, capsys, "r2-" + FINAL21, FINAL21, history_signals())[0] == 0
    assert seal_run(tmp_path, capsys, "r7-" + FINAL22, FINAL22, full_signals())[0] == 0
    empty = tmp_path / "sealed-empty"
    empty.mkdir()
    monkeypatch.setenv("SEALED_DIR", str(empty))
    code, out = cli(sc, ["--run-id", "r2-" + FINAL21], capsys)
    assert code == sc.EXIT_NO_KEY and "--allow-machine-key" in out["error"]
    code, out = cli(sc, ["--run-id", "r2-" + FINAL21, "--allow-machine-key"], capsys)
    assert code == 0 and out["key_source"] == "machine" and "machine-graded" in out["warning"]
    labels = be.query(ledger, "SELECT DISTINCT key_source FROM {{run_labels}} WHERE run_id = $r", {"r": "r2-" + FINAL21})
    assert labels.key_source.tolist() == ["machine"]
    assert cli(al, ["--run-id", "r2-" + FINAL21, "--leakage-ok", "true"], capsys)[1]["row"]["key_source"] == "machine"
    code, out = cli(sc, ["--run-id", "r7-" + FINAL22, "--allow-machine-key"], capsys)
    assert code == sc.EXIT_NO_KEY                                             # sealed exam: no exam_items either
    monkeypatch.setenv("SEALED_DIR", str(config.REPO_ROOT / "data"))
    assert cli(sc, ["--run-id", "r7-" + FINAL22], capsys)[0] == config.EXIT_HARD  # SEALED_DIR inside the repo


def _sealed_key(tmp_path, monkeypatch, exam: str, text: str) -> None:
    sealed = tmp_path / "sealed-keys"
    sealed.mkdir(exist_ok=True)
    monkeypatch.setenv("SEALED_DIR", str(sealed))
    (sealed / f"answer_key_{exam}.csv").write_text(text)


def test_off_list_topic_T00_is_never_ranked_but_stays_in_the_denominator(lab, tmp_path, capsys, monkeypatch):
    be, ledger = lab
    be.load_table(ledger, "topics", [{"course": "FX.101", "topic_id": "T00", "topic": "Off-list",
                                      "first_lecture": None, "last_lecture": None}], mode="upsert")
    be.load_table(ledger, "exam_items", [{"course": "FX.101", "exam_id": FINAL21, "exam_type": "final", "term": "2021F",
                                          "term_seq": 20213, "session": 20, "problem": "5", "points": 10.0,
                                          "topic_id": "T00", "points_share": 10.0, "tag_source": "human",
                                          "text_clean": None}], mode="upsert")
    signals = full_signals()
    signals["signals"].append({"topic_id": "T00", "x1": 1.0})        # a stray T00 row is dropped, never ranked
    code, out = seal_run(tmp_path, capsys, "r7-" + FINAL22, FINAL22, signals)
    assert code == 0, out
    obj = rs.read_sealed(out["run_id"])[0]
    assert [t["topic_id"] for t in obj["topics"]] == out["top_k"] + ["T01", "T03", "T08"] and len(obj["topics"]) == 8
    # K: final-2020F has 5 topics, final-2021F 5 + T00; counting T00 would give median(5, 6) -> 6
    assert (out["k"], out["k_source"]) == (5, "course_same_type")
    assert obj["baselines"]["last_exam"] == ["T04", "T05", "T06", "T07", "T08"]   # T00's 10 points never copied
    # the key splits problem 1 (20 pts) over T06 and T00: shares are of all 100 points, T00's 10 included
    good = (config.FIXTURE_SEALED_DIR / f"answer_key_{FINAL22}.csv").read_text()
    _sealed_key(tmp_path, monkeypatch, FINAL22, good.replace(",20,T06,", ",20,T06;T00,"))
    code, scored = cli(sc, ["--run-id", out["run_id"]], capsys)
    assert code == 0, scored
    assert scored["off_list_pts"] == 0.1 and scored["total_points"] == 100.0
    # top-5 {T06, T02, T04, T05, T07}: 0.1 + 0.2 + 0.1 + 0.1 + 0.2 (0.7 / 0.9 = 0.78 if T00 left the denominator)
    assert scored["model_pts"] == pytest.approx(0.7) and scored["recall_k"] == pytest.approx(5 / 6)
    assert scored["even_pts"] == pytest.approx(0.4) and scored["lastexam_pts"] == pytest.approx(0.7)
    labels = be.query(ledger, "SELECT * FROM {{run_labels}} WHERE run_id = $r", {"r": out["run_id"]})
    assert len(labels) == 8 and "T00" not in set(labels.topic_id) and labels.key_points.sum() == pytest.approx(0.9)
    # closed form for exam_history: a random K of the N predicted topics covers K/N of the NON-off-list points
    s = sc.compute_scores(hand_prediction("exam_history"), [("1", "T01", 10.0), ("2", "T00", 10.0)])
    assert (s["even_pts"], s["off_list_pts"], s["model_pts"], s["recall_k"]) == (0.2, 0.5, 0.5, 1.0)
    assert sc.compute_scores(hand_prediction(), [("1", "T00", 5.0)])["recall_k"] is None   # no predicted topic on it


def test_homework_analogous_guideline_tests_are_written_at_scoring(lab, tmp_path, capsys, monkeypatch):
    be, ledger = lab
    be.load_table(ledger, "guidelines", [{"course": "FX.101", "guideline_id": "G4", "kind": "homework_analogous",
                                          "applies_to_exam_type": "final", "from_session": None, "to_session": None,
                                          "share": None, "source_term": "2022F", "source_term_seq": 20223,
                                          "source_session": 1, "text": "Exam problems are analogous to homework.",
                                          "source_url": None}], mode="upsert")
    assert [sc.homework_analogous_verdict(v) for v in (0.5, 0.49, 0.3, 0.29)] == ["match", "partial", "partial", "miss"]
    assert seal_run(tmp_path, capsys, "r7-" + FINAL22, FINAL22, full_signals())[0] == 0
    assert seal_run(tmp_path, capsys, "r8-" + FINAL22, FINAL22, full_signals(), "--cold-start")[0] == 0
    # 60 of 100 points on T00; every predicted topic has a visible pset (due sessions 3..18) -> observed 0.4
    key = "exam_id,problem,sub,points,topic_ids,labeled_by,labeled_at\n" + "".join(
        f"{FINAL22},{n},,20,{t},t,\n" for n, t in ((1, "T00"), (2, "T00"), (3, "T00"), (4, "T08"), (5, "T02")))
    _sealed_key(tmp_path, monkeypatch, FINAL22, key)
    code, scored = cli(sc, ["--run-id", "r7-" + FINAL22], capsys)
    assert code == 0 and scored["guideline_tests"] == [{"guideline_id": "G4", "observed_share": 0.4,
                                                        "verdict": "partial"}]
    code, twin = cli(sc, ["--run-id", "r8-" + FINAL22], capsys)
    assert code == 0 and twin["guideline_tests"] == [] and "cold-start twin" in twin["guideline_tests_note"]
    rows = be.query(ledger, "SELECT * FROM {{guideline_tests}}")
    assert rows[["guideline_id", "run_id", "predicted_share", "observed_share", "verdict"]].values.tolist() == \
        [["G4", "r7-" + FINAL22, 0.5, 0.4, "partial"]]


def test_invalid_answer_key_exit_3(lab, tmp_path, capsys, monkeypatch):
    assert seal_run(tmp_path, capsys, "r7-" + FINAL22, FINAL22, full_signals())[0] == 0
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    monkeypatch.setenv("SEALED_DIR", str(sealed))
    good = (config.FIXTURE_SEALED_DIR / f"answer_key_{FINAL22}.csv").read_text()
    for text in (good.replace(",T08,", ",T99,"), good.replace(",20,T02,", ",0,T02,")):   # unknown topic; 80 != 100
        (sealed / f"answer_key_{FINAL22}.csv").write_text(text)
        code, out = cli(sc, ["--run-id", "r7-" + FINAL22], capsys)
        assert code == config.EXIT_VALIDATION and "invalid answer key" in out["error"]
