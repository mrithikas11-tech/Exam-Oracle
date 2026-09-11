"""Offline tests for Person B's Cognee models, ontology, tagging, feedback and student stores.

Nothing here calls an LLM or a network service: the conftest removes LLM_API_KEY and the live credentials;
HydraDB and Cognee are replaced by fakes whose calls are bound against the REAL installed SDK signatures (so a
wrong keyword fails here, not on stage); cognee itself is imported only inside tests, after the session fixture
has pointed its storage at a temporary folder.
"""
from __future__ import annotations

import asyncio
import csv
import inspect
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import rdflib
from rdflib.namespace import RDF, RDFS
from hydra_db import HydraDB
from hydra_db.context.client import ContextClient
from hydra_db.errors import NotFoundError

from oracle import cognee_feedback, cognee_tag, config, ontology, students
from oracle.backend import read_contract_csv
from oracle.ontology import TopicIndex, read_topics_csv

REPO = config.REPO_ROOT
FIXTURES = config.FIXTURES_DIR
TOPICS_CSV = FIXTURES / "topics.csv"
COURSE = "FX.101"
PREFS = students.PREF_FIELDS
MAYA = {"student_id": "s1", "display_name": "Maya (fictional)", "course": COURSE, "exam_id": "FX.101-final-2022F",
        "exam_date": "2026-09-20", "weekly_hours": 6, "style_order": "examples_first", "length": "short",
        "modality": "visual", "pace": "normal"}
LEO = {"student_id": "s2", "display_name": "Leo (fictional)", "course": COURSE, "exam_id": "FX.101-final-2022F",
       "exam_date": "2026-09-27", "weekly_hours": 12, "style_order": "proofs_first", "length": "detailed",
       "modality": "verbal", "pace": "normal"}


def _prefs(profile: dict) -> dict:
    return {f: profile[f] for f in PREFS}


def _contract_examples() -> list:
    """The JSON examples of contracts/student.md, in order: pref item, feedback item, event, read, plan."""
    blocks = re.findall(r"```json\n(.*?)```", (config.CONTRACTS_DIR / "student.md").read_text(encoding="utf-8"), re.S)
    return [json.loads(b) for b in blocks]


@pytest.fixture(scope="module")
def models(oracle_env):
    """oracle.models, imported only now: cognee storage must already point at the temporary LOCAL_DIR."""
    from oracle import models as m
    assert Path(os.environ["DATA_ROOT_DIRECTORY"]).is_relative_to(Path(oracle_env).resolve())
    assert Path(os.environ["SYSTEM_ROOT_DIRECTORY"]).is_relative_to(Path(oracle_env).resolve())
    assert "LLM_API_KEY" not in os.environ
    return m


# ------------------------------------------------------------------ models
def test_models_identity(models):
    topic = models.Topic(course=COURSE, name="Fourier series")
    assert topic.id == models.Topic(course="fx.101", name="fourier_series", topic_id="T05").id
    assert topic.id == models.Topic.id_for(COURSE, "Fourier series")
    assert topic.id == uuid.uuid5(uuid.NAMESPACE_OID, "Topic:fx.101|fourier_series")   # DataPoint.id_for, 1.5.4
    assert topic.id != models.Topic(course=COURSE, name="Fourier transform").id
    assert topic.id != models.Topic(course="FX.102", name="Fourier series").id

    p1 = models.ExamProblem(problem_id="FX.101-final-2020F-p2", exam_id="FX.101-final-2020F", points=20, text="a",
                            topics=[topic])
    p2 = models.ExamProblem(problem_id="FX.101-final-2020F-p2", exam_id="FX.101-final-2020F", text="b")
    assert p1.id == p2.id == models.ExamProblem.id_for("FX.101-final-2020F-p2")
    assert isinstance(p1.topics[0], models.Topic) and p2.topics == []
    assert (models.HomeworkProblem(hw_id="FX.101-2022F-ps1-p1", text="x").id
            == models.HomeworkProblem(hw_id="FX.101-2022F-ps1-p1", text="y").id)
    assert (models.Lecture(course=COURSE, term="2022F", session=3, title="L3").id
            == models.Lecture.id_for(COURSE, "2022F", 3))
    maya = models.StudentProfile(**MAYA)
    assert maya.id == models.StudentProfile(**{**MAYA, "length": "detailed"}).id == models.StudentProfile.id_for("s1")
    with pytest.raises(Exception):
        models.StudentProfile(**{**MAYA, "weekly_hours": 0.1})
    with pytest.raises(Exception):
        models.StudentProfile(**{**MAYA, "style_order": "random"})
    for model in models.MODELS:
        meta = model.model_fields["metadata"].default
        assert set(meta) == {"index_fields", "identity_fields"}, model.__name__
        assert model._get_identity_fields() == meta["identity_fields"]
    rows = [{"course": COURSE, "topic_id": "T05", "topic": "Fourier series"}]
    assert [t.id for t in models.topics_from_rows(rows)] == [topic.id]


