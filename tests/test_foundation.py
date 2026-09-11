"""Foundation tests: the ordering rule, config rules, the local backend, and both lessons stores.
Offline only — the hotdata and HydraDB paths are exercised with fakes whose calls are bound against the
REAL installed SDK signatures, so a wrong keyword fails here instead of on stage."""
from __future__ import annotations

import csv
import inspect
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from hotdata_framework import HotdataClient, ManagedDatabase, QueryResult
from hydra_db.context.client import ContextClient
from hydra_db.errors import NotFoundError

from oracle import config, ordering
from oracle.backend import (RUN_TABLES, TABLE_KEYS, Backend, DbHandle, HotdataBackend, LocalDuckDBBackend,
                            arrow_schema, bind_params, columns, get_backend, ledger_schema, ledger_tables,
                            param_names, read_contract_csv, render_params, sql_literal)
from oracle.config import ConfigError, SealViolation
from oracle.lessons_store import (COLD_START_WEIGHTS, FEATURES, MID_SIGNAL_EXAMPLE, POINTER_ID, SIGNALS,
                                  HydraDBLessonsStore, LocalJSONLessonsStore, cold_start_record,
                                  get_lessons_store, predict_p, to_ledger_rows)

REPO = config.REPO_ROOT
FIXTURES = config.FIXTURES_DIR
WEIGHTS = {"intercept": -1.0, "x1": 0.8, "x2": 0.3, "x3": 1.1, "x4": 0.2, "x5": 0.4, "x6": -0.1, "x7": 0.6}


