"""Refit the beta lessons from every scored run: `python -m oracle.refit_lessons --through-run-seq N`.

Spec: kit/02-product/prediction-model.md "From signals to probability" (after each run, refit beta by
L2-regularised logistic regression over all topic rows of all completed runs, y = 1 if the topic appeared on
that run's exam; C ~ 0.5 so weights move gradually; each beta carries a one-line statement),
contracts/README.md "Ground truth" (refit trains ONLY on run_labels, which score.py writes after the seal),
kit/03-architecture/rote-plays.md (refit_lessons.py: all runs -> new beta + statements; Play 2 step 8).

Training rows: predictions (x1..x7) JOIN run_labels (y) JOIN runs, for runs with run_seq <= N, leaving out
  * cold-start twins (runs.cold_start): a comparison run, not new evidence;
  * exam_history runs (D4: beta is refit only on feature_set = 'full' AND cold_start = false). Their x2..x5 are
    0 because the published term lies in their future, not because the topic is absent, so pooling them would
    teach the model that coverage and lecture time do not matter;
  * machine-keyed labels (run_labels.key_source != 'human') unless --allow-machine-labels, because "the AI
    never grades itself" (contracts/README.md);
  * any T00 row (the off-list bucket is never predicted, D6).
A NULL signal counts as 0, as in lessons_store.predict_p. On hotdata the join is LIVE [TEST].

Standardisation (D5): before the fit, each x1..x7 is z-scored WITHIN each run across that run's topic rows
(rank_and_seal.standardize_matrix: population std, zero variance -> 0), exactly as rank_and_seal does before it
applies the weights, so the weights learned here are the weights predictions use. The predictions table keeps
the raw signals; the transform is recomputed here.

Model: LogisticRegression(C=0.5, L2 penalty, solver='lbfgs', fit_intercept=True). scikit-learn 1.8
deprecated `penalty` (removed in 1.10; 1.9.1 is installed) and documents l1_ratio=0 as the same L2 penalty,
so logistic_model() passes whichever the installed version expects. tests/test_lessons.py checks that the
fit is identical to penalty='l2'.

Guards: the previous weights are kept, and the reason is given, when there are no training rows, fewer than
MIN_RUNS runs, only one class of y, or no signal that varies.

Constant signals: a (standardised) signal with the same value c on every training row carries no evidence about
its own weight. It cannot be told apart from the intercept, and for c = 0 (after z-scoring, any signal that is
constant within every run) it does not enter the likelihood at all, so the L2 penalty alone would pull its weight
to 0. Such a signal keeps its previous weight, and the intercept is shifted by -c * previous weight, so every
training row's fitted log-odds stay the same. For c = 0 the shift is 0.

Previous weights come from read_lessons.resolve_lessons(before_run_seq=N): the latest version strictly
before N. Refitting run N again (a Rote resume or replay) therefore gives the same answer and never uses
later lessons.

Statements: one deterministic sentence per signal, giving the direction, the change from the previous
weight, whether it is the strongest positive or negative signal, and the evidence behind it. "Strongest"
compares weight x standard deviation of the signal over the training rows, so signals measured on different
scales compare fairly. An optional LLM rewording (LIVE [TEST]) runs only when LLM_API_KEY is set and --no-llm
is not given. The LLM sees only the template sentences, no exam content, and it never predicts. A rewording
is kept only if every number in it also appears in its template sentence; otherwise the template stays.

evidence.rows_by_feature_set reports the training rows per feature set (only 'full' since D4); left_out lists
the runs dropped, by reason.

Prints ONE JSON object, which store_lessons takes as its input. Exit codes: 0 ok (if the weights were kept,
a "warning" is included); 1 hard fault (ledger unreadable or inconsistent, bad arguments).
"""
from __future__ import annotations

import inspect
import json
import re
import sys
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from oracle import config
from oracle.backend import Backend, DbRef, get_backend
from oracle.lessons_store import (FEATURES, SIGNAL_LABELS, SIGNALS, LessonsStore, make_record, predict_p,
                                  validate_weights)
from oracle.rank_and_seal import OFF_LIST_TOPIC, standardize_matrix
from oracle.read_lessons import ScriptArgumentParser, non_negative_int, parse_cli, resolve_lessons

