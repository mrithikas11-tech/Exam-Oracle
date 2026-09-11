"""Score a sealed prediction against the human answer key, and write the run's training labels.

Spec: kit/02-product/prediction-model.md "Scoring (per run)"; contracts/README.md "Ground truth" (the key is read
only after the seal; --allow-machine-key stamps key_source = machine); kit/02-product/data-sources-and-sealing.md
(answer_key.csv columns, topics ';'-separated, points split across a problem's topics, points sum to the printed
total); contracts/ledger-schema.sql (`run_labels`, written after the seal; refit trains only on these).

CLI:  python -m oracle.score --run-id r7-FX.101-final-2022F [--allow-machine-key]
Exit 0 ok | 1 hard fault (e.g. SEALED_DIR unset or inside the repo) | 3 invalid answer key (wrong exam_id, bad
points, topic outside the fixed list, points != exams.total_points) | 5 REFUSED: no <run_id>.sha256, or the
prediction file does not verify against it | 6 no human key SEALED_DIR/answer_key_<exam_id>.csv (and no machine key
allowed, or none in the ledger).

Metrics (shares of the exam's points; each key row's points are split equally across its distinct topics):
  model_pts    = sum of point shares whose topic is in the sealed top-K
  recall_k     = |exam topics in top-K| / |exam topics|
  brier        = mean over ALL topics of (p - y)^2, y = 1 if the topic is tagged anywhere on the key
  even_pts     = feature_set full: the same share for baselines.even; exam_history: the closed form K/N (the expected
                 share covered by a uniformly random K of the N topics — lecture time is not visible); even_method
                 says which was used
  lastexam_pts = the same share for baselines.last_exam
Order of operations: verify the seal -> only then locate and open the key (config.sealed_answer_key_path re-checks
the hash file). Writes run_labels (upsert on run_id, topic_id) and <run_id>.score.json next to the prediction (the
printed object; oracle.append_ledger reads it). Re-scoring is idempotent.
"""
from __future__ import annotations

import csv
import math
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from oracle import config
from oracle.backend import Backend, DbRef, get_backend
from oracle.rank_and_seal import (JsonArgumentParser, atomic_write, prediction_paths, pretty_json, read_sealed,
                                  round6, validate_prediction)

EXIT_UNSEALED = config.EXIT_UNSEALED   # 5: no seal, or the prediction does not verify: scoring refused
EXIT_NO_KEY = config.EXIT_NO_KEY       # 6: no human answer key (and no machine key allowed / available)
KEY_COLUMNS = ("exam_id", "problem", "points", "topic_ids")
KeyShares = list[tuple[str, str, float]]  # (problem part, topic_id, points on that topic)

_TOTAL_SQL = "SELECT total_points FROM {{exams}} WHERE exam_id = $exam_id"
_MACHINE_SQL = "SELECT problem, topic_id, points_share FROM {{exam_items}} WHERE exam_id = $exam_id"


class KeyInvalid(ValueError):
    """The answer key does not fit the prediction or the exam (exit 3)."""


class NoAnswerKey(RuntimeError):
    """No usable answer key (exit 6)."""


def score_path(run_id: str, directory: str | os.PathLike | None = None) -> Path:
    """<dir>/<run_id>.score.json, next to the sealed prediction."""
    return prediction_paths(run_id, directory)[0].with_name(f"{run_id}.score.json")


