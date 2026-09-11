"""Homework echo precompute: the `echo_pairs` table (signal x3's input).

Spec: kit/02-product/prediction-model.md (x3 = homework problems on t whose fused search score against any prior exam
problem exceeds tau), kit/04-tools/hotdata.md ("Homework echo via hybrid search": bm25_search on homework_items,
vector_search on homework_vec, reciprocal rank fusion 1/(60+rank), tau chosen once and logged),
contracts/ledger-schema.sql (echo_pairs columns; upsert key hw_id, exam_id, problem), contracts/README.md (time rule).

What is computed. For course C: every homework item (distinct hw_id) against every exam problem (distinct
exam_id, problem) of a NON-sealed exam registered in `exams` (sealed or unregistered exam_items are excluded in SQL,
so their text never reaches Python). Two lexical "legs" score each pair and are fused by reciprocal rank fusion:
    sim(h, p) = sum over legs of 1 / (RRF_K + rank_leg(h, p))       (a leg with no match contributes 0)
    local   (method "tfidf"):   word TF-IDF cosine + character 3-5-gram TF-IDF cosine; each homework item ranks
                                the exam problems.
    hotdata (method "hotdata"): bm25_search on homework_items + vector search on homework_vec (LIVE [TEST]); each
                                exam problem is a query that ranks homework items, because the indexes live on the
                                homework tables. Same fusion code, other ranking axis, so tau is chosen per method.
The top TOP_N exam problems per homework item are stored with sim; ranks are "min" ranks (ties share a rank).

Why the computation is causal (a design decision; contracts/README.md time rule). echo_pairs is precomputed once
for the whole course and filtered per target later (both the hw and the exam side must be visible). A plain global
ranking would leak: a later exam's problems (even the target's own) would push an earlier problem down the ranking
or out of the top 5, and would shift TF-IDF statistics. So every pair (h, p) is scored at cutoff
c = max(time(h), time(p)), time = (term_seq, session or end of term), using only documents with time <= c: the
TF-IDF vocabulary/IDF, the candidates a rank is taken over, and the top-N competition. A pair is stored when it is
in its homework item's top N among exam problems with time <= c. Consequence (tested): for ANY target T, the
echo_pairs rows visible to T, sims included, equal what this module computes from T-visible rows alone. A homework
item may therefore hold more than TOP_N rows in total (at most TOP_N per cutoff); consumers wanting "top N" apply
it after the visibility filter. Adding later exams only adds rows, so upsert is incremental and idempotent.

tau (recommend_tau). sim is rank-based: the best possible pair (rank 1 in both legs) scores 2/61 ~ 0.0328 for any
text, so an absolute cosine cut-off does not transfer. tau is the TAU_QUANTILE (0.75, 'lower' = an observed value)
quantile of each homework item's best sim: the top quarter of homework items count as echoes. Choose it once on the
first course, log it in kit/07-reference/decisions-log.md and pass --tau for every later course.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
from scipy.stats import rankdata
from sklearn.feature_extraction.text import TfidfVectorizer

from oracle import config, ordering
from oracle.backend import Backend, DbHandle, DbRef, columns, get_backend, to_contract_table
from oracle.text_clean import search_terms

RRF_K = 60
TOP_N = 5
TAU_QUANTILE = 0.75
DEFAULT_TAU = 2 / (RRF_K + 3)   # fallback when there are no pairs: a pair ranked 3rd by both legs
SIM_DECIMALS = 12               # scores are rounded before ranking so ties do not depend on float noise
LOCAL_LEGS = ("word", "char")
QUERY_SIDES = ("homework", "exam")

Time = tuple[int, int]
# score_fn(homework, problems) -> {leg: matrix [len(homework) x len(problems)]}; higher = more similar, NaN = no match.
ScoreFn = Callable[[Sequence["HomeworkDoc"], Sequence["ExamProblem"]], Mapping[str, np.ndarray]]


def echo_columns() -> list[str]:
    """The echo_pairs contract columns, in DDL order."""
    return [c.name for c in columns("echo_pairs")]


def problem_sort_key(problem: str) -> tuple:
    """Natural order for problem labels: '2' < '4' < '4b' < '10' < 'A'."""
    match = re.fullmatch(r"(\d+)(.*)", str(problem))
    return (0, int(match.group(1)), match.group(2)) if match else (1, 0, str(problem))


@dataclass(frozen=True)
class HomeworkDoc:
    hw_id: str
    term_seq: int
    session: int
    text: str                                   # search_terms() form

    @property
    def time(self) -> Time:
        return (self.term_seq, self.session)


@dataclass(frozen=True)
class ExamProblem:
    exam_id: str
    problem: str
    term_seq: int
    session: int | None                         # None = unknown = end of term
    text: str

    @property
    def time(self) -> Time:
        return (self.term_seq, ordering.session_or_end(self.session))

    @property
    def key(self) -> tuple:
        return (self.exam_id, problem_sort_key(self.problem))


# ------------------------------------------------------------------ corpus (read from the ledger; sealed never selected)
_HOMEWORK_SQL = ("SELECT hw_id, term_seq, session, topic_id, text_clean FROM {{homework_items}} "
                 "WHERE course = $course")
_PROBLEMS_SQL = ("SELECT i.exam_id, i.problem, i.term_seq, i.session, i.topic_id, i.text_clean "
                 "FROM {{exam_items}} AS i JOIN {{exams}} AS e ON e.exam_id = i.exam_id AND e.course = i.course "
                 "WHERE i.course = $course AND e.sealed = FALSE")
_EXCLUDED_SQL = ("SELECT COALESCE(SUM(CASE WHEN e.exam_id IS NULL THEN 1 ELSE 0 END), 0) AS unregistered, "
                 "COALESCE(SUM(CASE WHEN e.sealed THEN 1 ELSE 0 END), 0) AS sealed "
                 "FROM {{exam_items}} AS i LEFT JOIN {{exams}} AS e ON e.exam_id = i.exam_id AND e.course = i.course "
                 "WHERE i.course = $course")


def _joined_text(texts: pd.Series) -> str:
    """One search text per item: the distinct texts of its topic rows (already ordered by topic_id)."""
    cleaned = [search_terms(t) for t in texts]
    return " ".join(dict.fromkeys(t for t in cleaned if t))


def _one_time(frame: pd.DataFrame, key: list[str], what: str) -> None:
    per_item = frame.groupby(key, dropna=False)[["term_seq", "session"]].nunique(dropna=False)
    bad = per_item[(per_item > 1).any(axis=1)]
    if len(bad):
        raise ValueError(f"{what}: {len(bad)} item(s) have rows with different (term_seq, session), "
                         f"e.g. {list(bad.index[:3])}")


def _query(backend: Backend, db: DbRef, sql: str, params: Mapping[str, object], cols: list[str]) -> pd.DataFrame:
    """backend.query() with the expected columns guaranteed (an empty hotdata result may carry none)."""
    frame = backend.query(db, sql, params)
    return frame.reindex(columns=cols) if frame.empty else frame


def load_corpus(backend: Backend, db: DbRef, course: str) -> tuple[list[HomeworkDoc], list[ExamProblem], dict]:
    """Homework items and non-sealed exam problems of `course`, one per hw_id / (exam_id, problem), plus counts of
    exam_items rows excluded because their exam is sealed or missing from `exams` (fail-closed)."""
    params = {"course": config.validate_id(course, what="course")}
    hw = _query(backend, db, _HOMEWORK_SQL, params, ["hw_id", "term_seq", "session", "topic_id", "text_clean"])
    hw = hw.sort_values(["hw_id", "topic_id"], kind="stable")
    ex = _query(backend, db, _PROBLEMS_SQL, params, ["exam_id", "problem", "term_seq", "session", "topic_id", "text_clean"])
    ex = ex.sort_values(["exam_id", "problem", "topic_id"], kind="stable")
    excluded = _query(backend, db, _EXCLUDED_SQL, params, ["unregistered", "sealed"])
    _one_time(hw, ["hw_id"], "homework_items")
    _one_time(ex, ["exam_id", "problem"], "exam_items")

    homework = [HomeworkDoc(str(hw_id), int(g["term_seq"].iloc[0]), int(g["session"].iloc[0]),
                            _joined_text(g["text_clean"]))
                for hw_id, g in hw.groupby("hw_id", sort=True)]
    problems = []
    for (exam_id, problem), g in ex.groupby(["exam_id", "problem"], sort=True):
        session = g["session"].iloc[0]
        problems.append(ExamProblem(str(exam_id), str(problem), int(g["term_seq"].iloc[0]),
                                    None if pd.isna(session) else int(session), _joined_text(g["text_clean"])))
    stats = {
        "homework_items": len(homework),
        "exam_problems": len(problems),
        "empty_texts": {"homework": sum(not h.text for h in homework), "exam_problems": sum(not p.text for p in problems)},
        "sealed_items_excluded": int(excluded["sealed"].iloc[0]) if len(excluded) else 0,
        "unregistered_items_excluded": int(excluded["unregistered"].iloc[0]) if len(excluded) else 0,
    }
    return homework, problems, stats


# ------------------------------------------------------------------ causal reciprocal rank fusion (both methods)
def rrf(rank: float, k: int = RRF_K) -> float:
    """Reciprocal rank fusion contribution of one leg: 1 / (k + rank)."""
    return 1.0 / (k + rank)


def _leg_ranks(scores: np.ndarray, axis: int) -> np.ndarray:
    """'min' ranks (1 = best, ties share a rank) along `axis`; NaN (no match) ranks last and is masked by callers."""
    rounded = np.round(scores, SIM_DECIMALS)
    return rankdata(np.where(np.isnan(rounded), np.inf, -rounded), method="min", axis=axis)


def fuse_causal(homework: Sequence[HomeworkDoc], problems: Sequence[ExamProblem], score_fn: ScoreFn, *,
                query_side: str = "homework", top_n: int = TOP_N) -> dict[tuple[str, tuple], float]:
    """Causal RRF (module docstring): {(hw_id, problem key): sim} for every stored pair.

    query_side="homework": each homework item ranks exam problems (local); "exam": each exam problem ranks homework
    items (hotdata). At every cutoff c, score_fn sees only documents with time <= c, and only pairs whose cutoff is
    exactly c take their sim from that call."""
    if query_side not in QUERY_SIDES:
        raise ValueError(f"query_side must be one of {QUERY_SIDES}")
    if top_n < 1:
        raise ValueError("top_n must be >= 1")
    hw = sorted(homework, key=lambda h: (h.time, h.hw_id))
    pr = sorted(problems, key=lambda p: (p.time, p.key))
    if len({h.hw_id for h in hw}) != len(hw) or len({p.key for p in pr}) != len(pr):
        raise ValueError("duplicate homework or exam problem ids")
    ht, pt = [h.time for h in hw], [p.time for p in pr]
    axis = 1 if query_side == "homework" else 0
    sims: dict[tuple[int, int], float] = {}
    for c in sorted(set(ht) | set(pt)):
        nh, npr = sum(t <= c for t in ht), sum(t <= c for t in pt)
        if not nh or not npr:
            continue
        at_c = np.zeros((nh, npr), dtype=bool)
        at_c[[i for i in range(nh) if ht[i] == c], :] = True
        at_c[:, [j for j in range(npr) if pt[j] == c]] = True
        fused = np.zeros((nh, npr))
        for leg, scores in score_fn(hw[:nh], pr[:npr]).items():
            scores = np.asarray(scores, dtype=float)
            if scores.shape != (nh, npr):
                raise ValueError(f"leg {leg!r}: score matrix {scores.shape} != {(nh, npr)}")
            fused += np.where(np.isnan(scores), 0.0, 1.0 / (RRF_K + _leg_ranks(scores, axis)))
        for i, j in zip(*np.nonzero(at_c & (fused > 0))):
            sims[(int(i), int(j))] = round(float(fused[i, j]), SIM_DECIMALS)

    stored: dict[tuple[str, tuple], float] = {}
    by_hw: dict[int, list[tuple[int, float]]] = {}
    for (i, j), s in sims.items():
        by_hw.setdefault(i, []).append((j, s))
    for i, items in by_hw.items():
        ordered = sorted(items, key=lambda js: (-js[1], pr[js[0]].key))
        for pos, (j, s) in enumerate(ordered):
            cutoff = max(ht[i], pt[j])
            if sum(pt[q] <= cutoff for q, _ in ordered[:pos]) < top_n:
                stored[(hw[i].hw_id, pr[j].key)] = s
    return stored


def to_rows(course: str, homework: Sequence[HomeworkDoc], problems: Sequence[ExamProblem],
            stored: Mapping[tuple[str, tuple], float]) -> pd.DataFrame:
    """echo_pairs rows (contract columns), ordered by hw_id, sim descending, exam_id, problem."""
    hw = {h.hw_id: h for h in homework}
    pr = {p.key: p for p in problems}
    rows = [{"course": course, "hw_id": h_id, "hw_term_seq": hw[h_id].term_seq, "hw_session": hw[h_id].session,
             "exam_id": pr[key].exam_id, "problem": pr[key].problem, "exam_term_seq": pr[key].term_seq,
             "exam_session": pr[key].session, "sim": s}
            for (h_id, key), s in sorted(stored.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1]))]
    frame = pd.DataFrame(rows, columns=echo_columns())
    return frame.astype({"hw_term_seq": "Int32", "hw_session": "Int32", "exam_term_seq": "Int32",
                         "exam_session": "Int32", "sim": "float64"})


# ------------------------------------------------------------------ local legs: word + character TF-IDF
def _vectorizers() -> dict[str, TfidfVectorizer]:
    return {"word": TfidfVectorizer(stop_words="english", sublinear_tf=True),
            "char": TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)}


def tfidf_scores(homework: Sequence[HomeworkDoc], problems: Sequence[ExamProblem]) -> dict[str, np.ndarray]:
    """Cosine similarity per leg, fitted on exactly these documents (the cutoff's corpus). 0 -> NaN (no match)."""
    docs = [h.text for h in homework] + [p.text for p in problems]
    out: dict[str, np.ndarray] = {}
    for leg, vectorizer in _vectorizers().items():
        try:
            matrix = vectorizer.fit_transform(docs)
        except ValueError:          # empty vocabulary: no usable terms at this cutoff
            out[leg] = np.full((len(homework), len(problems)), np.nan)
            continue
        scores = np.round((matrix[: len(homework)] @ matrix[len(homework):].T).toarray(), SIM_DECIMALS)
        scores[scores <= 0] = np.nan
        out[leg] = scores
    return out


def compute_local(course: str, homework: Sequence[HomeworkDoc], problems: Sequence[ExamProblem], *,
                  top_n: int = TOP_N) -> pd.DataFrame:
    """LOCAL mode: causal RRF of word and character TF-IDF, each homework item ranking the exam problems."""
    stored = fuse_causal(homework, problems, tfidf_scores, query_side="homework", top_n=top_n)
    return to_rows(course, homework, problems, stored)


# ------------------------------------------------------------------ tau
def best_sim_per_homework(pairs: pd.DataFrame) -> pd.Series:
    """Each homework item's best stored sim (the quantity x3 compares with tau)."""
    return pairs.groupby("hw_id")["sim"].max() if len(pairs) else pd.Series(dtype="float64")


TAU_BELOW = 1e-9                # tau sits this far under the chosen observed score, so ties with it count


def recommend_tau(pairs: pd.DataFrame, quantile: float = TAU_QUANTILE) -> float:
    """tau for the test "sim > tau": TAU_BELOW under v, the `quantile` ('lower' = an observed value) of the
    per-homework best sims. Every homework item whose best match is at least as strong as v counts: at least
    1 - quantile of them, more when scores tie, which RRF makes common at the top (2/61 = rank 1 in both legs).
    DEFAULT_TAU when there are no pairs."""
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be in [0, 1]")
    best = best_sim_per_homework(pairs)
    if best.empty:
        return DEFAULT_TAU
    return round(float(np.quantile(best.to_numpy(), quantile, method="lower")) - TAU_BELOW, SIM_DECIMALS)


def homework_over_tau(pairs: pd.DataFrame, tau: float) -> int:
    """Number of homework items whose best sim exceeds tau."""
    return int((best_sim_per_homework(pairs) > tau).sum())


# ------------------------------------------------------------------ hotdata legs (LIVE [TEST]: unconfirmed, no credentials)
HOTDATA_CATALOG, HOTDATA_SCHEMA = "default", "public"   # LIVE [TEST] 'cat.s.t' literal inside a managed database
BM25_SCORE_COLUMN = "_score"    # LIVE [TEST] the docs only name vector_search's `_distance`
MAX_SEARCH_K = 10_000
MAX_QUERY_TERMS = 256
HOTDATA_LEGS = ("bm25", "vector")
# "vector_search": the kit docs' table function vector_search('cat.s.t','col','text',k) -> _distance.
# "vector_distance": the installed SDK's form for a provider-backed index (hotdata-framework 0.14.0 create_index
# docstring: vector_distance(source_col, 'query')). The docs and the SDK disagree; the G0 smoke test decides.
VECTOR_STYLES = ("vector_search", "vector_distance")
_SEARCH_TEXT_RE = re.compile(r"[^\W_]+(?: [^\W_]+)*")
SearchFn = Callable[[str], pd.DataFrame]   # search text -> rows (leg, hw_id, score); higher score = more similar


def search_text_literal(text: object, *, max_terms: int = MAX_QUERY_TERMS) -> str:
    """Single-quoted SQL literal for a search text (hotdata has no bind API). Only search_terms() output is
    rendered: letters/digits separated by single spaces, so no quote, backslash or comment marker can appear.
    ValueError when nothing searchable is left."""
    terms = " ".join(search_terms(text).split()[:max_terms])
    if not _SEARCH_TEXT_RE.fullmatch(terms) or "'" in terms or "\\" in terms:
        raise ValueError("search text has no searchable terms")
    return f"'{terms}'"


def search_table_literal(table: str) -> str:
    """'default.public.<table>' for the search table functions; only the two homework tables are allowed."""
    if table not in ("homework_items", "homework_vec"):
        raise ValueError(f"no search index is defined on {table!r}")
    return f"'{HOTDATA_CATALOG}.{HOTDATA_SCHEMA}.{table}'"


def search_sql(query_text: object, *, k: int, vector: bool = True, vector_style: str = "vector_search") -> str:
    """One exam problem's query: bm25_search on homework_items.text_clean (+ a vector leg on homework_vec), as
    rows (leg, hw_id, score) for course $course, one row per homework item (homework_items has a row per topic).
    BM25 applies WHERE after its top k, so callers pass k = every row (HotdataSearcher). LIVE [TEST]."""
    q = search_text_literal(query_text)
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_SEARCH_K:
        raise ValueError(f"k must be an int in [1, {MAX_SEARCH_K}]")
    if vector_style not in VECTOR_STYLES:
        raise ValueError(f"vector_style must be one of {VECTOR_STYLES}")
    legs = [f"SELECT 'bm25' AS leg, hw_id, course, {BM25_SCORE_COLUMN} AS score "
            f"FROM bm25_search({search_table_literal('homework_items')}, 'text_clean', {q}, {k})"]
    if vector and vector_style == "vector_search":
        legs.append(f"SELECT 'vector' AS leg, hw_id, course, -_distance AS score "
                    f"FROM vector_search({search_table_literal('homework_vec')}, 'text_clean', {q}, {k})")
    elif vector:
        legs.append("SELECT 'vector' AS leg, hw_id, course, -d AS score FROM (SELECT hw_id, course, "
                    f"vector_distance(text_clean, {q}) AS d FROM {{{{homework_vec}}}} ORDER BY d ASC LIMIT {k}) AS nn")
    return (f"SELECT leg, hw_id, MAX(score) AS score FROM ({' UNION ALL '.join(legs)}) AS hits "
            "WHERE course = $course GROUP BY leg, hw_id")


class HotdataSearcher:
    """SearchFn that runs search_sql() for one exam problem on the ledger via backend.query(). LIVE [TEST]."""

    def __init__(self, backend: Backend, db: DbRef, course: str, *, vector: bool = True,
                 vector_style: str = "vector_search") -> None:
        self.backend, self.db = backend, db
        self.course = config.validate_id(course, what="course")
        self.vector, self.vector_style = vector, vector_style
        rows = 0
        for table in ("homework_items",) + (("homework_vec",) if vector else ()):
            frame = backend.query(db, f"SELECT COUNT(*) AS n FROM {{{{{table}}}}}")
            rows = max(rows, int(frame["n"].iloc[0]) if len(frame) else 0)
        self.k = min(max(rows, 1), MAX_SEARCH_K)      # over-fetch every row (BM25 filters after its top k)
        self.k_capped = rows > MAX_SEARCH_K

    def __call__(self, query_text: str) -> pd.DataFrame:
        sql = search_sql(query_text, k=self.k, vector=self.vector, vector_style=self.vector_style)
        return self.backend.query(self.db, sql, {"course": self.course})     # LIVE [TEST]


def compute_hotdata(course: str, homework: Sequence[HomeworkDoc], problems: Sequence[ExamProblem],
                    search: SearchFn, *, legs: Sequence[str] = HOTDATA_LEGS, top_n: int = TOP_N) -> pd.DataFrame:
    """HOTDATA mode: each exam problem's text queries the homework tables (one SearchFn call), and the per-leg
    scores go through the same causal RRF as LOCAL mode, ranking homework items per exam problem. Homework items
    with empty text are never ranked. BM25's IDF is table-wide on hotdata (a known, weak exception to causality)."""
    hw_index = {h.hw_id: i for i, h in enumerate(homework)}
    pr_index = {p.key: j for j, p in enumerate(problems)}
    searchable = {h.hw_id for h in homework if h.text}
    grid = {leg: np.full((len(homework), len(problems)), np.nan) for leg in legs}
    for j, p in enumerate(problems):
        if not p.text:
            continue
        hits = search(p.text)
        for leg, hw_id, score in hits[["leg", "hw_id", "score"]].itertuples(index=False):
            if leg in grid and str(hw_id) in searchable and score is not None and not pd.isna(score):
                grid[leg][hw_index[str(hw_id)], j] = float(score)

    def score_fn(hw_sub: Sequence[HomeworkDoc], pr_sub: Sequence[ExamProblem]) -> dict[str, np.ndarray]:
        rows = [hw_index[h.hw_id] for h in hw_sub]
        cols = [pr_index[p.key] for p in pr_sub]
        return {leg: matrix[np.ix_(rows, cols)] for leg, matrix in grid.items()}

    stored = fuse_causal(homework, problems, score_fn, query_side="exam", top_n=top_n)
    return to_rows(course, homework, problems, stored)


def build_search_indexes(backend: Backend, db: DbRef, *, embedding_provider_id: str | None = None) -> list[dict]:
    """LIVE [TEST] HotdataClient.create_index: a BM25 index on homework_items.text_clean and, given an embedding
    provider id, a provider-backed vector index on homework_vec.text_clean, which must be that table's only index
    (kit/04-tools/hotdata.md). Re-running it on existing indexes is unconfirmed."""
    if backend.name != "hotdata":
        raise config.ConfigError("search indexes exist only on the hotdata backend")
    target = config.validate_id((db.database_id or db.name) if isinstance(db, DbHandle) else db,
                                what="hotdata database")
    client = backend.client  # type: ignore[attr-defined]
    # LIVE [TEST] BM25 index build (queries error without one, per the SDK docstring)
    built = [client.create_index(target, "homework_items", columns=["text_clean"], index_type="bm25")]
    if embedding_provider_id:
        config.validate_id(embedding_provider_id, what="embedding provider id")
        # LIVE [TEST] provider-backed vector index; the metric is left to the provider
        built.append(client.create_index(target, "homework_vec", columns=["text_clean"], index_type="vector",
                                         embedding_provider_id=embedding_provider_id))
    return [r.to_dict() if hasattr(r, "to_dict") else {"result": str(r)} for r in built]


# ------------------------------------------------------------------ load, run, CLI
METHODS = ("auto", "tfidf", "hotdata")


def load_echo_pairs(backend: Backend, db: DbRef, pairs: pd.DataFrame, *, course: str,
                    prune: bool = False) -> tuple[int, str]:
    """Upsert `pairs` into echo_pairs (key hw_id, exam_id, problem). prune=True rewrites the table instead, with
    this course's rows replaced and every other course kept, so pairs that no longer exist are removed."""
    if not prune:
        return (backend.load_table(db, "echo_pairs", pairs, mode="upsert") if len(pairs) else 0), "upsert"
    others = backend.query(db, f"SELECT {', '.join(echo_columns())} FROM {{{{echo_pairs}}}} WHERE course <> $course",
                           {"course": config.validate_id(course, what="course")})
    if others.attrs.get("hotdata_warning"):
        raise RuntimeError(f"refusing to prune: the other courses' rows were not read completely "
                           f"({others.attrs['hotdata_warning']})")
    table = pa.concat_tables([to_contract_table("echo_pairs", others), to_contract_table("echo_pairs", pairs)])
    return backend.load_table(db, "echo_pairs", table, mode="replace"), "replace"


def _warnings(course: str, stats: Mapping[str, Any]) -> list[str]:
    notes = []
    if not stats["homework_items"]:
        notes.append(f"no homework_items for {course}")
    if not stats["exam_problems"]:
        notes.append(f"no non-sealed exam problems for {course}")
    if stats["sealed_items_excluded"]:
        notes.append(f"{stats['sealed_items_excluded']} exam_items row(s) belong to a sealed exam: the ledger breaks "
                     f"the contract (exam_items never contains sealed exams); excluded in SQL, text never read")
    if stats["unregistered_items_excluded"]:
        notes.append(f"{stats['unregistered_items_excluded']} exam_items row(s) have no exams row, so they cannot be "
                     f"shown to be unsealed; excluded")
    return notes


def run(course: str, *, tau: float | None = None, method: str = "auto", top_n: int = TOP_N, prune: bool = False,
        vector: bool = True, vector_style: str = "vector_search", build_indexes: bool = False,
        embedding_provider_id: str | None = None, backend: Backend | None = None,
        search: SearchFn | None = None) -> dict[str, Any]:
    """Compute and load echo_pairs for `course`; return the JSON summary the CLI prints."""
    started = time.monotonic()
    course = config.validate_id(course, what="course")
    if tau is not None and not (math.isfinite(tau) and tau >= 0):
        raise ValueError("tau must be a finite number >= 0")
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    be = backend if backend is not None else get_backend()
    db = be.ensure_ledger()
    chosen = ("hotdata" if be.name == "hotdata" else "tfidf") if method == "auto" else method
    homework, problems, stats = load_corpus(be, db, course)
    notes = _warnings(course, stats)
    extra: dict[str, Any] = {}
    if chosen == "tfidf":
        legs: Sequence[str] = LOCAL_LEGS
        pairs = compute_local(course, homework, problems, top_n=top_n)
    else:
        legs = HOTDATA_LEGS if vector else ("bm25",)
        if search is None:
            if be.name != "hotdata":
                raise config.ConfigError("method 'hotdata' needs ORACLE_BACKEND=hotdata (bm25_search is hotdata SQL)")
            if build_indexes:
                extra["indexes"] = build_search_indexes(be, db, embedding_provider_id=embedding_provider_id if vector else None)
            searcher = HotdataSearcher(be, db, course, vector=vector, vector_style=vector_style)
            if searcher.k_capped:
                notes.append(f"search k capped at {MAX_SEARCH_K}: some homework items may be missed")
            search = searcher
        pairs = compute_hotdata(course, homework, problems, search, legs=legs, top_n=top_n)
    loaded, load_mode = load_echo_pairs(be, db, pairs, course=course, prune=prune)
    tau_value = float(tau) if tau is not None else recommend_tau(pairs)
    summary: dict[str, Any] = {
        "ok": True, "course": course, "method": chosen, "backend": be.name, "legs": list(legs),
        "query_side": "homework" if chosen == "tfidf" else "exam", "top_n": top_n, **stats,
        "pairs": len(pairs), "rows_loaded": loaded, "load_mode": load_mode,
        "max_rows_per_homework": int(pairs.groupby("hw_id").size().max()) if len(pairs) else 0,
        "tau": tau_value, "tau_source": "argument" if tau is not None else "quantile",
        "tau_rule": "--tau" if tau is not None else
        f"quantile {TAU_QUANTILE} (method=lower) of per-homework best sim, minus {TAU_BELOW:g}",
        "homework_over_tau": homework_over_tau(pairs, tau_value),
        "seconds": round(time.monotonic() - started, 3), **extra,
    }
    if notes:
        summary["warning"] = "; ".join(notes)
    return summary


class _JsonArgumentParser(argparse.ArgumentParser):
    """Usage errors are printed as one JSON object and exit EXIT_HARD (argparse's own exit 2 means skip-list)."""

    def error(self, message: str) -> None:  # type: ignore[override]
        config.emit({"ok": False, "error": f"usage: {message}"})
        raise SystemExit(config.EXIT_HARD)


def main(argv: list[str] | None = None) -> int:
    parser = _JsonArgumentParser(prog="python -m oracle.echo",
                                 description="Precompute homework-echo pairs for one course and upsert them into the "
                                             "ledger's echo_pairs table. Prints one JSON object.")
    parser.add_argument("--course", required=True, help="course code, e.g. 6.003")
    parser.add_argument("--tau", type=float, default=None,
                        help="echo threshold to report against (default: recommended from this course's scores)")
    parser.add_argument("--method", choices=METHODS, default="auto",
                        help="tfidf (offline) | hotdata (bm25 + vector search, LIVE) | auto (by ORACLE_BACKEND)")
    parser.add_argument("--top-n", type=int, default=TOP_N, help="exam problems kept per homework item per cutoff")
    parser.add_argument("--prune", action="store_true", help="replace this course's rows instead of upserting")
    parser.add_argument("--no-vector", action="store_true", help="hotdata: BM25 leg only (cut-list fallback)")
    parser.add_argument("--vector-style", choices=VECTOR_STYLES, default="vector_search")
    parser.add_argument("--build-indexes", action="store_true", help="hotdata: create the search indexes first")
    parser.add_argument("--embedding-provider", default=None, help="hotdata: embedding provider id for homework_vec")
    args = parser.parse_args(argv)
    try:
        summary = run(args.course, tau=args.tau, method=args.method, top_n=args.top_n, prune=args.prune,
                      vector=not args.no_vector, vector_style=args.vector_style, build_indexes=args.build_indexes,
                      embedding_provider_id=args.embedding_provider)
    except Exception as exc:  # bad input, config, backend or service failure: a hard fault (rote-plays.md)
        config.emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return config.EXIT_HARD
    config.emit(summary)
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
