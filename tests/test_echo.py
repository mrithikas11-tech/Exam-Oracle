"""Tests for oracle.text_clean and oracle.echo (homework echo precompute). Offline only: the hotdata path runs with a
fake search function, and with a fake client whose calls are bound against the real HotdataClient signatures."""
from __future__ import annotations

import inspect
import json
import math
import re
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from hotdata_framework import HotdataClient, ManagedDatabase, QueryResult

from conftest import FIXTURES_DIR, build_fixture_ledger
from oracle import echo, ordering, text_clean
from oracle.backend import HotdataBackend, LocalDuckDBBackend, arrow_schema, read_contract_csv, to_contract_table
from oracle.text_clean import clean_or_none, clean_text, search_terms

COURSE = "FX.101"
SEALED_EXAM = "FX.101-final-2022F"


def _records(df: pd.DataFrame) -> list[dict]:
    """Rows as plain dicts (NA -> None) so frames built different ways compare by value."""
    return [{k: (None if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)) else v)
             for k, v in row.items()} for row in df.to_dict("records")]


def _vis(ts, se, target):
    return ordering.visible(ts, se, target[0], target[1])


def _visible_rows(pairs: pd.DataFrame, target) -> pd.DataFrame:
    keep = [_vis(a, b, target) and _vis(c, None if pd.isna(d) else d, target)
            for a, b, c, d in pairs[["hw_term_seq", "hw_session", "exam_term_seq", "exam_session"]].itertuples(index=False)]
    return pairs[keep]


def _truncated(homework, problems, target):
    return ([h for h in homework if _vis(h.term_seq, h.session, target)],
            [p for p in problems if _vis(p.term_seq, p.session, target)])


# ================================================================== text_clean
def test_clean_text_strips_math_and_commands():
    assert clean_text(r"Find the \textbf{Fourier series} of $x(t)=\sum_k a_k$.") == "Find the Fourier series of ."
    assert clean_text(r"A $$\int f$$ B \[ y = mx \] C \( z \) D") == "A B C D"
    assert clean_text(r"\begin{align*} a &= b \\ c &= d \end{align*} after") == "after"
    assert clean_text(r"Use \emph{sampling}\label{eq:1} as in \ref{fig2} and \cite{oppenheim}.") == "Use sampling as in and ."
    assert clean_text(r"\section*{Intro} {\bf bold} text") == "Intro bold text"
    assert clean_text(r"costs \$5 ~ok, unbalanced $ dollar") == "costs 5 ok, unbalanced dollar"


def test_clean_text_whitespace_bytes_and_blanks():
    assert clean_text("a\t\tb\n\n c\r\n\x0c d") == "a b c d"
    assert clean_text("keep " + "x" * MAX_BYTES + " drop " + "x" * (MAX_BYTES + 1)) == "keep " + "x" * MAX_BYTES + " drop"
    assert clean_text("é" * 20 + " " + "é" * 21) == "é" * 20          # 40 bytes kept, 42 bytes dropped
    assert clean_text("Fou-\nrier trans-  \n form") == "Fourier transform"
    assert clean_text("ﬁnal") == "final"                          # NFKC ligature
    for blank in (None, float("nan"), pd.NA, "", "   "):
        assert clean_text(blank) == ""
    assert clean_or_none("$x$") is None and clean_or_none(" a ") == "a"


MAX_BYTES = text_clean.MAX_TOKEN_BYTES


@pytest.mark.parametrize("raw", [
    r"Find $x$ in \textbf{bold} and {\it it} ~ \\ next",
    "Fou-\nrier \\$ 5 $ unclosed",
    r"\begin{equation} a \end{equation} \verb|x| {{nested}} " + "y" * 60,
    "plain text, with punctuation: (a) and [b]!",
])
def test_clean_text_is_idempotent(raw):
    once = clean_text(raw)
    assert clean_text(once) == once
    assert not set(once) & set("$\\{}~")


def test_search_terms_follow_bm25_tokenisation():
    assert search_terms("x(t) = a_k + e^{j}, 3.5 dB") == "x t a k e j 3 5 dB"
    assert search_terms("x'); DROP TABLE t; --") == "x DROP TABLE t"
    assert search_terms("ω₀ résumé") == "ω0 résumé"                   # NFKC subscript, Unicode letters kept


