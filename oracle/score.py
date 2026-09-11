"""Score a sealed prediction against the human answer key, and write the run's training labels.

Spec: kit/02-product/prediction-model.md "Scoring (per run)"; contracts/README.md "Ground truth" (the key is read
only after the seal; --allow-machine-key stamps key_source = machine); kit/02-product/data-sources-and-sealing.md
(answer_key.csv columns, topics ';'-separated, points split across a problem's topics, points sum to the printed
total); contracts/ledger-schema.sql (`run_labels`, written after the seal; refit trains only on these);
kit/02-product/student-layer.md (`guideline_tests`).

CLI:  python -m oracle.score --run-id r7-FX.101-final-2022F [--allow-machine-key]
Exit 0 ok | 1 hard fault (e.g. SEALED_DIR unset or inside the repo) | 3 invalid answer key (wrong exam_id, bad
points, topic outside the fixed list, points != exams.total_points) | 5 REFUSED: no <run_id>.sha256, or the
prediction file does not verify against it | 6 no human key SEALED_DIR/answer_key_<exam_id>.csv (and no machine key
allowed, or none in the ledger).

Metrics (shares of the exam's points; each key row's points are split equally across its distinct topics):
  model_pts    = sum of point shares whose topic is in the sealed top-K
  recall_k     = |exam topics in top-K| / |exam topics|, over the predicted topics (null if none is on the key)
  brier        = mean over ALL predicted topics of (p - y)^2, y = 1 if the topic is tagged anywhere on the key
  even_pts     = feature_set full: the same share for baselines.even; exam_history: the closed form
                 K/N * (1 - off_list_pts) (the expected share covered by a uniformly random K of the N predicted
                 topics -- lecture time is not visible); even_method says which was used
  lastexam_pts = the same share for baselines.last_exam
  off_list_pts = the share of points on T00, the off-list bucket (D6): allowed on the key, never predicted, and
                 kept in the denominator of every share above (so no top-K can cover it)
Guideline tests (D7), written only for a human key and never for a cold-start twin: every guideline of kind
homework_analogous that applies to the target's exam type and was stated before it is tested once per run:
observed_share = share of the exam's points on topics with >= 1 homework problem visible to the target;
verdict = match if >= 0.5, partial if >= 0.3, else miss (predicted_share records the 0.5 match threshold). With no
visible homework the claim cannot be tested and no row is written. guideline_trust.sql turns them into trust.
Order of operations: verify the seal -> only then locate and open the key (config.sealed_answer_key_path re-checks
the hash file). Writes run_labels (upsert on run_id, topic_id), guideline_tests (upsert on guideline_id, run_id)
and <run_id>.score.json next to the prediction (the printed object; oracle.append_ledger reads it). Re-scoring is
idempotent.
"""
from __future__ import annotations

import csv
import math
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from oracle import config, ordering
from oracle.backend import Backend, DbRef, get_backend
from oracle.rank_and_seal import (OFF_LIST_TOPIC, JsonArgumentParser, atomic_write, prediction_paths, pretty_json,
                                  read_sealed, round6, validate_prediction)

EXIT_UNSEALED = config.EXIT_UNSEALED   # 5: no seal, or the prediction does not verify: scoring refused
EXIT_NO_KEY = config.EXIT_NO_KEY       # 6: no human answer key (and no machine key allowed / available)
KEY_COLUMNS = ("exam_id", "problem", "points", "topic_ids")
KeyShares = list[tuple[str, str, float]]  # (problem part, topic_id, points on that topic)
HW_ANALOGOUS_MATCH = 0.5               # D7: observed share >= 0.5 -> match
HW_ANALOGOUS_PARTIAL = 0.3             # D7: 0.3 <= observed share < 0.5 -> partial, else miss

_EXAM_SQL = 'SELECT course, exam_type, term_seq, "session", total_points FROM {{exams}} WHERE exam_id = $exam_id'
_MACHINE_SQL = "SELECT problem, topic_id, points_share FROM {{exam_items}} WHERE exam_id = $exam_id"
_HW_GUIDELINES_SQL = ("SELECT guideline_id FROM {{guidelines}} WHERE course = $course "
                      "AND applies_to_exam_type = $exam_type AND kind = 'homework_analogous' AND "
                      + ordering.visible_sql("source_term_seq", "source_session") + " ORDER BY guideline_id")