C_REGULARISATION = 0.5    # prediction-model.md: "keep regularization strong (C ~ 0.5)"
MAX_ITER = 100            # scikit-learn's default for lbfgs
MIN_RUNS = 2
CONSTANT_SPREAD = 1e-12   # max - min at or below this: the signal did not vary
LITTLE_EFFECT = 0.05      # |weight| below this reads as "has little effect"
# SIGNAL_LABELS (the spec table's display names) now live in lessons_store, next to FEATURES; re-exported here.

TRAINING_SQL = """
SELECT p.run_id, r.run_seq, r.course, r.cold_start, p.feature_set, l.key_source, p.topic_id,
       p.x1, p.x2, p.x3, p.x4, p.x5, p.x6, p.x7, l.y
FROM {{predictions}} AS p
JOIN {{run_labels}} AS l ON l.run_id = p.run_id AND l.topic_id = p.topic_id
JOIN (SELECT DISTINCT run_id, run_seq, course, cold_start FROM {{runs}}) AS r ON r.run_id = p.run_id
WHERE r.run_seq <= $through_run_seq
  AND p.topic_id <> $off_list
ORDER BY r.run_seq, p.run_id, p.topic_id
"""

# The optional LLM hook uses the same environment names as Cognee 1.5.4 (LLMConfig), so one .env serves both.
COGNEE_DEFAULT_LLM_MODEL = "openai/gpt-5-mini"  # cognee/infrastructure/llm/config.py LLMConfig.llm_model default
LLM_MAX_CHARS = 300
REWORD_PROMPT = ("Rewrite each value of the JSON object you are given as one plain sentence a student can read. "
                 "Keep every number exactly as written. Do not add numbers, facts or advice. "
                 "Reply with only a JSON object that has the same keys.")
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9.])[-+]?\d+(?:\.\d+)?")  # '3' in 'x3' is a name, not a number


# ------------------------------------------------------------------ training rows
def _run_ids(frame: pd.DataFrame) -> list[str]:
    return list(dict.fromkeys(frame["run_id"].tolist()))