def test_import_cognee_undoes_dotenv_override(models, monkeypatch):
    monkeypatch.setenv("ORACLE_T_KEEP", "process")
    before = dict(os.environ)
    monkeypatch.setenv("ORACLE_T_KEEP", "from-dotenv")        # cognee's override=True clobbered it
    monkeypatch.setenv("ORACLE_T_SECRET", "from-dotenv")      # a .env key that was not set before
    monkeypatch.setenv("ORACLE_T_OWN", "cognee-sets-this")    # cognee's own import-time setting
    touched = models._undo_dotenv(before, {"ORACLE_T_KEEP", "ORACLE_T_SECRET"})
    assert touched == ["ORACLE_T_KEEP", "ORACLE_T_SECRET"]
    assert os.environ["ORACLE_T_KEEP"] == "process" and "ORACLE_T_SECRET" not in os.environ
    assert os.environ["ORACLE_T_OWN"] == "cognee-sets-this"


def test_live_cognee_calls_match_installed_sdk(models):
    import cognee
    from cognee.api.v1.remember.remember import _COGNIFY_ONLY, RememberKwargs
    from cognee.context_global_variables import set_database_global_context_variables  # noqa: F401
    from cognee.infrastructure.databases.graph import get_graph_engine  # noqa: F401
    from cognee.modules.data.methods import get_datasets_by_name  # noqa: F401
    from cognee.modules.data.methods.check_dataset_name import check_dataset_name
    from cognee.modules.users.methods import get_default_user  # noqa: F401

    params = inspect.signature(cognee.remember).parameters
    assert {"dataset_name", "custom_prompt", "self_improvement", "dry_run"} <= set(params)
    assert {"graph_model", "node_set"} <= set(RememberKwargs.__annotations__) and "config" in _COGNIFY_ONLY
    inspect.signature(cognee.session.add_feedback).bind(session_id="run-3", qa_id="q", feedback_text="t",
                                                        feedback_score=4)
    inspect.signature(cognee.session.get_session).bind(session_id="run-3")
    inspect.signature(cognee.improve).bind(dataset="course-FX-101", session_ids=["run-3"])
    check_dataset_name(cognee_tag.course_dataset_name("6.003"))          # 'course-6-003'
    check_dataset_name(students.student_dataset_name("s1"))              # 'student-1'
    with pytest.raises(ValueError):
        check_dataset_name("course-6.003")