_HW_TOPICS_SQL = ("SELECT DISTINCT topic_id FROM {{homework_items}} WHERE course = $course "
                  "AND topic_id <> $off_list AND " + ordering.visible_sql() + " ORDER BY topic_id")


class KeyInvalid(ValueError):
    """The answer key does not fit the prediction or the exam (exit 3)."""


class NoAnswerKey(RuntimeError):
    """No usable answer key (exit 6)."""


def score_path(run_id: str, directory: str | os.PathLike | None = None) -> Path:
    """<dir>/<run_id>.score.json, next to the sealed prediction."""
    return prediction_paths(run_id, directory)[0].with_name(f"{run_id}.score.json")


def read_answer_key(path: str | os.PathLike, exam_id: str, topic_ids: set[str]) -> KeyShares:
    """Parse a human answer key (exam_id, problem, sub, points, topic_ids, ...) into per-topic point shares.
    Topics must come from `topic_ids` or be the off-list bucket T00 (D6)."""
    path = Path(path)
    shares: KeyShares = []
    seen: set[str] = set()
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in KEY_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise KeyInvalid(f"{path.name}: missing columns {missing}")
        for line, row in enumerate(reader, 2):
            where = f"{path.name}:{line}"
            if (row.get("exam_id") or "").strip() != exam_id:
                raise KeyInvalid(f"{where}: exam_id {row.get('exam_id')!r} is not {exam_id!r}")
            problem, sub = (row.get("problem") or "").strip(), (row.get("sub") or "").strip()
            part = f"{problem}{sub}"
            if not problem or f"{problem}|{sub}" in seen:
                raise KeyInvalid(f"{where}: missing or duplicate problem {part!r}")
            seen.add(f"{problem}|{sub}")
            try:
                points = float(row.get("points") or "")
            except ValueError:
                raise KeyInvalid(f"{where}: points {row.get('points')!r} is not a number") from None
            if not math.isfinite(points) or points < 0:
                raise KeyInvalid(f"{where}: points must be a finite number >= 0")
            topics = list(dict.fromkeys(t.strip() for t in (row.get("topic_ids") or "").split(";") if t.strip()))
            unknown = [t for t in topics if t not in topic_ids and t != OFF_LIST_TOPIC]
            if not topics or unknown:
                raise KeyInvalid(f"{where}: topics must come from the course's fixed list (got {topics}, unknown {unknown})")
            shares += [(part, t, points / len(topics)) for t in topics]
    if not shares:
        raise KeyInvalid(f"{path.name}: the answer key has no rows")
    return shares


def machine_key(backend: Backend, ledger: DbRef, exam_id: str, topic_ids: set[str]) -> KeyShares:
    """The target's exam_items tags from the ledger (Cognee or human tags) as point shares; NOT a fair test."""
    rows = backend.query(ledger, _MACHINE_SQL, {"exam_id": exam_id})
    if rows.empty:
        raise NoAnswerKey(f"no human answer key and no exam_items tags for {exam_id} in the ledger")
    unknown = sorted(set(rows["topic_id"]) - topic_ids - {OFF_LIST_TOPIC})
    if unknown:
        raise KeyInvalid(f"exam_items of {exam_id} use topics outside the fixed list: {unknown}")
    return [(str(r.problem), str(r.topic_id), float(r.points_share)) for r in rows.itertuples(index=False)]