def load_training_rows(backend: Backend, ledger: DbRef, through_run_seq: int, *,
                       allow_machine_labels: bool = False) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Training rows for the runs with run_seq <= N (module docstring), and the run_ids left out, by reason.
    Exact duplicate rows (a repeated append) are dropped; conflicting ones raise ValueError."""
    frame = backend.query(ledger, TRAINING_SQL, {"through_run_seq": int(through_run_seq), "off_list": OFF_LIST_TOPIC})
    if frame.attrs.get("hotdata_warning"):  # LIVE [TEST] a truncated result would silently bias the fit
        raise RuntimeError(f"training rows may be incomplete: {frame.attrs['hotdata_warning']}")
    frame = frame.drop_duplicates()
    if frame.duplicated(["run_id", "topic_id"]).any():
        raise ValueError("the ledger holds conflicting rows for one (run_id, topic_id) across predictions, "
                         "run_labels and runs")
    if not frame["y"].isin([0, 1]).all():
        raise ValueError("run_labels.y must be 0 or 1")
    cold = frame["cold_start"].astype(bool)
    full = frame["feature_set"] == "full"
    machine = frame["key_source"] != "human"
    left_out = {"cold_start_runs": _run_ids(frame[cold]),
                "exam_history_runs": _run_ids(frame[~cold & ~full]),
                "machine_label_runs": [] if allow_machine_labels else _run_ids(frame[~cold & full & machine])}
    keep = ~cold & full if allow_machine_labels else ~cold & full & ~machine
    rows = frame[keep].reset_index(drop=True)
    rows[list(FEATURES)] = rows[list(FEATURES)].fillna(0.0).astype(float)
    return rows, left_out


def standardize_rows(rows: pd.DataFrame) -> np.ndarray:
    """The x1..x7 matrix of `rows` with every signal z-scored within each run_id (D5), rows in their given
    order: rank_and_seal.standardize_matrix applied per run, the transform the predictions were made with."""
    X = rows[list(FEATURES)].to_numpy(dtype=float)
    Z = np.zeros_like(X)
    if len(rows):
        for positions in rows.groupby("run_id", sort=False).indices.values():
            Z[positions] = standardize_matrix(X[positions])
    return Z


# ------------------------------------------------------------------ the fit
def logistic_model() -> LogisticRegression:
    """The spec's model: L2 penalty, C = 0.5, lbfgs, with an intercept. scikit-learn >= 1.8 spells the L2
    penalty l1_ratio=0 (`penalty` is deprecated there and removed in 1.10); older versions take penalty='l2'."""
    if inspect.signature(LogisticRegression).parameters["penalty"].default == "deprecated":
        return LogisticRegression(C=C_REGULARISATION, l1_ratio=0.0, solver="lbfgs", fit_intercept=True,
                                  max_iter=MAX_ITER)
    return LogisticRegression(C=C_REGULARISATION, penalty="l2", solver="lbfgs", fit_intercept=True,
                              max_iter=MAX_ITER)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _guard(n_rows: int, n_runs: int, y: np.ndarray, varying: list[str]) -> str | None:
    """Why these rows cannot teach the model anything, or None."""
    if n_rows == 0:
        return "no scored runs to learn from yet"
    if n_runs < MIN_RUNS:
        return f"only {_count(n_runs, 'scored run')} so far (a refit needs at least {MIN_RUNS})"
    if len(np.unique(y)) < 2:
        return f"all {n_rows} topic rows have y={int(y[0])} (a refit needs both outcomes)"
    if not varying:
        return "no signal varies across the training rows"
    return None


def refit(rows: pd.DataFrame, previous: Mapping[str, Any]) -> dict[str, Any]:
    """Fit new weights on `rows` (columns run_id, course, x1..x7 RAW, y), starting from the `previous` lessons
    record. The signals are z-scored per run first (standardize_rows, D5). Pure, no I/O. Returns the weights,
    whether a refit happened (and if not, why), the signals that kept their previous weight, per-signal effect
    sizes, the evidence counts and lbfgs's iteration count."""
    prev = validate_weights(previous["weights"])
    X = standardize_rows(rows)
    y = rows["y"].to_numpy(dtype=int)
    spread = X.max(axis=0) - X.min(axis=0) if len(rows) else np.zeros(len(FEATURES))
    varying = [f for f, s in zip(FEATURES, spread) if s > CONSTANT_SPREAD]
    evidence = {"n_rows": int(len(rows)), "n_runs": int(rows["run_id"].nunique()),
                "n_positive": int(y.sum()), "n_courses": int(rows["course"].nunique())}
    reason = _guard(evidence["n_rows"], evidence["n_runs"], y, varying)
    if reason is not None:
        return {"refit": False, "reason": reason, "weights": prev, "kept_previous": list(FEATURES), "effects": {},
                "supporting_runs": list(previous.get("supporting_runs") or []), "n_iter": 0, **evidence}
    model = logistic_model().fit(X[:, [FEATURES.index(f) for f in varying]], y)
    constant = {f: float(X[0, i]) for i, f in enumerate(FEATURES) if f not in varying}
    weights = {"intercept": float(model.intercept_[0]) - sum(prev[f] * c for f, c in constant.items())}
    weights.update({f: float(w) for f, w in zip(varying, model.coef_[0])})
    weights.update({f: prev[f] for f in constant})
    effects = {f: weights[f] * float(X[:, FEATURES.index(f)].std()) for f in varying}
    return {"refit": True, "reason": None, "weights": {s: weights[s] for s in SIGNALS},
            "kept_previous": [f for f in FEATURES if f in constant], "effects": effects,
            "supporting_runs": _run_ids(rows), "n_iter": int(model.n_iter_[0]), **evidence}


# ------------------------------------------------------------------ statements
def _num(value: float) -> str:
    """Signed, two decimals, never '-0.00'."""
    rounded = round(float(value), 2)
    return f"{rounded if rounded else 0.0:+.2f}"


def template_statements(result: Mapping[str, Any], previous_weights: Mapping[str, float]) -> dict[str, str]:
    """One deterministic sentence per signal, built from the refit result (module docstring)."""
    w, prev = result["weights"], previous_weights
    base_p = predict_p(w, {})
    if not result["refit"]:
        why = result["reason"]
        out = {"intercept": f"Base rate kept: a topic at the run average on every signal gets p = {base_p:.2f} "
                            f"(intercept {_num(w['intercept'])}): {why}."}
        out.update({f: f"{SIGNAL_LABELS[f]} ({f}) kept at {_num(w[f])}: {why}." for f in FEATURES})
        return out
    evidence = f"from {_count(result['n_runs'], 'run')} in {_count(result['n_courses'], 'course')}"
    out = {"intercept": f"Base rate: a topic at the run average on every signal gets p = {base_p:.2f} "
                        f"(intercept {_num(w['intercept'])}, "
                        f"was {_num(prev['intercept'])}); {result['n_positive']} of {result['n_rows']} topic rows "
                        f"{evidence} were on their exam."}
    effects = result["effects"]
    strongest_up = max(effects, key=effects.__getitem__, default=None)
    strongest_down = min(effects, key=effects.__getitem__, default=None)
    for f in FEATURES:
        label = f"{SIGNAL_LABELS[f]} ({f})"
        if f in result["kept_previous"]:
            out[f] = (f"{label} kept at {_num(w[f])}: it had the same value on all {result['n_rows']} topic rows, "
                      f"so they carry no evidence about it.")
            continue
        if f == strongest_up and w[f] >= LITTLE_EFFECT:
            role = "is the strongest positive signal so far"
        elif f == strongest_down and w[f] <= -LITTLE_EFFECT:
            role = "is the strongest negative signal so far"
        elif w[f] >= LITTLE_EFFECT:
            role = "raises p"
        elif w[f] <= -LITTLE_EFFECT:
            role = "lowers p"
        else:
            role = "has little effect"
        out[f] = f"{label} {role}: weight {_num(w[f])} (was {_num(prev[f])}) {evidence}."
    return out