def _csv_rows(table: str) -> list[dict[str, str]]:
    with open(FIXTURES / f"{table}.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _count(be, db, table: str) -> int:
    return int(be.query(db, "SELECT count(*) AS n FROM {{%s}}" % table)["n"].iloc[0])


def _unset_after_test(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Unset env vars now AND on teardown, even if the code under test sets them directly."""
    for name in names:
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)


# ================================================================== ordering
def test_term_to_seq_and_back():
    assert ordering.term_to_seq("2011F") == 20113
    assert ordering.term_to_seq("2009S") == 20091
    assert ordering.term_to_seq("2010U") == 20102
    assert ordering.term_to_seq("2011f") == 20113
    assert ordering.seq_to_term(20113) == "2011F"
    for bad in ("2011X", "11F", "", "2011", "F2011", "2011FF", None, 20113):
        with pytest.raises(ValueError):
            ordering.term_to_seq(bad)  # type: ignore[arg-type]
    for bad in (20114, 20110, 2011, True):
        with pytest.raises(ValueError):
            ordering.seq_to_term(bad)


def test_visible_rule():
    v = ordering.visible
    assert v(20103, 25, 20113, 5)            # earlier term: any session
    assert not v(20123, 1, 20113, 30)        # later term: never
    assert v(20113, 7, 20113, 8)             # same term, earlier session
    assert not v(20113, 8, 20113, 8)         # same session is NOT visible
    assert not v(20113, 9, 20113, 8)
    assert not v(20113, None, 20113, 8)      # unknown session = end of term
    assert v(20113, 30, 20113, None)         # an end-of-term target sees numbered sessions
    assert not v(20113, None, 20113, None)
    assert v(20103, None, 20113, 1)
    assert not v(20113, float("nan"), 20113, 8)
    assert not v(20113, pd.NA, 20113, 8)
    assert v("2010F", 3, "2011F", 1)


def test_visible_sql_matches_python_on_duckdb_and_rendered():
    con = duckdb.connect(":memory:")
    con.execute('CREATE TABLE r (term_seq INTEGER, "session" INTEGER)')
    grid = [(ts, s) for ts in (20203, 20213, 20223) for s in (None, 1, 7, 8, 9, 20)]
    con.executemany("INSERT INTO r VALUES (?, ?)", grid)
    sql = f'SELECT term_seq, coalesce("session", -1) FROM r WHERE {ordering.visible_sql()}'
    for target_ts, target_s in ((20223, 8), (20223, 20), (20213, None), (20203, 1), (20233, 1)):
        expected = sorted((ts, -1 if s is None else s) for ts, s in grid
                          if ordering.visible(ts, s, target_ts, target_s))
        params = ordering.visibility_params(target_ts, target_s)
        assert sorted(con.execute(sql, bind_params(sql, params)).fetchall()) == expected
        rendered = render_params(sql, params)  # the hotdata path: validated literals, no $ left
        assert "$" not in rendered
        assert sorted(con.execute(rendered).fetchall()) == expected


def test_visible_sql_alias_and_bad_identifiers():
    frag = ordering.visible_sql("exam_term_seq", "exam_session", alias="e",
                                target_term_seq_param="tts", target_session_param="tss")
    assert 'e."exam_term_seq" < $tts' in frag and 'COALESCE(e."exam_session", 1000000) < $tss' in frag
    assert set(param_names(frag)) == {"tts", "tss"}
    for bad in ({"term_seq_col": "x; DROP"}, {"session_col": "a b"}, {"alias": "1e"}, {"target_session_param": "p-q"}):
        with pytest.raises(ValueError):
            ordering.visible_sql(**bad)


def test_feature_set():
    assert ordering.feature_set(20223, 20223) == "full"
    assert ordering.feature_set(20213, 20223) == "exam_history"
    assert ordering.feature_set("2022F", 20223) == "full"
    assert ordering.feature_set(20233, "2022F") == "exam_history"  # later than the published term, per contract
    assert not hasattr(ordering, "EXAM_HISTORY_ZERO_SIGNALS")      # D3: nothing is forced to zero


# ================================================================== config
def test_sealed_dir_inside_repo_is_rejected(tmp_path, monkeypatch):
    for inside in (REPO, REPO / "data", REPO / "contracts", REPO / "contracts" / "fixtures",
                   REPO / "contracts" / "fixtures" / "sealed" / ".." / ".."):
        with pytest.raises(ConfigError):
            config.resolve_sealed_dir(inside)
    link = tmp_path / "looks-outside"
    link.symlink_to(REPO / "kit", target_is_directory=True)
    with pytest.raises(ConfigError):
        config.resolve_sealed_dir(link)  # a symlink back into the repo is still inside it
    assert config.resolve_sealed_dir(FIXTURES / "sealed") == (FIXTURES / "sealed").resolve()
    outside = tmp_path / "sealed"
    assert config.resolve_sealed_dir(outside) == outside.resolve()
    assert config.get_settings().sealed_dir() == (FIXTURES / "sealed").resolve()  # from SEALED_DIR (conftest)
    monkeypatch.delenv("SEALED_DIR")
    with pytest.raises(ConfigError):
        config.resolve_sealed_dir()
    with pytest.raises(ConfigError):
        config.get_settings().sealed_dir()


def test_answer_key_path_requires_the_hash_file_first(tmp_path, sealed_dir):
    hash_file = tmp_path / "r1-FX.101-final-2022F.sha256"
    with pytest.raises(SealViolation):
        config.sealed_answer_key_path("FX.101-final-2022F", hash_file=hash_file)
    hash_file.write_text("not-a-hash\n")
    with pytest.raises(SealViolation):
        config.sealed_answer_key_path("FX.101-final-2022F", hash_file=hash_file)
    hash_file.write_text(config.sha256_hex(b"prediction") + "  r1-FX.101-final-2022F.json\n")
    path = config.sealed_answer_key_path("FX.101-final-2022F", hash_file=hash_file)
    assert path == sealed_dir / "answer_key_FX.101-final-2022F.csv" and path.is_file()
    with pytest.raises(ValueError):
        config.sealed_answer_key_path("../x", hash_file=hash_file)
    with pytest.raises(ConfigError):
        config.sealed_answer_key_path("FX.101-final-2022F", hash_file=hash_file, sealed_dir=REPO / "data")


def test_validate_id():
    for good in ("6.003-final-2011F", "r10-6.003-final-2011F", "T01", "s1", "FX.101", "2026-09-20", "a_b"):
        assert config.validate_id(good) == good
    for bad in ("", "a b", "x'; DROP TABLE runs; --", "a/b", "..", ".", "é", "a\n", None, 5, "x" * 201):
        with pytest.raises(ValueError):
            config.validate_id(bad)


def test_validate_ocw_url():
    for good in ("https://ocw.mit.edu/courses/6-003-signals-and-systems-fall-2011/pages/exams/",
                 "https://OCW.MIT.EDU/x", "https://ocw.mit.edu:443/x?y=1"):
        assert config.validate_ocw_url(good) == good
    for bad in ("http://ocw.mit.edu/x", "https://ocw.mit.edu.evil.com/x", "https://evil.com/ocw.mit.edu",
                "https://user@ocw.mit.edu/x", "https://ocw.mit.edu:8443/x", "ftp://ocw.mit.edu/x",
                "//ocw.mit.edu/x", "https://ocw.mit.edu\\@evil.com/", "javascript:alert(1)",
                "https://ocw.mit.edu./x", "https://ocw.mit.edu/a b", "https://ocw.mit.edu:99999/x", "", None):
        with pytest.raises(ValueError):
            config.validate_ocw_url(bad)
    assert config.is_ocw_url("https://ocw.mit.edu/") and not config.is_ocw_url("https://mit.edu/")


def test_dotenv_parser_and_loader(tmp_path, monkeypatch):
    text = ('# comment\nexport A=1\nB = "two words" # trailing\nC=\'x#y\'\nD=plain # note\nE=\n'
            'F=has#hash\nnot a line\n')
    assert config.parse_dotenv(text) == {"A": "1", "B": "two words", "C": "x#y", "D": "plain", "E": "", "F": "has#hash"}
    dotenv = tmp_path / ".env"
    dotenv.write_text("ORACLE_TEST_X=fromfile\nORACLE_TEST_Y=fromfile\nORACLE_TEST_Z=\n")
    monkeypatch.setenv("ORACLE_TEST_X", "fromenv")
    _unset_after_test(monkeypatch, "ORACLE_TEST_Y", "ORACLE_TEST_Z")
    config.load_dotenv(dotenv)
    assert os.environ["ORACLE_TEST_X"] == "fromenv"      # the environment wins
    assert os.environ["ORACLE_TEST_Y"] == "fromfile"
    assert "ORACLE_TEST_Z" not in os.environ              # empty values are not exported
    config.load_dotenv(dotenv, override=True)
    assert os.environ["ORACLE_TEST_X"] == "fromfile"


def test_settings_defaults_and_choices(oracle_env, monkeypatch):
    s = config.get_settings()
    assert (s.backend, s.lessons_store, s.student_store) == ("local", "local", "local")
    assert s.local_dir == Path(oracle_env).resolve()
    assert (s.hotdata_ledger_db, s.hydradb_database) == ("ledger", "exam-oracle")
    assert not any(s.public_dict()["secrets_present"].values())
    monkeypatch.setenv("ORACLE_BACKEND", "bogus")
    with pytest.raises(ConfigError):
        config.get_settings()
    monkeypatch.setenv("ORACLE_BACKEND", "hotdata")
    monkeypatch.setenv("HOTDATA_LEDGER_DB", "led'ger")
    with pytest.raises(ConfigError):
        config.get_settings()


_COGNEE_ENV_SCRIPT = textwrap.dedent("""
    import os, sys
    from pathlib import Path
    from types import SimpleNamespace
    from oracle import config
    for name in ("DATA_ROOT_DIRECTORY", "SYSTEM_ROOT_DIRECTORY", "COGNEE_LOGS_DIR"):
        os.environ.pop(name, None)
    local = Path(os.environ["ORACLE_LOCAL_DIR"]).resolve()
    effective = config.configure_cognee_env()
    assert effective == {"DATA_ROOT_DIRECTORY": str(local / "cognee" / "data"),
                         "SYSTEM_ROOT_DIRECTORY": str(local / "cognee" / "system"),
                         "COGNEE_LOGS_DIR": str(local / "cognee" / "logs")}, effective
    assert all(Path(p).is_dir() for p in effective.values())
    assert "cognee" not in sys.modules                          # the helper itself never imports cognee
    explicit = local / "explicit-data"
    os.environ["DATA_ROOT_DIRECTORY"] = str(explicit)           # an explicit setting is respected
    assert config.configure_cognee_env()["DATA_ROOT_DIRECTORY"] == str(explicit) and explicit.is_dir()
    del os.environ["SYSTEM_ROOT_DIRECTORY"]
    sys.modules["cognee"] = SimpleNamespace()                   # simulate "cognee already imported"
    try:
        config.configure_cognee_env()
    except RuntimeError:
        print("ok")
    else:
        raise AssertionError("expected RuntimeError: storage unset after cognee was imported")
""")


def test_configure_cognee_env(tmp_path):
    """In a fresh interpreter: once any test has imported cognee, "before the import" cannot be recreated in
    this process (the in-process version was skipped whenever a cognee test ran first)."""
    env = {**os.environ, "ORACLE_SKIP_DOTENV": "1", "ORACLE_LOCAL_DIR": str(tmp_path)}
    done = subprocess.run([sys.executable, "-c", _COGNEE_ENV_SCRIPT], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stdout + done.stderr


def test_canonical_json_and_hash():
    assert config.canonical_json({"b": 1, "a": "é", "c": [1.5, None]}) == '{"a":"é","b":1,"c":[1.5,null]}'.encode()
    with pytest.raises(ValueError):
        config.canonical_json({"p": float("nan")})
    assert config.sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_config_cli_prints_one_json_object(tmp_path):
    env = {k: v for k, v in os.environ.items()}
    env.update(ORACLE_SKIP_DOTENV="1", ORACLE_LOCAL_DIR=str(tmp_path), SEALED_DIR=str(FIXTURES / "sealed"))

    def run() -> tuple[int, dict]:
        out = subprocess.run([sys.executable, "-m", "oracle.config"], cwd=REPO, env=env,
                             capture_output=True, text=True, timeout=120)
        lines = [line for line in out.stdout.splitlines() if line.strip()]
        assert len(lines) == 1, out.stdout + out.stderr
        return out.returncode, json.loads(lines[0])

    code, obj = run()
    assert code == config.EXIT_OK and obj["ok"] and obj["sealed_dir_ok"] and obj["backend"] == "local"
    env["SEALED_DIR"] = str(REPO / "data")
    code, obj = run()
    assert code == config.EXIT_OK and obj["sealed_dir_ok"] is False and "inside the repository" in obj["sealed_dir_error"]
    env["ORACLE_BACKEND"] = "bogus"
    code, obj = run()
    assert code == config.EXIT_HARD and obj["ok"] is False


# ================================================================== backend: contract + local
def test_ledger_schema_and_table_keys():
    schema = ledger_schema()
    ddl_tables = [line.split()[2] for line in config.LEDGER_SCHEMA_PATH.read_text().splitlines()
                  if line.upper().startswith("CREATE TABLE")]
    assert list(schema) == ddl_tables and "run_labels" in schema     # every table, in file order
    for table, key in TABLE_KEYS.items():
        names = [c.name for c in columns(table)]
        assert all(k in names for k in key), table
    assert set(RUN_TABLES) <= set(schema) and "homework_vec" not in TABLE_KEYS
    assert arrow_schema("runs").field("run_seq").type == pa.int32()
    assert not next(c for c in columns("runs") if c.name == "hash").nullable


def test_read_contract_csv_types():
    g = read_contract_csv(FIXTURES / "guidelines.csv", "guidelines")
    assert list(g.columns) == [c.name for c in columns("guidelines")]
    assert g.loc[g.guideline_id == "G1", "from_session"].isna().all()
    assert g.loc[g.guideline_id == "G2", "share"].iloc[0] == 0.5
    exams = read_contract_csv(FIXTURES / "exams.csv", "exams")
    assert int(exams["sealed"].sum()) == 1 and exams["date"].isna().all()
    with pytest.raises(ValueError):
        read_contract_csv(FIXTURES / "exams.csv", "runs")


def test_ensure_ledger_is_idempotent(tmp_path):
    be = LocalDuckDBBackend(tmp_path / "lb", "ledger")
    handle = be.ensure_ledger()
    assert Path(handle.path).is_file() and handle.tables == ledger_tables()
    be.ensure_ledger()
    tables = set(be.query(handle, "SELECT table_name FROM information_schema.tables")["table_name"])
    assert tables == set(ledger_tables())
    assert isinstance(be, Backend)


def test_fixture_ledger_matches_the_csvs(fixture_ledger):
    be, handle = fixture_ledger
    for table in ledger_tables():
        expected = len(_csv_rows(table)) if (FIXTURES / f"{table}.csv").is_file() else 0
        assert _count(be, handle, table) == expected, table


def test_visibility_on_the_fixture_ledger(fixture_ledger):
    be, ledger = fixture_ledger
    items = _csv_rows("exam_items")
    sql = ("SELECT exam_id, problem, topic_id FROM {{exam_items}} WHERE course = $course AND "
           + ordering.visible_sql())
    expected_counts = {"FX.101-quiz1-2020F": 0, "FX.101-final-2021F": 11, "FX.101-quiz1-2022F": 16,
                       "FX.101-final-2022F": 19}
    for exam_id, expected in expected_counts.items():
        target = be.query(ledger, "SELECT term_seq, session FROM {{exams}} WHERE exam_id = $e", {"e": exam_id}).iloc[0]
        params = {"course": "FX.101", **ordering.visibility_params(int(target.term_seq), int(target.session))}
        got = be.query(ledger, sql, params)
        python = [r for r in items if ordering.visible(int(r["term_seq"]), int(r["session"]),
                                                       int(target.term_seq), int(target.session))]
        assert len(got) == len(python) == expected, exam_id
    lectures_sql = "SELECT count(*) AS n FROM {{lectures}} WHERE " + ordering.visible_sql()
    assert int(be.query(ledger, lectures_sql, ordering.visibility_params(20223, 8))["n"].iloc[0]) == 7
    assert int(be.query(ledger, lectures_sql, ordering.visibility_params(20213, 20))["n"].iloc[0]) == 0


def _run_row(**overrides):
    row = {"run_id": "r1-FX.101-quiz1-2021F", "run_seq": 1, "course": "FX.101", "target_exam": "FX.101-quiz1-2021F",
           "feature_set": "exam_history", "cold_start": False, "k": 2, "model_pts": None, "even_pts": None,
           "lastexam_pts": None, "recall_k": None, "brier": None, "tokens": 0, "seconds": 1.5,
           "checks_first_try": True, "leakage_ok": True, "key_source": "human", "lessons_run_seq": None,
           "hash": "a" * 64, "made_at": "2026-09-11T09:10:00-07:00"}
    row.update(overrides)
    return row


def test_load_table_modes_and_contract_checks(tmp_path):
    be = LocalDuckDBBackend(tmp_path / "lb", "ledger")
    h = be.ensure_ledger()
    assert be.load_table(h, "runs", [_run_row()], mode="append") == 1
    be.load_table(h, "runs", [_run_row(model_pts=0.5)], mode="upsert")
    df = be.query(h, "SELECT run_id, model_pts, lessons_run_seq FROM {{runs}}")
    assert len(df) == 1 and df.model_pts.iloc[0] == 0.5 and pd.isna(df.lessons_run_seq.iloc[0])
    be.load_table(h, "runs", [_run_row(run_id="r2-FX.101-final-2021F", run_seq=2)], mode="upsert")
    assert _count(be, h, "runs") == 2
    be.load_table(h, "runs", [_run_row()], mode="append")
    assert _count(be, h, "runs") == 3                                   # append does not de-duplicate
    be.load_table(h, "runs", pd.DataFrame([_run_row(run_seq=9)]), mode="replace")
    assert be.query(h, "SELECT run_seq FROM {{runs}}")["run_seq"].tolist() == [9]
    be.load_table(h, "runs", pa.Table.from_pylist([_run_row(run_seq=7)], schema=arrow_schema("runs")), mode="replace")
    assert be.query(h, "SELECT run_seq FROM {{runs}}")["run_seq"].tolist() == [7]

    bad_calls = [
        dict(table="runs", df=[_run_row(), _run_row()], mode="upsert"),            # duplicate keys in one batch
        dict(table="runs", df=[{k: v for k, v in _run_row().items() if k != "hash"}], mode="append"),
        dict(table="runs", df=[{**_run_row(), "extra": 1}], mode="append"),
        dict(table="runs", df=[_run_row(hash=None)], mode="append"),                # NOT NULL
        dict(table="runs", df=[_run_row(k="two")], mode="append"),                  # wrong type
        dict(table="runs", df=[_run_row(k=2.5)], mode="append"),                    # fractional INTEGER
        dict(table="runs", df=[_run_row()], mode="merge"),
        dict(table="nope", df=[], mode="append"),
        dict(table="homework_vec", df=[], mode="upsert"),                           # no key declared
    ]
    for call in bad_calls:
        with pytest.raises(ValueError):
            be.load_table(h, **call)
    assert be.query(h, "SELECT run_seq FROM {{runs}}")["run_seq"].tolist() == [7]  # untouched
    vec = [{"course": "FX.101", "hw_id": "FX.101-2022F-ps1-p1", "text_clean": "a"}]
    be.load_table(h, "homework_vec", vec, mode="upsert", key=["hw_id"])
    be.load_table(h, "homework_vec", vec, mode="upsert", key=["hw_id"])
    assert _count(be, h, "homework_vec") == 1


def test_query_is_read_only_and_params_are_validated(fixture_ledger):
    be, h = fixture_ledger
    for write in ("INSERT INTO {{runs}} SELECT * FROM {{runs}}", "DELETE FROM {{runs}}", "DROP TABLE {{runs}}"):
        with pytest.raises(duckdb.Error):
            be.query(h, write)
    assert _count(be, h, "runs") == 3
    with pytest.raises(ValueError):
        be.query(h, "SELECT * FROM {{runs}} WHERE run_id = $rid")                          # missing
    with pytest.raises(ValueError):
        be.query(h, "SELECT * FROM {{runs}} WHERE run_id = $rid", {"rid": "x' OR '1'='1"})  # injection
    with pytest.raises(ValueError):
        be.query(h, "SELECT * FROM {{runs}} WHERE k = $1", {"1": 2})                        # positional
    with pytest.raises(ValueError):
        be.query(h, "SELECT * FROM {{nope}}")
    with pytest.raises(ValueError):
        be.query(h, "SELECT * FROM {{ runs")
    df = be.query(h, "SELECT '$notaparam' AS s, $k AS k -- $alsonot\n", {"k": 3, "unused": "x"})
    assert df.s.iloc[0] == "$notaparam" and df.k.iloc[0] == 3
    df = be.query(h, "SELECT run_id FROM {{runs}} WHERE run_seq >= $n AND key_source = $src ORDER BY run_seq",
                  {"n": 2, "src": "human"})
    assert df.run_id.tolist() == ["r2-FX.101-final-2021F", "r3-FX.101-quiz1-2022F"]


def test_sql_literal_validator():
    assert sql_literal(None) == "NULL" and sql_literal(True) == "TRUE" and sql_literal(3) == "3"
    assert sql_literal(0.7) == "0.7" and sql_literal("6.003-final-2011F") == "'6.003-final-2011F'"
    for bad in ("a'b", "a b", "", float("inf"), float("nan")):
        with pytest.raises(ValueError):
            sql_literal(bad)
    for bad in ([1], {"a": 1}, b"x"):
        with pytest.raises(TypeError):
            sql_literal(bad)


def test_run_db_lifecycle(fixture_ledger):
    be, ledger = fixture_ledger
    h = be.create_run_db("r3-FX.101-quiz1-2022F")
    assert (h.name, h.backend, h.tables) == ("run-r3-FX.101-quiz1-2022F", "local", RUN_TABLES)
    assert Path(h.path).is_file() and h.expires_at.endswith("Z")
    assert DbHandle.from_dict(json.loads(json.dumps(h.to_dict()))) == h
    target = be.query(ledger, "SELECT term_seq, session FROM {{exams}} WHERE exam_id = $e",
                      {"e": "FX.101-quiz1-2022F"}).iloc[0]
    params = {"course": "FX.101", **ordering.visibility_params(int(target.term_seq), int(target.session))}
    for table in ("exam_items", "lectures", "homework_items"):
        rows = be.query(ledger, "SELECT * FROM {{%s}} WHERE course = $course AND %s" % (table, ordering.visible_sql()), params)
        be.load_table(h, table, rows, mode="replace")
    assert (_count(be, h, "exam_items"), _count(be, h, "lectures"), _count(be, h, "homework_items")) == (16, 7, 4)
    leaked = be.query(h, "SELECT count(*) AS n FROM {{exam_items}} WHERE NOT " + ordering.visible_sql(), params)
    assert int(leaked["n"].iloc[0]) == 0
    with pytest.raises(duckdb.Error):
        be.query(h, "SELECT * FROM {{predictions}}")          # not a run table
    with pytest.raises(ValueError):
        be.load_table(h, "predictions", [], mode="replace")
    h2 = be.create_run_db("r3-FX.101-quiz1-2022F")             # a replay starts from an empty database
    assert _count(be, h2, "exam_items") == 0
    be.drop_run_db(h2)
    assert not Path(h2.path).exists()
    be.drop_run_db(h2)                                        # idempotent
    for refused in (ledger, "ledger", "not-a-run"):
        with pytest.raises(ValueError):
            be.drop_run_db(refused)
    with pytest.raises(ValueError):
        be.create_run_db("../evil")
    with pytest.raises(ValueError):
        be.create_run_db("r9-x", expires_in_hours=0)
    with pytest.raises(FileNotFoundError):
        be.query("run-r404-nothing", "SELECT 1")


def test_get_backend_factory_never_connects(monkeypatch):
    assert isinstance(get_backend(), LocalDuckDBBackend)
    monkeypatch.setenv("ORACLE_BACKEND", "hotdata")
    be = get_backend()
    assert isinstance(be, HotdataBackend) and be._client is None and isinstance(be, Backend)
    with pytest.raises(RuntimeError):
        be.client                                              # gated: HOTDATA_API_KEY is not set in tests


# ================================================================== backend: hotdata glue (fake client)
class FakeHotdata:
    """Stands in for HotdataClient; each call is bound against the real method signature first."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.dbs: dict[str, ManagedDatabase] = {}
        self.loaded: pa.Table | None = None

    def _record(self, method: str, *args, **kwargs) -> None:
        inspect.signature(getattr(HotdataClient, method)).bind(self, *args, **kwargs)
        self.calls.append((method, args, kwargs))

    def create_managed_database(self, *args, **kwargs):
        self._record("create_managed_database", *args, **kwargs)
        n = len(self.dbs) + 1
        db = ManagedDatabase(id=f"db{n}", description=args[0] if args else kwargs.get("description"),
                             default_connection_id=f"conn{n}")
        self.dbs[db.id] = db
        return db

    def resolve_managed_database(self, name_or_id):
        self._record("resolve_managed_database", name_or_id)
        for db in self.dbs.values():
            if name_or_id in (db.id, db.description):
                return db
        raise KeyError(name_or_id)

    def load_managed_table(self, *args, **kwargs):
        self._record("load_managed_table", *args, **kwargs)
        self.loaded = pq.read_table(kwargs["file"])
        return SimpleNamespace(row_count=self.loaded.num_rows)

    def execute_sql(self, *args, **kwargs):
        self._record("execute_sql", *args, **kwargs)
        return QueryResult(columns=["n"], rows=[[1]], row_count=1, result_id=None, query_run_id=None, execution_time_ms=1)

    def delete_managed_database(self, *args, **kwargs):
        self._record("delete_managed_database", *args, **kwargs)


def test_hotdata_backend_glue_with_fake_client():
    fake = FakeHotdata()
    be = HotdataBackend(client=fake, ledger_name="ledger")
    assert be.table_ref("runs") == '"default"."public"."runs"'
    h = be.ensure_ledger()
    create = fake.calls[-1]
    assert create[0] == "create_managed_database" and create[2]["tables"] == list(ledger_tables())
    assert create[2]["keys"]["run_labels"] == ["run_id", "topic_id"] and "expires_at" not in create[2]

    assert be.load_table(h, "runs", [_run_row()], mode="upsert") == 1
    method, args, kwargs = fake.calls[-1]
    assert method == "load_managed_table" and args[1] == "runs"
    assert kwargs["mode"] == "upsert" and kwargs["key"] == ["run_id"] and kwargs["file"].endswith(".parquet")
    assert fake.loaded.schema.equals(arrow_schema("runs"))
    be.load_table(h, "runs", [_run_row()], mode="replace")
    assert fake.calls[-1][2]["key"] is None

    df = be.query(h, "SELECT count(*) AS n FROM {{runs}} WHERE run_id = $rid AND k > $k AND '$x' <> ''",
                  {"rid": "r1-FX.101-quiz1-2021F", "k": 2})
    method, args, kwargs = fake.calls[-1]
    assert args[0] == ("SELECT count(*) AS n FROM \"default\".\"public\".\"runs\" "
                       "WHERE run_id = 'r1-FX.101-quiz1-2021F' AND k > 2 AND '$x' <> ''")
    assert kwargs["database"].id == "db1" and df["n"].tolist() == [1]
    before = len(fake.calls)
    with pytest.raises(ValueError):
        be.query(h, "SELECT * FROM {{runs}} WHERE run_id = $rid", {"rid": "x' OR '1'='1"})
    assert len(fake.calls) == before                         # rejected before anything is sent

    rh = be.create_run_db("r1-FX.101-quiz1-2021F", expires_in_hours=2)
    kwargs = fake.calls[-1][2]
    assert kwargs["tables"] == list(RUN_TABLES) and kwargs["expires_at"].endswith("Z")
    assert "homework_vec" not in kwargs["keys"] and kwargs["keys"]["exam_items"] == ["exam_id", "problem", "topic_id"]
    assert rh.database_id == "db2" and DbHandle.from_dict(rh.to_dict()) == rh
    be.drop_run_db(rh)
    assert fake.calls[-1][0] == "delete_managed_database"
    before = len(fake.calls)
    with pytest.raises(ValueError):
        be.drop_run_db(h)
    assert len(fake.calls) == before


# ================================================================== lessons
def test_cold_start_weights():
    assert tuple(COLD_START_WEIGHTS) == SIGNALS and COLD_START_WEIGHTS["x6"] == 0.0
    others = {COLD_START_WEIGHTS[k] for k in FEATURES if k != "x6"}
    assert len(others) == 1 and 0 < next(iter(others)) <= 1
    p_mid = predict_p(COLD_START_WEIGHTS, MID_SIGNAL_EXAMPLE)
    assert 0.3 <= p_mid <= 0.5
    assert predict_p(COLD_START_WEIGHTS, {}) < p_mid < predict_p(COLD_START_WEIGHTS, {k: 1.0 for k in FEATURES})
    assert cold_start_record() == {"run_seq": None, "weights": COLD_START_WEIGHTS, "statements": {}, "supporting_runs": []}


def test_local_lessons_store(tmp_path, fixture_ledger):
    store = LocalJSONLessonsStore(tmp_path / "lessons.jsonl")
    assert store.read_latest() == cold_start_record()
    store.write(1, COLD_START_WEIGHTS, {}, ["r1-FX.101-quiz1-2021F"])
    rec3 = store.write(3, WEIGHTS, {"x3": "Homework echo is  the strongest signal so far."},
                       ["r1-FX.101-quiz1-2021F", "r2-FX.101-final-2021F", "r3-FX.101-quiz1-2022F"])
    assert rec3["statements"] == {"x3": "Homework echo is the strongest signal so far."}
    store.write(2, COLD_START_WEIGHTS, {}, [])                   # a late write of an older run
    assert store.read_latest() == rec3
    newer = store.write(3, {**WEIGHTS, "x1": 0.9}, {}, [])       # a replay of run 3 wins the tie
    assert store.read_latest() == newer and len(store.history()) == 4
    for bad in (dict(weights={k: v for k, v in WEIGHTS.items() if k != "x7"}),
                dict(weights={**WEIGHTS, "x8": 1.0}), dict(weights={**WEIGHTS, "x2": float("nan")}),
                dict(weights={**WEIGHTS, "x2": True}), dict(statements={"x9": "?"}),
                dict(supporting_runs=["bad run"]), dict(supporting_runs="r1-FX.101-quiz1-2021F"), dict(run_seq=-1)):
        args = {"run_seq": 4, "weights": WEIGHTS, "statements": {}, "supporting_runs": [], **bad}
        with pytest.raises(ValueError):
            store.write(**args)
    assert len(store.history()) == 4

    be, h = fixture_ledger
    rows = to_ledger_rows(rec3)
    assert [r["signal"] for r in rows] == list(SIGNALS)
    be.load_table(h, "lessons", rows, mode="upsert")
    be.load_table(h, "lessons", rows, mode="upsert")
    assert _count(be, h, "lessons") == 8


def test_get_lessons_store_factory_never_connects(monkeypatch, oracle_env):
    store = get_lessons_store()
    assert isinstance(store, LocalJSONLessonsStore) and store.path == Path(oracle_env).resolve() / "lessons.jsonl"
    monkeypatch.setenv("LESSONS_STORE", "hydradb")
    hydra = get_lessons_store()
    assert isinstance(hydra, HydraDBLessonsStore) and hydra._client is None
    assert (hydra.database, hydra.collection) == ("exam-oracle", "shared")
    with pytest.raises(ConfigError):
        hydra.client                                            # gated: HYDRADB_API_KEY is not set in tests


class FakeContext:
    """Stands in for hydra_db ContextClient; each call is bound against the real method signature first."""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}
        self.ingests: list[dict] = []
        self.content_available = True

    def _bind(self, method: str, **kwargs) -> None:
        inspect.signature(getattr(ContextClient, method)).bind(self, **kwargs)

    def ingest(self, **kwargs):
        self._bind("ingest", **kwargs)
        self.ingests.append(kwargs)
        for item in json.loads(kwargs["memories"]):
            self.store[item["id"]] = item
        return SimpleNamespace(success=True)

    def inspect(self, **kwargs):
        self._bind("inspect", **kwargs)
        item = self.store.get(kwargs["id"])
        if item is None:
            raise NotFoundError(body={"error": "not found"})
        return SimpleNamespace(data=SimpleNamespace(content=item["text"] if self.content_available else None))

    def list(self, **kwargs):
        self._bind("list", **kwargs)
        found = [SimpleNamespace(memory_id=i, additional_metadata=self.store[i]["additional_metadata"])
                 for i in kwargs.get("ids") or [] if i in self.store]
        return SimpleNamespace(data=SimpleNamespace(user_memories=found))


def test_hydradb_lessons_store_with_fake_client():
    fake = SimpleNamespace(context=FakeContext())
    store = HydraDBLessonsStore(client=fake, database="exam-oracle")
    assert store.read_latest() == cold_start_record()
    runs = ["r1-FX.101-quiz1-2021F", "r2-FX.101-final-2021F"]
    rec = store.write(2, WEIGHTS, {"x3": "Homework echo is the strongest signal so far."}, runs)
    call = fake.context.ingests[-1]
    assert (call["database"], call["collection"], call["type"], call["upsert"]) == ("exam-oracle", "shared", "memory", "true")
    items = json.loads(call["memories"])
    assert [i["id"] for i in items] == [f"lesson_{s}_r2" for s in SIGNALS] + [POINTER_ID]
    assert all(i["infer"] is False for i in items)
    assert all(len(json.dumps(i["additional_metadata"], separators=(",", ":")).encode()) <= 1024 for i in items)
    x3 = items[SIGNALS.index("x3")]
    assert x3["text"] == "Homework echo is the strongest signal so far."
    assert x3["additional_metadata"] == {"kind": "lesson", "signal": "x3", "weight": 1.1, "run_seq": 2, "runs": ";".join(runs)}
    assert json.loads(items[-1]["text"]) == rec and store.read_latest() == rec

    store.write(1, COLD_START_WEIGHTS, {}, [])                  # older run: lessons stored, pointer untouched
    assert POINTER_ID not in [i["id"] for i in json.loads(fake.context.ingests[-1]["memories"])]
    assert store.read_latest() == rec
    rec5 = store.write(5, {**WEIGHTS, "x6": 0.25}, {}, runs)
    assert store.read_latest() == rec5

    fake.context.content_available = False                     # fallback: weights copy in the pointer metadata
    latest = store.read_latest()
    assert latest["run_seq"] == 5 and latest["weights"] == rec5["weights"] and latest["statements"] == {}