def compute_scores(prediction: Mapping[str, Any], key_shares: Sequence[tuple[str, str, float]]) -> dict[str, Any]:
    """Pure scoring of a prediction object against per-topic point shares (see the module docstring)."""
    topics = prediction["topics"]
    ids = [t["topic_id"] for t in topics]
    k, n = int(prediction["k"]), len(ids)
    top_k = [t["topic_id"] for t in topics if t["in_top_k"]]
    if len(top_k) != k:
        raise ValueError(f"prediction marks {len(top_k)} topics in_top_k but k = {k}")
    on_key: dict[str, float] = {}
    for _, tid, points in key_shares:
        if tid not in ids and tid != OFF_LIST_TOPIC:
            raise KeyInvalid(f"answer key topic {tid!r} is not in the prediction's topic list")
        on_key[tid] = on_key.get(tid, 0.0) + float(points)
    total = sum(on_key.values())  # T00's points stay in the denominator (D6)
    if not total > 0:
        raise KeyInvalid("the answer key has no points")
    key_points = {t: on_key.get(t, 0.0) / total for t in ids}
    off_list = 0.0 if OFF_LIST_TOPIC in ids else on_key.get(OFF_LIST_TOPIC, 0.0) / total
    y = {t: int(t in on_key) for t in ids}

    def covered(chosen: Sequence[str]) -> float:
        return sum(key_points[t] for t in set(chosen))

    if prediction["feature_set"] == "full":
        even, even_method = covered(prediction["baselines"]["even"]), "baseline_list"
    else:
        even, even_method = k / n * (1.0 - off_list), "closed_form_k_over_n"
    exam_topics = [t for t in ids if y[t]]
    recall = round6(len(set(exam_topics) & set(top_k)) / len(exam_topics)) if exam_topics else None
    return {"k": k, "n_topics": n, "top_k": top_k, "topics_on_exam": sorted(exam_topics), "total_points": round6(total),
            "model_pts": round6(covered(top_k)), "recall_k": recall,
            "brier": round6(sum((float(t["p"]) - y[t["topic_id"]]) ** 2 for t in topics) / n),
            "even_pts": round6(even), "even_method": even_method,
            "lastexam_pts": round6(covered(prediction["baselines"]["last_exam"])), "off_list_pts": round6(off_list),
            "key_points": {t: round6(v) for t, v in key_points.items()}, "y": y}


def label_rows(run_id: str, scores: Mapping[str, Any], key_source: str) -> list[dict[str, Any]]:
    """Rows of the ledger `run_labels` table, one per topic of the prediction."""
    return [{"run_id": run_id, "topic_id": t, "y": y, "key_points": scores["key_points"][t], "key_source": key_source}
            for t, y in scores["y"].items()]


def _exam_row(backend: Backend, ledger: DbRef, exam_id: str) -> dict[str, Any] | None:
    rows = backend.query(ledger, _EXAM_SQL, {"exam_id": exam_id})
    if len(rows) != 1:
        return None
    r = rows.iloc[0]
    return {"course": str(r["course"]), "exam_type": str(r["exam_type"]), "term_seq": int(r["term_seq"]),
            "session": None if pd.isna(r["session"]) else int(r["session"]),
            "total_points": None if pd.isna(r["total_points"]) else float(r["total_points"])}


def _check_printed_total(exam: Mapping[str, Any] | None, shares: KeyShares) -> None:
    printed = exam["total_points"] if exam is not None else None
    if printed is None:
        return
    total = sum(points for _, _, points in shares)
    if abs(total - printed) > 1e-6 * max(1.0, abs(printed)):
        raise KeyInvalid(f"answer key points sum to {total:g}, the exam's printed total is {printed:g}")


def homework_analogous_verdict(observed_share: float) -> str:
    """D7: match if >= 50% of the exam's points are on topics with visible homework, partial 30-50%, else miss."""
    if observed_share >= HW_ANALOGOUS_MATCH:
        return "match"
    return "partial" if observed_share >= HW_ANALOGOUS_PARTIAL else "miss"


def guideline_test_rows(backend: Backend, ledger: DbRef, run_id: str, exam: Mapping[str, Any],
                        key_points: Mapping[str, float]) -> tuple[list[dict[str, Any]], str | None]:
    """(guideline_tests rows, note) for the target's applicable homework_analogous guidelines stated before it."""
    params = {"course": exam["course"], "exam_type": exam["exam_type"], "off_list": OFF_LIST_TOPIC,
              **ordering.visibility_params(exam["term_seq"], exam["session"])}
    guideline_ids = [str(g) for g in backend.query(ledger, _HW_GUIDELINES_SQL, params)["guideline_id"]]
    if not guideline_ids:
        return [], None
    with_homework = {str(t) for t in backend.query(ledger, _HW_TOPICS_SQL, params)["topic_id"]}
    if not with_homework:
        return [], "no homework is visible to the target, so its homework_analogous guidelines cannot be tested"
    observed = round6(sum(v for t, v in key_points.items() if t in with_homework))
    verdict = homework_analogous_verdict(observed)
    return [{"guideline_id": g, "run_id": run_id, "predicted_share": HW_ANALOGOUS_MATCH, "observed_share": observed,
             "verdict": verdict} for g in guideline_ids], None