# ------------------------------------------------------------------ optional LLM rewording
def _numbers(text: str) -> set[float]:
    return {round(float(n), 6) for n in _NUMBER_RE.findall(text)}


def _llm_settings() -> tuple[dict[str, str] | None, str | None]:
    """(settings, None) when the hook may run; (None, reason) otherwise (reason None = simply not configured)."""
    api_key = config.env("LLM_API_KEY")
    if not api_key:
        return None, None
    model = config.env("LLM_MODEL") or COGNEE_DEFAULT_LLM_MODEL
    provider = (config.env("LLM_PROVIDER") or (model.split("/", 1)[0] if "/" in model else "openai")).lower()
    endpoint = config.env("LLM_ENDPOINT") or ""
    if provider != "openai" and not endpoint:
        return None, (f"LLM_PROVIDER={provider} needs an OpenAI-compatible LLM_ENDPOINT for statement rewording; "
                      f"template statements kept")
    return {"api_key": api_key, "model": model.removeprefix("openai/"), "endpoint": endpoint}, None


def _openai_complete(settings: Mapping[str, str]) -> Callable[..., Any]:
    """chat.completions.create of an OpenAI-compatible client (openai 2.x). LIVE [TEST]: never runs in tests."""
    from openai import OpenAI
    client = OpenAI(api_key=settings["api_key"], base_url=settings["endpoint"] or None,  # LIVE [TEST]
                    timeout=30.0, max_retries=1)
    return client.chat.completions.create


def reword_statements(statements: Mapping[str, str], complete: Callable[..., Any],
                      model: str) -> tuple[dict[str, str], dict[str, Any]]:
    """Ask the LLM to reword the template sentences. A rewording is kept only if it is one short line, without
    markup, whose numbers all appear in its template sentence. Any failure keeps the templates: this is an
    optional, degraded lane and never fails the refit."""
    report: dict[str, Any] = {"used": True, "model": model, "accepted": [], "rejected": [], "tokens": None,
                              "warning": None}
    out = dict(statements)
    try:
        response = complete(model=model, messages=[  # LIVE [TEST]
            {"role": "system", "content": REWORD_PROMPT},
            {"role": "user", "content": json.dumps(dict(statements), sort_keys=True, ensure_ascii=False)}])
        content = response.choices[0].message.content or ""
        reply = json.loads(content[content.index("{"):content.rindex("}") + 1])
        report["tokens"] = getattr(getattr(response, "usage", None), "total_tokens", None)
    except Exception as exc:  # optional lane: whatever went wrong, the deterministic templates stand
        report["warning"] = f"LLM rewording failed ({type(exc).__name__}); template statements kept"
        return out, report
    for signal, template in statements.items():
        text = reply.get(signal) if isinstance(reply, dict) else None
        text = " ".join(text.split()) if isinstance(text, str) else ""
        if (text and len(text) <= LLM_MAX_CHARS and not set("<>") & set(text)
                and _numbers(text) <= _numbers(template)):
            out[signal] = text
            report["accepted"].append(signal)
        else:
            report["rejected"].append(signal)
    return out, report