def read_answer_key(path: str | os.PathLike, exam_id: str, topic_ids: set[str]) -> KeyShares:
    """Parse a human answer key (exam_id, problem, sub, points, topic_ids, ...) into per-topic point shares."""
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
            unknown = [t for t in topics if t not in topic_ids]
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
    unknown = sorted(set(rows["topic_id"]) - topic_ids)
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
        if tid not in ids:
            raise KeyInvalid(f"answer key topic {tid!r} is not in the prediction's topic list")
        on_key[tid] = on_key.get(tid, 0.0) + float(points)
    total = sum(on_key.values())
    if not total > 0:
        raise KeyInvalid("the answer key has no points")
    key_points = {t: on_key.get(t, 0.0) / total for t in ids}
    y = {t: int(t in on_key) for t in ids}

    def covered(chosen: Sequence[str]) -> float:
        return sum(key_points[t] for t in set(chosen))

    if prediction["feature_set"] == "full":
        even, even_method = covered(prediction["baselines"]["even"]), "baseline_list"
    else:
        even, even_method = k / n, "closed_form_k_over_n"
    exam_topics = [t for t in ids if y[t]]
    return {"k": k, "n_topics": n, "top_k": top_k, "topics_on_exam": sorted(exam_topics), "total_points": round6(total),
            "model_pts": round6(covered(top_k)), "recall_k": round6(len(set(exam_topics) & set(top_k)) / len(exam_topics)),
            "brier": round6(sum((float(t["p"]) - y[t["topic_id"]]) ** 2 for t in topics) / n),
            "even_pts": round6(even), "even_method": even_method,
            "lastexam_pts": round6(covered(prediction["baselines"]["last_exam"])),
            "key_points": {t: round6(v) for t, v in key_points.items()}, "y": y}


def label_rows(run_id: str, scores: Mapping[str, Any], key_source: str) -> list[dict[str, Any]]:
    """Rows of the ledger `run_labels` table, one per topic of the prediction."""
    return [{"run_id": run_id, "topic_id": t, "y": y, "key_points": scores["key_points"][t], "key_source": key_source}
            for t, y in scores["y"].items()]


def _check_printed_total(backend: Backend, ledger: DbRef, exam_id: str, shares: KeyShares) -> None:
    exam = backend.query(ledger, _TOTAL_SQL, {"exam_id": exam_id})
    printed = exam["total_points"].iloc[0] if len(exam) == 1 else None
    if printed is None or pd.isna(printed):
        return
    total = sum(points for _, _, points in shares)
    if abs(total - float(printed)) > 1e-6 * max(1.0, abs(float(printed))):
        raise KeyInvalid(f"answer key points sum to {total:g}, the exam's printed total is {float(printed):g}")


def score_run(run_id: str, *, allow_machine_key: bool = False, backend: Backend | None = None,
              directory: str | os.PathLike | None = None,
              sealed_dir: str | os.PathLike | None = None) -> dict[str, Any]:
    """Verify the seal, then score; writes run_labels and <run_id>.score.json; returns the printed object."""
    prediction, digest = read_sealed(run_id, directory)  # SealBroken (exit 5) before anything sealed is touched
    validate_prediction(prediction)
    exam_id = prediction["target_exam"]
    ids = {t["topic_id"] for t in prediction["topics"]}
    key_path = config.sealed_answer_key_path(exam_id, hash_file=prediction_paths(run_id, directory)[1],
                                             sealed_dir=sealed_dir)
    be = backend or get_backend()
    ledger = be.ensure_ledger()
    out: dict[str, Any] = {"ok": True, "run_id": run_id, "exam_id": exam_id, "hash": digest,
                           "feature_set": prediction["feature_set"]}
    if key_path.is_file():
        shares, key_source = read_answer_key(key_path, exam_id, ids), "human"
        _check_printed_total(be, ledger, exam_id, shares)
    elif allow_machine_key:
        shares, key_source = machine_key(be, ledger, exam_id, ids), "machine"
        out["warning"] = "machine-graded: scored against the ledger's exam_items tags, not a human answer key"
    else:
        raise NoAnswerKey(f"no human answer key {key_path.name} in SEALED_DIR; label one, or pass --allow-machine-key")
    scores = compute_scores(prediction, shares)
    be.load_table(ledger, "run_labels", label_rows(run_id, scores, key_source), mode="upsert")
    out.update({"key_source": key_source, **{k: v for k, v in scores.items() if k not in ("key_points", "y")}})
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