def score_run(run_id: str, *, allow_machine_key: bool = False, backend: Backend | None = None,
              directory: str | os.PathLike | None = None,
              sealed_dir: str | os.PathLike | None = None) -> dict[str, Any]:
    """Verify the seal, then score; writes run_labels, guideline_tests and <run_id>.score.json; returns the
    printed object."""
    prediction, digest = read_sealed(run_id, directory)  # SealBroken (exit 5) before anything sealed is touched
    validate_prediction(prediction)
    exam_id = prediction["target_exam"]
    ids = {t["topic_id"] for t in prediction["topics"]}
    key_path = config.sealed_answer_key_path(exam_id, hash_file=prediction_paths(run_id, directory)[1],
                                             sealed_dir=sealed_dir)
    be = backend or get_backend()
    ledger = be.ensure_ledger()
    exam = _exam_row(be, ledger, exam_id)
    out: dict[str, Any] = {"ok": True, "run_id": run_id, "exam_id": exam_id, "hash": digest,
                           "feature_set": prediction["feature_set"]}
    if key_path.is_file():
        shares, key_source = read_answer_key(key_path, exam_id, ids), "human"
        _check_printed_total(exam, shares)
    elif allow_machine_key:
        shares, key_source = machine_key(be, ledger, exam_id, ids), "machine"
        out["warning"] = "machine-graded: scored against the ledger's exam_items tags, not a human answer key"
    else:
        raise NoAnswerKey(f"no human answer key {key_path.name} in SEALED_DIR; label one, or pass --allow-machine-key")
    scores = compute_scores(prediction, shares)
    be.load_table(ledger, "run_labels", label_rows(run_id, scores, key_source), mode="upsert")

    tests: list[dict[str, Any]] = []
    note: str | None = None
    if key_source != "human":
        note = "machine key: guideline tests need a human answer key (the AI never grades itself)"
    elif prediction.get("cold_start"):
        note = "cold-start twin: a comparison run is not evidence, so it writes no guideline tests"
    elif exam is not None:
        tests, note = guideline_test_rows(be, ledger, run_id, exam, scores["key_points"])
    if tests:
        be.load_table(ledger, "guideline_tests", tests, mode="upsert")

    out.update({"key_source": key_source, **{k: v for k, v in scores.items() if k not in ("key_points", "y")}})
    out["guideline_tests"] = [{k: t[k] for k in ("guideline_id", "observed_share", "verdict")} for t in tests]
    if note:
        out["guideline_tests_note"] = note
    if prediction["feature_set"] != "full":
        out["even_note"] = "exam_history run: lecture time is not visible, so even_pts is the closed form k/N"
    atomic_write(score_path(run_id, directory), pretty_json(out))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(prog="python -m oracle.score",
                                description="Score a sealed prediction against its answer key (after the seal only).")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--allow-machine-key", action="store_true",
                        help="without a human key, score against the ledger's exam_items tags (key_source=machine)")
    args = parser.parse_args(argv)
    try:
        out = score_run(args.run_id, allow_machine_key=args.allow_machine_key)
    except config.SealViolation as exc:
        config.emit({"ok": False, "refused": True, "error": str(exc)})
        return EXIT_UNSEALED
    except NoAnswerKey as exc:
        config.emit({"ok": False, "error": str(exc)})
        return EXIT_NO_KEY
    except KeyInvalid as exc:
        config.emit({"ok": False, "error": f"invalid answer key: {exc}"})
        return config.EXIT_VALIDATION
    except Exception as exc:  # every other failure is a hard fault of this step
        config.emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return config.EXIT_HARD
    config.emit(out)
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