def test_text_clean_cli(capsys):
    assert text_clean.main(["--text", r"$x$ hello \emph{world}"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": True, "text_clean": "hello world", "search_terms": "hello world"}


# ================================================================== corpus + sealing
def _extra_exam_item(exam_id: str, text: str) -> dict:
    return {"course": COURSE, "exam_id": exam_id, "exam_type": "final", "term": "2022F", "term_seq": 20223,
            "session": 20, "problem": "1", "points": 20.0, "topic_id": "T06", "points_share": 20.0,
            "tag_source": "human", "text_clean": text}


def test_sealed_and_unregistered_exams_are_excluded(tmp_path):
    be, handle = build_fixture_ledger(LocalDuckDBBackend(tmp_path, "ledger"))
    canary = "CANARY sealed homework echo text"
    be.load_table(handle, "exam_items", [_extra_exam_item(SEALED_EXAM, canary),
                                         _extra_exam_item("FX.101-quiz9-2022F", canary)], mode="append")
    homework, problems, stats = echo.load_corpus(be, handle, COURSE)
    assert stats["sealed_items_excluded"] == 1 and stats["unregistered_items_excluded"] == 1
    assert {p.exam_id for p in problems}.isdisjoint({SEALED_EXAM, "FX.101-quiz9-2022F"})
    assert not any("CANARY" in p.text for p in problems)

    summary = echo.run(COURSE, backend=be, prune=True)
    assert summary["ok"] and "sealed exam" in summary["warning"] and "no exams row" in summary["warning"]
    stored = be.query(handle, "SELECT DISTINCT exam_id FROM {{echo_pairs}}")
    assert set(stored["exam_id"]).isdisjoint({SEALED_EXAM, "FX.101-quiz9-2022F"})


# ================================================================== local echo
def test_local_echo_is_deterministic_and_fits_the_contract(fixture_ledger):
    be, handle = fixture_ledger
    homework, problems, stats = echo.load_corpus(be, handle, COURSE)
    assert stats["homework_items"] == 12 and stats["exam_problems"] == 16
    first = echo.compute_local(COURSE, homework, problems)
    second = echo.compute_local(COURSE, list(reversed(homework)), list(reversed(problems)))
    assert len(first) > 0 and _records(first) == _records(second)
    assert list(first.columns) == echo.echo_columns()
    to_contract_table("echo_pairs", first)
    assert first["sim"].between(1e-12, 2 / 61 + 1e-12).all()
    order = [(r["hw_id"], -r["sim"], r["exam_id"], echo.problem_sort_key(r["problem"])) for r in _records(first)]
    assert order == sorted(order)
    assert not first.duplicated(["hw_id", "exam_id", "problem"]).any()


def test_rrf_best_pair_scores_two_over_61():
    assert echo.rrf(1) == 1 / 61 and echo.rrf(3) == 1 / 63
    hw = [echo.HomeworkDoc("C-2022F-ps1-p1", 20223, 3, "laplace transform of a damped sinusoid")]
    pr = [echo.ExamProblem("C-final-2021F", "1", 20213, 20, "laplace transform of a damped sinusoid"),
          echo.ExamProblem("C-final-2021F", "2", 20213, 20, "sampling theorem aliasing"),
          echo.ExamProblem("C-final-2021F", "3", 20213, 20, "bode plot of a second order system")]
    rows = _records(echo.compute_local("C", hw, pr))
    assert rows[0]["problem"] == "1" and rows[0]["sim"] == round(2 / 61, 12)
    assert all(r["sim"] < 2 / 61 for r in rows[1:])


def _single_cutoff_corpus(n_problems: int = 8):
    words = ["fourier", "series", "sampling", "convolution", "laplace", "transform", "filter", "stability",
             "impulse", "response", "frequency", "signal"]
    problems = [echo.ExamProblem("C-final-2021F", str(i + 1), 20213, 20,
                                 " ".join(words[(i + j) % len(words)] for j in range(4))) for i in range(n_problems)]
    homework = [echo.HomeworkDoc(f"C-2022F-ps{k}-p1", 20223, 3 * k, " ".join(words[k:k + 5])) for k in (1, 2, 3)]
    return homework, problems


@pytest.mark.parametrize("top_n", [5, 2])
def test_top_n_cap_when_every_exam_precedes_the_homework(top_n):
    homework, problems = _single_cutoff_corpus()
    pairs = echo.compute_local("C", homework, problems, top_n=top_n)
    assert pairs.groupby("hw_id").size().to_dict() == {h.hw_id: top_n for h in homework}


def test_every_stored_pair_is_in_the_top_n_at_its_cutoff(fixture_ledger):
    be, handle = fixture_ledger
    pairs = echo.compute_local(COURSE, *echo.load_corpus(be, handle, COURSE)[:2])
    rows = _records(pairs)
    for r in rows:
        cutoff = max((r["hw_term_seq"], r["hw_session"]), (r["exam_term_seq"], ordering.session_or_end(r["exam_session"])))
        ahead = [q for q in rows if q["hw_id"] == r["hw_id"]
                 and (q["exam_term_seq"], ordering.session_or_end(q["exam_session"])) <= cutoff
                 and (-q["sim"], q["exam_id"], echo.problem_sort_key(q["problem"]))
                 < (-r["sim"], r["exam_id"], echo.problem_sort_key(r["problem"]))]
        assert len(ahead) < echo.TOP_N
    visible_to_final = _visible_rows(pairs, (20223, 20))
    top = visible_to_final.groupby("hw_id").head(echo.TOP_N)
    assert top.groupby("hw_id").size().max() == echo.TOP_N


def test_echo_pairs_are_causal_for_every_target(fixture_ledger):
    """Rows visible to a target T (sims included) equal a recomputation from T-visible rows only."""
    be, handle = fixture_ledger
    homework, problems, _ = echo.load_corpus(be, handle, COURSE)
    full = echo.compute_local(COURSE, homework, problems)
    exams = read_contract_csv(FIXTURES_DIR / "exams.csv", "exams")
    for target in exams[["term_seq", "session"]].itertuples(index=False):
        target = (int(target[0]), int(target[1]))
        alone = echo.compute_local(COURSE, *_truncated(homework, problems, target))
        assert _records(_visible_rows(full, target)) == _records(alone), target


def test_a_later_exam_cannot_displace_earlier_pairs():
    homework, problems = _single_cutoff_corpus()
    before = echo.compute_local("C", homework, problems)
    clones = [echo.ExamProblem("C-quiz1-2022F", str(i + 1), 20223, 20, h.text) for i, h in enumerate(homework)]
    after = echo.compute_local("C", homework, problems + clones)
    assert _records(after[after["exam_id"] != "C-quiz1-2022F"]) == _records(before)
    assert set(after["exam_id"]) >= {"C-quiz1-2022F"}


# ================================================================== tau
def test_recommend_tau_and_counting():
    pairs = pd.DataFrame({"hw_id": ["a", "a", "b", "c", "d"], "sim": [0.03, 0.01, 0.031, 0.032, 0.0328]})
    tau = echo.recommend_tau(pairs)
    assert tau == pytest.approx(0.032 - echo.TAU_BELOW) and tau < 0.032
    assert echo.homework_over_tau(pairs, tau) == 2
    tied = pd.DataFrame({"hw_id": ["a", "b", "c", "d"], "sim": [2 / 61] * 4})
    assert echo.homework_over_tau(tied, echo.recommend_tau(tied)) == 4      # ties at the top all count
    assert echo.recommend_tau(pd.DataFrame(columns=["hw_id", "sim"])) == echo.DEFAULT_TAU
    with pytest.raises(ValueError):
        echo.recommend_tau(pairs, quantile=1.5)


# ================================================================== load + CLI
def _ledger_rows(be, handle, course=COURSE) -> int:
    return int(be.query(handle, "SELECT count(*) AS n FROM {{echo_pairs}} WHERE course = $c", {"c": course})["n"][0])


def test_cli_upserts_prunes_and_prints_one_json_object(fixture_ledger, capsys):
    be, handle = fixture_ledger
    other = {"course": "ZZ.999", "hw_id": "ZZ.999-2022F-ps1-p1", "hw_term_seq": 20223, "hw_session": 3,
             "exam_id": "ZZ.999-final-2021F", "problem": "1", "exam_term_seq": 20213, "exam_session": 20, "sim": 0.03}
    be.load_table(handle, "echo_pairs", [other], mode="append")

    assert echo.main(["--course", COURSE]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    out = json.loads(lines[0])
    assert out["ok"] and out["method"] == "tfidf" and out["load_mode"] == "upsert" and out["tau_source"] == "quantile"
    assert out["pairs"] == out["rows_loaded"] > 0 and "warning" not in out
    assert _ledger_rows(be, handle) >= out["pairs"]                        # the fixture's made-up rows may linger

    assert echo.main(["--course", COURSE, "--prune", "--tau", "0.03"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["load_mode"] == "replace" and out["tau"] == 0.03 and out["tau_source"] == "argument"
    assert _ledger_rows(be, handle) == out["pairs"] and _ledger_rows(be, handle, "ZZ.999") == 1

    assert echo.main(["--course", COURSE]) == 0                           # replay: upsert is idempotent
    assert _ledger_rows(be, handle) == json.loads(capsys.readouterr().out)["pairs"]


@pytest.mark.parametrize("argv", [["--course", "bad course!"], ["--course", COURSE, "--tau", "-1"], [],
                                  ["--course", COURSE, "--method", "hotdata"]])
def test_cli_hard_faults_print_json(fixture_ledger, capsys, argv):
    if argv:
        assert echo.main(argv) == 1
    else:                                                    # usage error: JSON, then exit 1 (not argparse's 2)
        with pytest.raises(SystemExit) as stop:
            echo.main(argv)
        assert stop.value.code == 1
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is False and out["error"]


# ================================================================== hotdata path
def _overlap_search(homework):
    """Fake SearchFn: a 'bm25' leg scoring shared terms and a 'vector' leg of -Jaccard distance for every item."""
    docs = {h.hw_id: set(h.text.lower().split()) for h in homework}

    def search(query: str) -> pd.DataFrame:
        q = set(query.lower().split())
        rows = []
        for hw_id, terms in docs.items():
            shared = len(q & terms)
            if shared:
                rows.append(("bm25", hw_id, float(shared)))
            rows.append(("vector", hw_id, -(1 - shared / max(len(q | terms), 1))))
        return pd.DataFrame(rows, columns=["leg", "hw_id", "score"])
    return search


def test_hotdata_fusion_is_deterministic_and_causal(fixture_ledger):
    be, handle = fixture_ledger
    homework, problems, _ = echo.load_corpus(be, handle, COURSE)
    full = echo.compute_hotdata(COURSE, homework, problems, _overlap_search(homework))
    assert len(full) and _records(full) == _records(
        echo.compute_hotdata(COURSE, homework[::-1], problems[::-1], _overlap_search(homework)))
    to_contract_table("echo_pairs", full)
    for target in [(20223, 8), (20223, 20)]:
        hw_t, pr_t = _truncated(homework, problems, target)
        assert _records(_visible_rows(full, target)) == _records(
            echo.compute_hotdata(COURSE, hw_t, pr_t, _overlap_search(hw_t))), target
    summary = echo.run(COURSE, backend=be, method="hotdata", search=_overlap_search(homework), prune=True)
    assert summary["method"] == "hotdata" and summary["query_side"] == "exam" and summary["pairs"] == len(full)


def test_search_sql_renders_only_safe_literals():
    sql = echo.search_sql("Find $x^2$ the Fourier series'); DROP TABLE x; --", k=50)
    assert "bm25_search('default.public.homework_items', 'text_clean', 'Find the Fourier series DROP TABLE x', 50)" in sql
    assert "vector_search('default.public.homework_vec', 'text_clean', 'Find the Fourier series DROP TABLE x', 50)" in sql
    assert sql.endswith("WHERE course = $course GROUP BY leg, hw_id") and "--" not in sql and ";" not in sql
    alt = echo.search_sql("fourier", k=7, vector_style="vector_distance")
    assert "vector_distance(text_clean, 'fourier')" in alt and "{{homework_vec}}" in alt and "LIMIT 7" in alt
    assert "vector" not in echo.search_sql("fourier", k=7, vector=False).replace("vector_", "")
    for bad in ("$x$", "", None):
        with pytest.raises(ValueError):
            echo.search_text_literal(bad)
    for k in (0, True, 2.5, echo.MAX_SEARCH_K + 1):
        with pytest.raises(ValueError):
            echo.search_sql("fourier", k=k)
    with pytest.raises(ValueError):
        echo.search_table_literal("exam_items")


class FakeHotdata:
    """Stands in for HotdataClient over the fixture CSVs; each call is bound against the real method signature."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.db = ManagedDatabase(id="db1", description="ledger", default_connection_id="conn1")
        self.hw = read_contract_csv(FIXTURES_DIR / "homework_items.csv", "homework_items")
        self.items = read_contract_csv(FIXTURES_DIR / "exam_items.csv", "exam_items")
        self.loaded = None

    def _bind(self, method: str, *args, **kwargs) -> None:
        inspect.signature(getattr(HotdataClient, method)).bind(self, *args, **kwargs)
        self.calls.append((method, args, kwargs))

    def resolve_managed_database(self, name_or_id):
        self._bind("resolve_managed_database", name_or_id)
        return self.db

    def create_index(self, *args, **kwargs):
        self._bind("create_index", *args, **kwargs)
        return SimpleNamespace(to_dict=lambda: {"table": args[1], "index_type": kwargs["index_type"]})

    def load_managed_table(self, *args, **kwargs):
        self._bind("load_managed_table", *args, **kwargs)
        self.loaded = pq.read_table(kwargs["file"])
        return SimpleNamespace(row_count=self.loaded.num_rows)

    def execute_sql(self, *args, **kwargs):
        self._bind("execute_sql", *args, **kwargs)
        sql = args[0]
        match = re.search(r"bm25_search\('[^']*', 'text_clean', '([^']*)', (\d+)\)", sql)
        if match:
            homework = [echo.HomeworkDoc(r.hw_id, r.term_seq, r.session, search_terms(r.text_clean))
                        for r in self.hw.drop_duplicates("hw_id").itertuples()]
            frame = _overlap_search(homework)(match.group(1))
        elif "COUNT(*) AS n" in sql:
            frame = pd.DataFrame({"n": [len(self.hw)]})
        elif "COALESCE(SUM" in sql:
            frame = pd.DataFrame({"unregistered": [0], "sealed": [0]})
        elif "homework_items" in sql:
            frame = self.hw[["hw_id", "term_seq", "session", "topic_id", "text_clean"]]
        else:
            frame = self.items[["exam_id", "problem", "term_seq", "session", "topic_id", "text_clean"]]
        rows = [[None if (isinstance(v, float) and math.isnan(v)) else v for v in row] for row in frame.values.tolist()]
        return QueryResult(columns=list(frame.columns), rows=rows, row_count=len(rows), result_id=None,
                           query_run_id=None, execution_time_ms=1)


def test_hotdata_run_with_fake_client_matches_sdk_signatures():
    fake = FakeHotdata()
    be = HotdataBackend(client=fake, ledger_name="ledger")
    summary = echo.run(COURSE, backend=be, build_indexes=True, embedding_provider_id="sys_emb_openai")
    assert summary["ok"] and summary["method"] == "hotdata" and summary["pairs"] > 0
    assert [i["table"] for i in summary["indexes"]] == ["homework_items", "homework_vec"]
    index_calls = [c for c in fake.calls if c[0] == "create_index"]
    assert index_calls[0][1][0] == "db1" and index_calls[0][2]["index_type"] == "bm25"
    assert index_calls[1][2] == {"columns": ["text_clean"], "index_type": "vector", "embedding_provider_id": "sys_emb_openai"}
    searches = [c[1][0] for c in fake.calls if c[0] == "execute_sql" and "bm25_search" in c[1][0]]
    assert len(searches) == 16 and all("WHERE course = 'FX.101'" in s and ", 12)" in s for s in searches)
    load = [c for c in fake.calls if c[0] == "load_managed_table"][-1]
    assert load[1][1] == "echo_pairs" and load[2]["mode"] == "upsert" and load[2]["key"] == ["hw_id", "exam_id", "problem"]
    assert fake.loaded.schema.equals(arrow_schema("echo_pairs")) and fake.loaded.num_rows == summary["pairs"]