# ------------------------------------------------------------------ ontology
def test_ontology_from_fixture_topics(models, tmp_path):
    topics = read_topics_csv(TOPICS_CSV, COURSE)
    assert [t.topic_id for t in topics] == [f"T0{i}" for i in range(1, 9)]
    data = ontology.serialize_ontology(ontology.build_ontology(topics, COURSE))
    assert data == ontology.serialize_ontology(ontology.build_ontology(topics, COURSE))
    graph = rdflib.Graph().parse(data=data, format="turtle")
    topic_class = rdflib.URIRef(f"{ontology.BASE_IRI}FX-101/class#Topic")
    individuals = set(graph.subjects(RDF.type, topic_class))
    assert {str(graph.value(i, RDFS.label)) for i in individuals} == {t.topic for t in topics}

    from cognee.modules.ontology.exceptions import EmptyOntologyInStrictModeError
    from cognee.modules.ontology.get_default_ontology_resolver import (get_configured_ontology_mode,
                                                                       get_configured_ontology_resolver)
    cfg = ontology.cognee_ontology_config(data)
    resolver = cfg["ontology_config"]["ontology_resolver"]
    assert set(cfg) == {"ontology_config"} and cfg["ontology_config"]["ontology_mode"] == "strict"
    assert get_configured_ontology_mode(cfg) == "strict" and get_configured_ontology_resolver(cfg) is resolver
    assert sorted(resolver.lookup["individuals"]) == sorted(ontology.name_key(t.topic) for t in topics)
    assert "topic" in resolver.lookup["classes"]
    assert resolver.find_closest_match("Fourier Series", "individuals") == "fourier_series"
    assert resolver.find_closest_match("quantum chromodynamics", "individuals") is None
    path = ontology.write_ontology(topics, COURSE, tmp_path / "fx.ttl")
    assert ontology.cognee_ontology_config(path, mode="annotate")["ontology_config"]["ontology_mode"] == "annotate"
    with pytest.raises(EmptyOntologyInStrictModeError):
        ontology.cognee_ontology_config(b"@prefix ex: <https://exam-oracle.invalid/x#> .\nex:a ex:b ex:c .\n")
    with pytest.raises(ValueError):
        ontology.cognee_ontology_config(data, mode="loose")
    assert ontology.ontology_env(path) == {"ONTOLOGY_FILE_PATH": str(path), "ONTOLOGY_MODE": "strict",
                                           "ONTOLOGY_RESOLVER": "rdflib", "MATCHING_STRATEGY": "fuzzy"}


