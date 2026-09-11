"""Course-structure contract (contracts/course-structure.md): the three real course folders pass
oracle.structure's check and map into ledger-shaped tables; broken copies fail with exit 3; the D8 run lists use
exam_ids exactly as in exams.csv and validate (alone and together) under run_list's forward-in-time rules."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from oracle import config, run_list, structure
from oracle.backend import columns

REPO_ROOT = Path(__file__).resolve().parents[1]
COURSES = ("6.003", "18.06", "6.641")
RUN_LISTS = config.DATA_DIR / "run_lists"
CHAIN = RUN_LISTS / "real_chain.csv"
SIDE = RUN_LISTS / "history_side_track.csv"
LEDGER_TABLES = ("courses", "topics", "lectures", "exams", "guidelines")


@pytest.fixture(scope="module")
def loaded() -> dict[str, structure.CourseStructure]:
    return {c: structure.load_course(c) for c in COURSES}


def _sessions(s: structure.CourseStructure) -> dict[str, int]:
    return {r["exam_id"]: r["session"] for r in s.tables["exams"].to_pylist()}


# ------------------------------------------------------------------ the real folders
@pytest.mark.parametrize("course", COURSES)
def test_real_course_passes_check(course):
    s = structure.check_course(course)
    assert s.problems == [], s.problems


def test_ledger_frames_have_contract_columns(loaded):
    for course, s in loaded.items():
        frames = s.frames
        for table in LEDGER_TABLES:
            assert list(frames[table].columns) == [c.name for c in columns(table)], (course, table)
            assert set(frames[table]["course"]) == {course}


def test_lectures_skip_review_rows_and_exams_skip_excluded(loaded):
    assert {c: s.tables["lectures"].num_rows for c, s in loaded.items()} == {"6.003": 25, "18.06": 34, "6.641": 25}
    lectures_1806 = {r["session"] for r in loaded["18.06"].tables["lectures"].to_pylist()}
    assert not {24, 36} & lectures_1806
    assert "6.003-quiz3-2010S" not in _sessions(loaded["6.003"])      # T5-CURATION: role excluded
    assert "6.641-final-1995S" not in _sessions(loaded["6.641"])      # A-PACKET: role excluded


def test_sealed_reveal_is_an_exams_row_but_never_a_source(loaded):
    s = loaded["6.003"]
    sealed = [r for r in s.tables["exams"].to_pylist() if r["sealed"]]
    assert [r["exam_id"] for r in sealed] == ["6.003-final-2011F"]
    assert "6.003-final-2011F" not in set(s.exam_sources["exam_id"])
    assert len(s.skip_list) == 10
    assert loaded["18.06"].skip_list == [] and loaded["6.641"].skip_list == []


def test_d2_exam_sessions(loaded):
    got = {**_sessions(loaded["6.003"]), **_sessions(loaded["6.641"]), **_sessions(loaded["18.06"])}
    want = {"6.003-quiz1-2011F": 9, "6.003-quiz2-2011F": 14, "6.003-quiz3-2011F": 20, "6.003-final-2011F": 26,
            "6.641-quiz1-2009S": 12, "6.641-final-2009S": 26,
            "18.06-quiz1-2010S": 12, "18.06-quiz2-2010S": 25, "18.06-quiz3-2010S": 37, "18.06-final-2010S": 40,
            "6.003-quiz1-2009F": 1000, "6.003-final-2010S": 9000, "6.641-quiz2-2006S": 2000,
            "18.06-quiz3-2009F": 3000}
    assert {k: got[k] for k in want} == want


def test_homework_due_session_lookup(loaded):
    s = loaded["6.003"]
    assert s.homework_due_session("2011F", "4") == 8
    assert s.homework_due_session("2011F", 5) == 9
    visible_to_quiz1 = [r.set for r in s.homework_sets.itertuples() if r.due_session < 9]
    assert visible_to_quiz1 == ["1", "2", "3", "4"]
    assert loaded["6.641"].homework_due_session("2009S", "opt") is None
    assert not loaded["6.641"].homework_sets.set_index("set").loc["opt", "loadable"]
    assert loaded["18.06"].homework_due_session("2010S", "10") == 34
    with pytest.raises(KeyError):
        s.homework_due_session("2010S", "1")


def test_topics_keep_t00_and_session_bounds(loaded):
    for s in loaded.values():
        t00 = [r for r in s.tables["topics"].to_pylist() if r["topic_id"] == "T00"]
        assert len(t00) == 1 and t00[0]["first_lecture"] is None and t00[0]["last_lecture"] is None
    t10 = {r["topic_id"]: r for r in loaded["6.641"].tables["topics"].to_pylist()}["T10"]
    assert (t10["first_lecture"], t10["last_lecture"]) == (12, 13)      # SESSION numbers (LOADER-TOPICS), not L11


def test_non_ocw_sources_only_in_the_documented_exception(loaded):
    assert loaded["6.003"].non_ocw_urls == [] and loaded["6.641"].non_ocw_urls == []
    urls = loaded["18.06"].non_ocw_urls
    assert urls and all(u.startswith("https://web.mit.edu/18.06/www/") for u in urls)
    assert not any(config.is_ocw_url(u) for u in urls)       # the fetch allowlist is NOT extended by the check


def test_cli_prints_one_json_and_exits_0():
    proc = subprocess.run([sys.executable, "-m", "oracle.structure", "--course", "18.06", "--check"],
                          cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(proc.stdout)
    assert out["ok"] is True and out["counts"]["exams"] == 31 and out["problems"] == []


# ------------------------------------------------------------------ broken copies fail (exit 3)
def _copy(tmp_path: Path) -> Path:
    shutil.copytree(config.DATA_DIR / "courses" / "6.003", tmp_path / "6.003")
    return tmp_path


def _edit(tmp_path: Path, name: str, old: str, new: str) -> Path:
    base = _copy(tmp_path)
    path = base / "6.003" / name
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return base


BREAKS = {
    "partition": ("sessions.csv", ",Z Transform,T05,", ",Z Transform,T99,", "partition"),
    "exam id": ("exams.csv", "6.003-quiz1-2009F,quiz1,", "6.003-quiz1-2009F,quiz2,", "exam_id must be"),
    "term_seq": ("sessions.csv", "6.003,2011F,20113,1,", "6.003,2011F,20111,1,", "term_seq"),
    "sealed flag": ("exams.csv", ",true,,,false,sealed_reveal", ",false,,,false,sealed_reveal", "sealed = true"),
    "sealed url": ("exams.csv", ",true,,,false,sealed_reveal", ",true,https://ocw.mit.edu/x.pdf,,false,sealed_reveal",
                   "a sealed exam carries no"),
    "skip-listed url": ("guidelines.csv", "abaccd6013647c3dcacc697edfab18b0_MIT6_003F11_q1.pdf\n",
                        "662817e41af7452eb9911dca78f3f231_MIT6_003F11_final.pdf\n", "skip_list.txt"),
    "d2 session": ("exams.csv", ",2011F,20113,14,calendar_shared_row,", ",2011F,20113,30,calendar_shared_row,",
                   "last delivered session"),
    "position": ("exams.csv", ",2011F,20113,9,calendar_shared_row,", ",2011F,20113,15,calendar_shared_row,",
                 "A1"),
    "extra column": ("exams.csv", ",url_verified,role\n", ",url_verified,role,problem_text\n", "header must be"),
    "markup": ("exams.csv", "Quiz #1 (cover); Quiz 1 (Exams page),2009F", "Quiz #1 \\frac{1}{2},2009F", "markup"),
    "non-ocw url": ("homework_sets.csv", "https://ocw.mit.edu/courses/6-003-signals-and-systems-fall-2011/f42f53c3",
                    "https://web.mit.edu/courses/6-003-signals-and-systems-fall-2011/f42f53c3", "not https://ocw"),
}


@pytest.mark.parametrize("case", sorted(BREAKS))
def test_broken_copy_fails(tmp_path, case):
    name, old, new, expect = BREAKS[case]
    base = _edit(tmp_path, name, old, new)
    s = structure.check_course("6.003", base)
    assert s.problems and any(expect in p for p in s.problems), s.problems
    assert s.tables == {}
    with pytest.raises(structure.StructureError):
        structure.load_course("6.003", base)


def test_forbidden_json_key_and_empty_skip_list_fail(tmp_path):
    base = _copy(tmp_path)
    folder = base / "6.003"
    meta = json.loads((folder / "course.json").read_text(encoding="utf-8"))
    meta["packet_sections"] = {"url": meta["ocw_url"], "uncertainties": "the Final's problem 3 is on sampling"}
    (folder / "course.json").write_text(json.dumps(meta), encoding="utf-8")
    (folder / "skip_list.txt").write_text("", encoding="utf-8")
    problems = structure.check_course("6.003", base).problems
    assert any("'uncertainties' is forbidden" in p for p in problems)
    assert any("skip_list.txt must list" in p for p in problems)


def test_cli_exit_3_on_a_broken_copy(tmp_path, capsys):
    base = _edit(tmp_path, *BREAKS["partition"][:3])
    code = structure.main(["--course", "6.003", "--check", "--courses-dir", str(base)])
    out = json.loads(capsys.readouterr().out)
    assert code == config.EXIT_VALIDATION == 3
    assert out["ok"] is False and out["problems"]
    assert structure.main(["--course", "9.99", "--check", "--courses-dir", str(base)]) == 3


# ------------------------------------------------------------------ D8 run lists
def _exam_times(loaded) -> tuple[dict[str, run_list.ExamTime], dict[str, str]]:
    times, roles = {}, {}
    for course, s in loaded.items():
        published = s.meta["published_term_seq"]
        for r in s.tables["exams"].to_pylist():
            times[r["exam_id"]] = run_list.ExamTime(course, r["term_seq"], r["session"], published)
        roles.update({r["exam_id"]: r["role"] for r in s.rows["exams.csv"]})
    return times, roles


def _as_ledger(rows) -> list[run_list.LedgerRun]:
    return [run_list.LedgerRun(r.run_id, r.run_seq, r.course, r.target_exam, r.cold_start) for r in rows]


def test_real_chain_is_d8(loaded):
    rows = run_list.read_run_list(CHAIN)
    assert [(r.target_exam, r.cold_start) for r in rows] == [
        ("6.641-quiz1-2009S", False), ("6.641-final-2009S", False),
        ("18.06-quiz1-2010S", False), ("18.06-quiz2-2010S", False), ("18.06-quiz3-2010S", False),
        ("18.06-final-2010S", False),
        ("6.003-quiz1-2011F", False), ("6.003-quiz1-2011F", True), ("6.003-quiz2-2011F", False),
        ("6.003-quiz3-2011F", False), ("6.003-final-2011F", False), ("6.003-final-2011F", True)]
    times, roles = _exam_times(loaded)
    assert all(r.feature_set == "full" for r in rows)
    assert {roles[r.target_exam] for r in rows} == {"target_full", "sealed_reveal"}
    assert run_list.validate(rows, times) == []
    header = CHAIN.read_text(encoding="utf-8").splitlines()
    assert [ln for ln in header if not ln.startswith("#")][0] == \
        "run_seq,course,target_exam,feature_set,cold_start,notes"


def test_history_side_track_validates_alone_and_with_the_chain(loaded):
    chain, side = run_list.read_run_list(CHAIN), run_list.read_run_list(SIDE)
    assert [r.target_exam for r in side] == ["6.641-final-2006S", "6.641-quiz1-2008S", "6.641-final-2008S",
                                             "6.003-quiz1-2010S", "6.003-quiz2-2010S", "6.003-final-2010S"]
    times, roles = _exam_times(loaded)
    assert all(r.feature_set == "exam_history" and not r.cold_start for r in side)
    assert all(roles[r.target_exam] in ("target_history", "x1_input") for r in side)
    assert not {r.run_seq for r in side} & {r.run_seq for r in chain}
    assert run_list.validate(side, times) == []
    assert run_list.validate(side, times, _as_ledger(chain)) == []    # side track after the chain is in the ledger
    assert run_list.validate(chain, times, _as_ledger(side)) == []    # or before it


def test_real_template_is_marked_superseded():
    assert CHAIN.name in (RUN_LISTS / "REAL_TEMPLATE.csv").read_text(encoding="utf-8").splitlines()[0]