def maybe_reword(statements: Mapping[str, str], *, use_llm: bool,
                 complete: Callable[..., Any] | None = None) -> tuple[dict[str, str], dict[str, Any]]:
    """The optional LLM hook: off unless use_llm is true AND LLM_API_KEY is set. Tests inject `complete`."""
    off: dict[str, Any] = {"used": False, "model": None, "accepted": [], "rejected": [], "tokens": None,
                           "warning": None}
    if not use_llm:
        return dict(statements), off
    settings, why_not = _llm_settings()
    if settings is None:
        return dict(statements), {**off, "warning": why_not}
    try:
        fn = complete if complete is not None else _openai_complete(settings)
    except Exception as exc:  # e.g. the client cannot be built: degrade to the templates
        return dict(statements), {**off, "warning": f"LLM client unavailable ({type(exc).__name__}); "
                                                    f"template statements kept"}
    return reword_statements(statements, fn, settings["model"])


# ------------------------------------------------------------------ the step
def run_refit(through_run_seq: int, *, backend: Backend | None = None, ledger: DbRef | None = None,
              store: LessonsStore | None = None, allow_machine_labels: bool = False, use_llm: bool = True,
              complete: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Load the training rows, refit, and write the statements. Returns the JSON object the CLI prints; its
    run_seq / weights / statements / supporting_runs are the lessons version N that store_lessons stores."""
    be = backend if backend is not None else get_backend()
    db = ledger if ledger is not None else be.ledger()
    previous = resolve_lessons(through_run_seq, store=store, backend=be, ledger=db)
    rows, left_out = load_training_rows(be, db, through_run_seq, allow_machine_labels=allow_machine_labels)
    result = refit(rows, previous)
    statements, llm = maybe_reword(template_statements(result, previous["weights"]), use_llm=use_llm,
                                   complete=complete)
    record = make_record(through_run_seq, result["weights"], statements, result["supporting_runs"])

    warnings = [] if result["refit"] else [f"weights kept: {result['reason']}"]
    if not len(rows) and left_out["exam_history_runs"]:
        warnings.append(f"{_count(len(left_out['exam_history_runs']), 'exam_history run')} left out: beta is refit "
                        f"only on feature_set=full runs (D4)")
    if len(rows) and through_run_seq not in set(rows["run_seq"].astype(int).tolist()):
        warnings.append(f"run_seq {through_run_seq} contributed no training rows (unscored, a cold-start twin, an "
                        f"exam_history run or machine-keyed); lessons version {through_run_seq} is built from "
                        f"earlier runs only")
    if result["n_iter"] >= MAX_ITER:
        warnings.append(f"lbfgs stopped after {MAX_ITER} iterations without converging")
    if llm["warning"]:
        warnings.append(llm["warning"])
    by_feature_set = rows["feature_set"].value_counts().sort_index() if len(rows) else pd.Series(dtype=int)
    out: dict[str, Any] = {
        "ok": True, **record, "refit": result["refit"], "reason": result["reason"],
        "previous": {"run_seq": previous["run_seq"], "source": previous["source"], "weights": previous["weights"]},
        "kept_previous": result["kept_previous"],
        "effects": {f: float(v) for f, v in result["effects"].items()},
        "evidence": {"n_rows": result["n_rows"], "n_runs": result["n_runs"], "n_positive": result["n_positive"],
                     "n_courses": result["n_courses"],
                     "rows_by_feature_set": {str(k): int(v) for k, v in by_feature_set.items()}},
        "left_out": left_out,
        "model": {"type": "LogisticRegression", "C": C_REGULARISATION, "penalty": "l2", "solver": "lbfgs",
                  "fit_intercept": True, "n_iter": result["n_iter"]},
        "llm": llm,
    }
    if warnings:
        out["warning"] = "; ".join(warnings)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = ScriptArgumentParser(
        prog="python -m oracle.refit_lessons",
        description="Refit the beta lessons from every scored run through run_seq N. Prints one JSON object "
                    "(the input of oracle.store_lessons).")
    parser.add_argument("--through-run-seq", type=non_negative_int, required=True, metavar="N",
                        help="train on scored runs with run_seq <= N; the result is lessons version N")
    parser.add_argument("--allow-machine-labels", action="store_true",
                        help="also train on machine-keyed labels (those runs are charted as machine-graded)")
    parser.add_argument("--no-llm", action="store_true",
                        help="template statements only, even when LLM_API_KEY is set")
    args = parse_cli(parser, argv)
    if isinstance(args, int):
        return args
    try:
        out = run_refit(args.through_run_seq, allow_machine_labels=args.allow_machine_labels,
                        use_llm=not args.no_llm)
    except Exception as exc:  # script boundary: every failure is reported as one JSON object (hard-fault lane)
        config.emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return config.EXIT_HARD
    config.emit(out)
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