def test_ontology_cli(tmp_path):
    out = tmp_path / "fx.ttl"
    proc = subprocess.run([sys.executable, "-m", "oracle.ontology", "--course", COURSE, "--topics", str(TOPICS_CSV),
                           "--out", str(out)], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 1
    result = json.loads(lines[0])
    assert result["ok"] and result["topics"] == 8 and result["sha256"] == config.sha256_hex(out.read_bytes())


def test_topic_index_matching():
    index = TopicIndex(read_topics_csv(TOPICS_CSV, COURSE))
    assert [index.match(v) for v in ("T05", "t05", "Fourier Series", "fourier-series", "Fourier seres", "Z-transform")] \
        == ["T05", "T05", "T05", "T05", "T05", "T03"]
    assert index.match("Laplace transform") is None and index.match("") is None and index.match(None) is None
    assert index.match_id("Fourier series") is None and index.name("T07") == "Sampling"
    dup = [ontology.TopicRow(COURSE, "T01", "Z transform"), ontology.TopicRow(COURSE, "T02", "Z-Transform")]
    with pytest.raises(ValueError):
        TopicIndex(dup)


# ------------------------------------------------------------------ tagging
def test_topics_from_graph():
    index = TopicIndex(read_topics_csv(TOPICS_CSV, COURSE))
    nodes = [("p2", {"type": "ExamProblem", "problem_id": "fx.101-final-2021f-p2"}),
             ("uuid-p3", {"type": "ExamProblem", "problem_id": "garbled by the model"}),
             ("px", {"type": "ExamProblem", "problem_id": "FX.101-final-2020F-p1"}),
             ("t-a", {"type": "Topic", "name": "Sampling", "topic_id": "T07"}),
             ("t-b", {"type": "Topic", "name": "modulation"}),
             ("t-c", {"type": "Topic", "name": "Laplace transform"}),
             ("t-d", {"type": "Topic", "name": "Convolution", "topic_id": "T99"}),
             ("chunk", {"type": "DocumentChunk", "text": "..."})]
    edges = [("p2", "t-a", "topics", {}), ("p2", "t-b", "topics", {}), ("p2", "t-c", "topics", {}),
             ("chunk", "p2", "contains", {}), ("t-d", "uuid-p3", "topics", {}), ("px", "t-a", "topics", {})]
    tags, unmatched = cognee_tag.topics_from_graph(
        nodes, edges, ["FX.101-final-2021F-p2", "FX.101-final-2021F-p3"], index,
        problem_node_ids={"FX.101-final-2021F-p3": "uuid-p3"})
    assert tags == {"FX.101-final-2021F-p2": ["T07", "T08"], "FX.101-final-2021F-p3": ["T04"]}
    assert unmatched == {"FX.101-final-2021F-p2": ["Laplace transform"]}


FIXTURE_PROBLEMS = [("FX.101-quiz1-2020F", "1"), ("FX.101-final-2020F", "2"), ("FX.101-final-2021F", "2"),
                    ("FX.101-quiz1-2022F", "2")]


def _fixture_items(tmp_path: Path) -> tuple[Path, Path, dict]:
    """Items + mock tags for some non-sealed fixture problems, from contracts/fixtures/exam_items.csv."""
    with open(FIXTURES / "exam_items.csv", newline="", encoding="utf-8") as fh:
        fixture = [r for r in csv.DictReader(fh) if (r["exam_id"], r["problem"]) in FIXTURE_PROBLEMS]
    by_problem: dict = {}
    for r in fixture:
        by_problem.setdefault((r["exam_id"], r["problem"]), {"points": float(r["points"]), "topics": []})["topics"].append(r["topic_id"])
    items = [{"exam_id": e, "problem": p, "points": v["points"], "text": f"Find $\\omega_0$ in \\frac{{1}}{{2}} ({e} p{p})"}
             for (e, p), v in by_problem.items()]
    items_path = tmp_path / "items.json"
    items_path.write_text(json.dumps(items), encoding="utf-8")
    tags_path = tmp_path / "tags.csv"
    with open(tags_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["exam_id", "problem", "topic_ids"])
        for (e, p), v in by_problem.items():
            writer.writerow([e, p, ";".join(v["topics"])])
    return items_path, tags_path, {(r["exam_id"], r["problem"], r["topic_id"]): r for r in fixture}


def _run(main, argv, capsys) -> tuple[int, dict]:
    code = main(argv)
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1, lines
    return code, json.loads(lines[0])


def test_mock_tag_path_writes_valid_exam_items(fixture_ledger, tmp_path, capsys):
    items_path, tags_path, expected = _fixture_items(tmp_path)
    out = tmp_path / "exam_items.csv"
    code, result = _run(cognee_tag.main, ["--course", COURSE, "--items", str(items_path), "--mock-tags",
                                          str(tags_path), "--topics", str(TOPICS_CSV), "--out", str(out)], capsys)
    assert code == 0 and result["ok"] and result["mode"] == "mock" and result["dataset"] == "course-FX-101"
    assert result["tagged"] == 4 and result["untagged"] == [] and "warning" not in result
    rows = read_contract_csv(out, "exam_items").to_dict("records")            # exactly the contract columns and types
    assert result["row_count"] == len(rows) == len(expected) == 7
    shares: dict = {}
    for row in rows:
        want = expected[(row["exam_id"], row["problem"], row["topic_id"])]   # exam metadata from the ledger
        assert (row["exam_type"], row["term"], row["term_seq"], row["session"]) == \
            (want["exam_type"], want["term"], int(want["term_seq"]), int(want["session"]))
        assert row["points"] == pytest.approx(float(want["points"]))
        assert row["points_share"] == pytest.approx(float(want["points_share"]))
        assert row["tag_source"] == "cognee" and row["course"] == COURSE
        assert row["text_clean"].startswith("Find omega 0 in frac 1 2")
        key = (row["exam_id"], row["problem"])
        shares[key] = shares.get(key, 0.0) + row["points_share"]
    points = {(r["exam_id"], r["problem"]): r["points"] for r in rows}
    assert all(total == pytest.approx(points[key]) for key, total in shares.items())   # split, not duplicated
    assert shares[("FX.101-final-2021F", "2")] == pytest.approx(25.0)                   # 2 topics x 12.5

    # ledger topics instead of --topics; hand tags; a problem without tags is a degraded warning
    items = json.loads(items_path.read_text()) + [{"exam_id": "FX.101-final-2021F", "problem": "4", "points": 25,
                                                    "text": "no tag for this one"}]
    items_path.write_text(json.dumps(items))
    code, result = _run(cognee_tag.main, ["--course", COURSE, "--items", str(items_path), "--mock-tags",
                                          str(tags_path), "--tag-source", "human"], capsys)
    assert code == 0 and result["ok"] and result["untagged"] == ["FX.101-final-2021F-p4"] and "warning" in result
    assert {r["tag_source"] for r in result["rows"]} == {"human"} and len(result["rows"]) == 7


def test_tag_refusals_and_llm_gate(fixture_ledger, tmp_path, capsys, monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("Cognee must not be called")

    monkeypatch.setattr(cognee_tag, "_remember_items", must_not_run)
    monkeypatch.setattr(cognee_tag, "tag_live", must_not_run)
    items_path, tags_path, _ = _fixture_items(tmp_path)
    base = ["--course", COURSE, "--topics", str(TOPICS_CSV)]

    def items_file(items) -> str:
        path = tmp_path / f"items-{len(list(tmp_path.iterdir()))}.json"
        path.write_text(json.dumps(items))
        return str(path)

    sealed = items_file([{"exam_id": "FX.101-final-2022F", "problem": "1", "points": 20, "text": "sealed"}])
    out = tmp_path / "never.csv"
    code, result = _run(cognee_tag.main, base + ["--items", sealed, "--out", str(out)], capsys)     # live mode
    assert code == config.EXIT_SKIP_LIST and "SEALED" in result["error"] and not out.exists()
    code, _ = _run(cognee_tag.main, base + ["--items", sealed, "--mock-tags", str(tags_path)], capsys)
    assert code == config.EXIT_SKIP_LIST
    unknown = items_file([{"exam_id": "FX.101-midterm-2022F", "problem": "1", "points": 20, "text": "x"}])
    assert _run(cognee_tag.main, base + ["--items", unknown, "--mock-tags", str(tags_path)], capsys)[0] == config.EXIT_HARD
    no_topics = items_file([{"exam_id": "FX.102-final-2022F", "problem": "1", "points": 20, "text": "x"}])
    code, result = _run(cognee_tag.main, ["--course", "FX.102", "--items", no_topics, "--mock-tags", str(tags_path)],
                        capsys)                                        # ledger has no topics for FX.102
    assert code == config.EXIT_HARD and "topic list" in result["error"]
    bad = items_file([{"exam_id": "FX.101-quiz1-2020F", "problem": "1", "text": "no points"}])
    assert _run(cognee_tag.main, base + ["--items", bad, "--mock-tags", str(tags_path)], capsys)[0] == config.EXIT_VALIDATION
    off_list = tmp_path / "off.csv"
    off_list.write_text("exam_id,problem,topic_ids\nFX.101-quiz1-2020F,1,T09\n")
    assert _run(cognee_tag.main, base + ["--items", str(items_path), "--mock-tags", str(off_list)], capsys)[0] \
        == config.EXIT_VALIDATION
    for extra in ([], ["--dry-run"]):
        code, result = _run(cognee_tag.main, base + ["--items", str(items_path)] + extra, capsys)
        assert code == cognee_tag.EXIT_NO_LLM == 7 and "LLM_API_KEY" in result["error"] and not result["ok"]
    with pytest.raises(SystemExit) as exc:
        cognee_tag.main(base + ["--items", str(items_path), "--mock-tags", str(tags_path), "--dry-run"])
    assert exc.value.code == config.EXIT_HARD and json.loads(capsys.readouterr().out)["ok"] is False


def test_tag_cli_exit_code_without_llm_key(fixture_ledger, tmp_path):
    items_path, _, _ = _fixture_items(tmp_path)
    proc = subprocess.run([sys.executable, "-m", "oracle.cognee_tag", "--course", COURSE, "--items", str(items_path)],
                          cwd=REPO, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 7 and json.loads(proc.stdout)["ok"] is False


def test_remember_calls_match_cognee_signature(models, fixture_ledger):
    import cognee
    backend, _ = fixture_ledger
    calls = []

    async def remember(data, **kwargs):
        inspect.signature(cognee.remember).bind(data, **kwargs)
        calls.append((data, kwargs))
        return "estimate"

    items = cognee_tag.parse_items([{"exam_id": "FX.101-final-2021F", "problem": "2", "points": 25, "text": "t"}], COURSE)
    exams = cognee_tag.load_exams(COURSE, backend)
    topics = read_topics_csv(TOPICS_CSV, COURSE)
    result = asyncio.run(cognee_tag._remember_items(COURSE, items, exams, topics, dry_run=True,
                                                    cognee_module=SimpleNamespace(remember=remember)))
    data, kw = calls[0]
    assert result == ["estimate"] and data.startswith("problem_id: FX.101-final-2021F-p2\nexam_id: FX.101-final-2021F\n")
    assert kw["dataset_name"] == "course-FX-101" and kw["graph_model"] is models.ExamProblem
    assert kw["node_set"] == [COURSE, "2021F", "exam"] and kw["self_improvement"] is False and kw["dry_run"] is True
    assert kw["config"]["ontology_config"]["ontology_mode"] == "strict" and "T05: Fourier series" in kw["custom_prompt"]


# ------------------------------------------------------------------ Cognee feedback
def test_cognee_feedback_offline(fixture_ledger, capsys):
    assert [cognee_feedback.score_from_points(x) for x in (0, 0.5, 0.62, 0.75, 1, 1.7, -1)] == [1, 3, 3, 4, 5, 5, 1]
    with pytest.raises(ValueError):
        cognee_feedback.score_from_points(True)
    assert cognee_feedback.session_id_for_run("r3-FX.101-quiz1-2022F") == "run-3"
    with pytest.raises(ValueError):
        cognee_feedback.session_id_for_run("run-3")
    code, result = _run(cognee_feedback.main, ["--run-id", "r3-FX.101-quiz1-2022F"], capsys)
    assert code == 0 and result == {"ok": True, "run_id": "r3-FX.101-quiz1-2022F", "session_id": "run-3",
                                    "dataset": "course-FX-101", "score": 4, "skipped": "no LLM_API_KEY",
                                    "text": "Backtest r3-FX.101-quiz1-2022F: the top-K covered 75% of the exam's points."}
    code, result = _run(cognee_feedback.main, ["--run-id", "r9-FX.101-final-2022F"], capsys)
    assert code == 0 and result["ok"] and "not in the ledger" in result["warning"]
    code, result = _run(cognee_feedback.main, ["--session-id", "s1-plan-0003", "--dataset", "student-1",
                                               "--score", "2", "--text", "too wordy"], capsys)
    assert code == 0 and result["skipped"] == "no LLM_API_KEY" and result["score"] == 2
    with pytest.raises(SystemExit) as exc:
        cognee_feedback.main([])
    assert exc.value.code == config.EXIT_HARD and json.loads(capsys.readouterr().out)["ok"] is False
    with pytest.raises(ValueError):
        cognee_feedback.apply_feedback("run-3", dataset="course-FX.101", score=3)


def test_cognee_feedback_live_calls_with_fake(models, monkeypatch):
    import cognee
    monkeypatch.setenv("LLM_API_KEY", "offline-test-not-a-key")   # the fake module below is all that runs
    feedback, improved = [], []

    async def get_session(**kw):
        inspect.signature(cognee.session.get_session).bind(**kw)
        return [SimpleNamespace(qa_id="qa-1"), SimpleNamespace(qa_id="qa-2")]

    async def add_feedback(**kw):
        inspect.signature(cognee.session.add_feedback).bind(**kw)
        feedback.append(kw)
        return kw["qa_id"] == "qa-1"

    async def improve(**kw):
        inspect.signature(cognee.improve).bind(**kw)
        improved.append(kw)

    fake = SimpleNamespace(session=SimpleNamespace(get_session=get_session, add_feedback=add_feedback), improve=improve)
    result = asyncio.run(cognee_feedback.apply_feedback_async("run-3", dataset="course-FX-101", score=4, text="t",
                                                              cognee_module=fake))
    assert result == {"feedback_applied": 1, "qa_ids": ["qa-1"], "improved": True}
    assert [f["qa_id"] for f in feedback] == ["qa-1", "qa-2"] and feedback[0]["feedback_score"] == 4
    assert improved == [{"dataset": "course-FX-101", "session_ids": ["run-3"]}]


# ------------------------------------------------------------------ students
def test_student_prefs_round_trip_and_ledger_row(fixture_ledger, tmp_path):
    backend, handle = fixture_ledger
    store = students.LocalJSONStudentStore(tmp_path / "students")
    assert students.save_student(MAYA, store=store, backend=backend) == {
        "student_id": "s1", "memories": ["s1_pref_style", "s1_pref_length", "s1_pref_modality", "s1_pref_pace"],
        "ledger_rows": 1}
    read = store.read_prefs("s1")
    assert read["explicit"] == _prefs(MAYA) and read["inferred"] == []
    store.write_prefs("s1", {**_prefs(MAYA), "length": "detailed"})              # fixed ids: replaced, not added
    assert store.read_prefs("s1")["explicit"]["length"] == "detailed"
    assert len([m for m in store.load("s1")["memories"] if "_pref_" in m]) == 4
    assert store.read_prefs("s2") == {"student_id": "s2", "explicit": {}, "inferred": [], "system_prompt": ""}
    students.save_student(MAYA, store=store, backend=backend)                    # upsert: still one row
    frame = backend.query(handle, "SELECT * FROM {{students}} WHERE student_id = $sid", {"sid": "s1"})
    assert len(frame) == 1 and frame.loc[0, "exam_date"] == "2026-09-20" and frame.loc[0, "weekly_hours"] == 6
    for bad in ({**MAYA, "weekly_hours": 0.1}, {**MAYA, "style_order": "random"}, {**MAYA, "student_id": "x1"},
                {**MAYA, "exam_id": "OTHER-final-2022F"}, {**MAYA, "exam_date": "20 Sep"}):
        with pytest.raises(ValueError):
            students.validate_profile(bad)


def test_system_prompts_differ_for_maya_and_leo():
    read_example = _contract_examples()[3]
    maya = students.build_system_prompt(_prefs(MAYA), [])
    leo = students.build_system_prompt(_prefs(LEO), [])
    assert maya == read_example["system_prompt"]                       # student.md §4, before any feedback
    assert maya != leo and "worked example before any theory" in maya and "proof" in leo
    assert "under 120 words" in maya and "up to 400 words" in leo
    tuned = students.build_system_prompt(_prefs(MAYA), read_example["inferred"])
    assert tuned == maya + " Learned from recent feedback (takes priority over the settings above): prefers concise explanations."
    assert "too wordy" not in tuned                                    # raw feedback text never enters the prompt


def test_feedback_and_pref_shapes_match_contract(tmp_path):
    pref_example, feedback_example, event, read_example = _contract_examples()[:4]
    assert students.pref_memories("s1", read_example["explicit"])[0] == pref_example[0]
    assert students.feedback_memory(event, 3) == feedback_example[0]
    store = students.LocalJSONStudentStore(tmp_path)
    store.write_prefs("s1", read_example["explicit"])
    item = store.write_feedback(event)
    assert item == {**feedback_example[0], "id": "s1_fb_0001"}
    assert store.write_feedback(event) == item                         # a replayed event is not stored twice
    assert store.read_prefs("s1") == {**read_example, "system_prompt": students.build_system_prompt(
        read_example["explicit"], read_example["inferred"])}
    second = store.write_feedback({**event, "text": "that example helped", "score": 5,
                                   "created_at": "2026-09-11T15:09:00-07:00"})
    assert second["id"] == "s1_fb_0002"
    assert store.read_prefs("s1")["inferred"] == [read_example["inferred"][0],
                                                  "Prefers a worked example before the theory (from feedback 'that example helped')"]
    assert store.write_feedback({"student_id": "s1", "session_id": "s1-plan-0004", "score": 1})["text"] == "(rating only) 1/5"
    with pytest.raises(ValueError):
        students.validate_feedback({"student_id": "s1", "session_id": "s1-plan-0004"})
    assert students.infer_preferences(["too wordy", "more detail please", "no diagrams please", "too slow"]) == [
        "Prefers more detailed explanations (from feedback 'more detail please')",
        "Prefers verbal explanations (from feedback 'no diagrams please')",
        "Prefers a faster pace (from feedback 'too slow')"]


class FakeStudentContext:
    """Stands in for hydra_db ContextClient; each call is bound against the real method signature first."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def _bind(self, method: str, **kwargs) -> None:
        inspect.signature(getattr(ContextClient, method)).bind(self, **kwargs)
        self.calls.append((method, kwargs))

    def ingest(self, **kwargs):
        self._bind("ingest", **kwargs)
        for item in json.loads(kwargs["memories"]):
            self.store[(kwargs["collection"], item["id"])] = item
        return SimpleNamespace(success=True)

    def inspect(self, **kwargs):
        self._bind("inspect", **kwargs)
        item = self.store.get((kwargs["collection"], kwargs["id"]))
        if item is None:
            raise NotFoundError(body={"error": "not found"})
        return SimpleNamespace(data=SimpleNamespace(content=item["text"]))

    def list(self, **kwargs):
        self._bind("list", **kwargs)
        rows = [SimpleNamespace(memory_id=i, additional_metadata=item["additional_metadata"])
                for (c, i), item in self.store.items() if c == kwargs["collection"]]
        page, size = kwargs.get("page", 1), kwargs.get("page_size", 100)
        return SimpleNamespace(data=SimpleNamespace(user_memories=rows[(page - 1) * size: page * size], total=len(rows)))


class FakeHydra:
    """Stands in for hydra_db.HydraDB: `query` is bound against HydraDB.query; infer:true memories come back as
    an extracted preference, as HydraDB would serve them."""

    def __init__(self) -> None:
        self.context = FakeStudentContext()
        self.queries: list[dict] = []

    def query(self, **kwargs):
        inspect.signature(HydraDB.query).bind(self, **kwargs)
        self.queries.append(kwargs)
        chunks = [SimpleNamespace(chunk_content="Prefers concise explanations" if item["infer"] else item["text"],
                                  additional_metadata=item["additional_metadata"], id=i)
                  for (c, i), item in self.context.store.items() if c == kwargs["collection"]]
        return SimpleNamespace(data=SimpleNamespace(chunks=chunks))


def test_hydradb_student_store_with_fake_client(monkeypatch):
    event = _contract_examples()[2]
    fake = FakeHydra()
    store = students.HydraDBStudentStore(client=fake, database="exam-oracle")
    items = store.write_profile(MAYA)
    method, call = fake.context.calls[-1]
    assert method == "ingest" and (call["database"], call["collection"], call["type"], call["upsert"]) == \
        ("exam-oracle", "s1", "memory", "true")
    assert json.loads(call["memories"]) == items == students.pref_memories("s1", MAYA)
    assert store.write_feedback(event)["id"] == "s1_fb_0001"
    assert store.write_feedback({**event, "text": "still too long"})["id"] == "s1_fb_0002"
    store.write_profile(LEO)
    read = store.read_prefs("s1")
    assert read["explicit"] == _prefs(MAYA) and read["inferred"] == ["Prefers concise explanations"]
    assert read["system_prompt"].endswith("(takes priority over the settings above): prefers concise explanations.")
    assert store.read_prefs("s2")["inferred"] == [] and store.read_prefs("s2")["explicit"] == _prefs(LEO)
    assert fake.queries[0]["collection"] == "s1" and fake.queries[0]["type"] == "memory"
    reads_of_s1 = [kw for m, kw in fake.context.calls if m != "ingest" and kw.get("collection") == "s1"]
    assert reads_of_s1 and all(kw["database"] == "exam-oracle" for kw in reads_of_s1)

    monkeypatch.setenv("STUDENT_STORE", "hydradb")
    hydra = students.get_student_store()
    assert isinstance(hydra, students.HydraDBStudentStore) and hydra._client is None
    with pytest.raises(config.ConfigError):
        hydra.client                                                   # gated: HYDRADB_API_KEY is not set in tests
    monkeypatch.setenv("STUDENT_STORE", "local")
    assert isinstance(students.get_student_store(), students.LocalJSONStudentStore)


def test_students_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ORACLE_LOCAL_DIR", str(tmp_path / "local"))
    profile, event_path = tmp_path / "maya.json", tmp_path / "event.json"
    profile.write_text(json.dumps(MAYA))
    event_path.write_text(json.dumps(_contract_examples()[2]))
    code, result = _run(students.main, ["save", "--profile", str(profile)], capsys)
    assert code == 0 and result["ok"] and result["ledger_rows"] == 1
    code, result = _run(students.main, ["feedback", "--event", str(event_path)], capsys)
    assert code == 0 and result["memory"]["id"] == "s1_fb_0001" and result["cognee"] == {"skipped": "no LLM_API_KEY"}
    proc = subprocess.run([sys.executable, "-m", "oracle.students", "read", "--student-id", "s1"], cwd=REPO,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    read = json.loads(proc.stdout)
    assert read["explicit"] == _prefs(MAYA) and read["inferred"] == _contract_examples()[3]["inferred"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**MAYA, "pace": "warp"}))
    code, result = _run(students.main, ["save", "--profile", str(bad)], capsys)
    assert code == config.EXIT_HARD and "pace" in result["error"]
