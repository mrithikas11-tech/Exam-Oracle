"""Real course structures -> a temporary LOCAL ledger -> one run database per real target: visibility (D1/D2/D3).

The three real course folders (data/courses/6.641, 18.06, 6.003) are loaded through oracle.structure.load_course.
A's loader does not exist yet, so the content tables are stood in for by synthetic rows that carry the REAL times:
  * exam_items: one row per exam A may fetch (structure.exam_sources), with its exams row's term/term_seq/session;
  * homework_items + homework_vec: one row per loadable homework set, session = its DUE session (D2);
  * echo_pairs: every (homework, exam item) pair of a course;
  * POISON: an exam_items row (and its echo pairs) for the sealed 6.003 Final, as if a loader broke the rule
    "a sealed exam is an exams row only", so the test proves make_run_db still never lets it through.
Then, for every target of data/run_lists/real_chain.csv (and history_side_track.csv), oracle.make_run_db builds the
run database, and every table must hold EXACTLY the rows that an independent transcription of D1 (below, not
oracle.ordering) selects; sql/leakage.sql must report nothing. No answer key and no network is touched.
"""
from __future__ import annotations

import pytest

from oracle import config, make_run_db, run_list, structure
from oracle.backend import RUN_TABLES, DbHandle, get_backend
from oracle.leakage_check import check_leakage

COURSES = ("6.641", "18.06", "6.003")
RUN_LISTS = config.DATA_DIR / "run_lists"
CHAIN = run_list.read_run_list(RUN_LISTS / "real_chain.csv")
SIDE = run_list.read_run_list(RUN_LISTS / "history_side_track.csv")
ALL_RUNS = [*CHAIN, *SIDE]
SEALED = "6.003-final-2011F"
SEALED_TIME = (20113, 26)
# D2: published-term exams get calendar sessions (own row, or last delivered session + 1 for a shared row);
# other-term exams get synthetic ordinals that only order a term's exams
D2_SESSIONS = {
    "6.641-quiz1-2009S": 12, "6.641-final-2009S": 26,
    "18.06-quiz1-2010S": 12, "18.06-quiz2-2010S": 25, "18.06-quiz3-2010S": 37, "18.06-final-2010S": 40,
    "6.003-quiz1-2011F": 9, "6.003-quiz2-2011F": 14, "6.003-quiz3-2011F": 20, "6.003-final-2011F": 26,
    "6.641-final-2006S": 9000, "6.641-quiz1-2008S": 1000, "6.641-final-2008S": 9000,
    "6.003-quiz1-2010S": 1000, "6.003-quiz2-2010S": 2000, "6.003-final-2010S": 9000,
}
CONTENT = ("exam_items", "homework_items", "homework_vec", "echo_pairs")


def _d1(row_term_seq: int, row_session: int | None, t_term_seq: int, t_session: int | None) -> bool:
    """D1, transcribed here on purpose: visible iff an earlier term, or the same term and an earlier session;
    an unknown session is the end of its term."""
    end = float("inf")
    rs, ts = (end if row_session is None else row_session), (end if t_session is None else t_session)
    return row_term_seq < t_term_seq or (row_term_seq == t_term_seq and rs < ts)


def _i(value) -> int | None:
    return None if value is None or value != value else int(value)   # None / NaN / NA -> None


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setenv("ORACLE_LOCAL_DIR", str(tmp_path_factory.mktemp("real-visibility")))
    try:
        be = get_backend()
        ledger = be.ensure_ledger()
        structs = {c: structure.load_course(c) for c in COURSES}
        rows: dict[str, list[dict]] = {t: [] for t in ("courses", "topics", "lectures", "exams", "guidelines",
                                                        *CONTENT)}
        for c, s in structs.items():
            for name, tbl in s.tables.items():
                rows[name] += tbl.to_pylist()
            exams = {e["exam_id"]: e for e in s.tables["exams"].to_pylist()}
            fetched = list(s.exam_sources["exam_id"]) + [e for e in exams if exams[e]["sealed"]]   # + the POISON
            items = [{"course": c, "exam_id": x, "exam_type": exams[x]["exam_type"], "term": exams[x]["term"],
                      "term_seq": exams[x]["term_seq"], "session": exams[x]["session"], "problem": "1",
                      "points": 10.0, "topic_id": "T01", "points_share": 10.0, "tag_source": "human",
                      "text_clean": None} for x in fetched]
            hws = [{"course": c, "term": h.term, "term_seq": int(h.term_seq), "session": int(h.due_session),
                    "hw_id": f"{c}-{h.term}-ps{h.set}-p1", "topic_id": "T01", "text_clean": None}
                   for h in s.homework_sets.itertuples(index=False) if h.loadable]
            rows["exam_items"] += items
            rows["homework_items"] += hws
            rows["homework_vec"] += [{"course": c, "hw_id": h["hw_id"], "text_clean": None} for h in hws]
            rows["echo_pairs"] += [{"course": c, "hw_id": h["hw_id"], "hw_term_seq": h["term_seq"],
                                    "hw_session": h["session"], "exam_id": i["exam_id"], "problem": i["problem"],
                                    "exam_term_seq": i["term_seq"], "exam_session": i["session"], "sim": 1.0}
                                   for h in hws for i in items]
        for table, recs in rows.items():
            be.load_table(ledger, table, recs, mode="replace")
        runs = {}
        for r in ALL_RUNS:
            made = make_run_db.build_run_db(be, r.course, r.target_exam, r.run_id)
            handle = DbHandle.from_dict(made["run_db"])
            try:
                got = {t: be.query(handle, "SELECT * FROM {{%s}}" % t) for t in RUN_TABLES}
                leak = check_leakage(be, r.course, r.target_exam, r.run_id, run_db=handle)
            finally:
                be.drop_run_db(handle)
            runs[r.run_id] = {"row": r, "made": made, "tables": got, "leak": leak}
        yield {"structs": structs, "rows": rows, "runs": runs}
    finally:
        patch.undo()


def _target(structs, exam_id: str) -> dict[str, str]:
    """The target's exams.csv row, read straight from the structure file (not from the ledger)."""
    course = exam_id.rsplit("-", 2)[0]
    return next(e for e in structs[course].rows["exams.csv"] if e["exam_id"] == exam_id)


def _expected(real, exam_id: str) -> dict[str, set]:
    """Every row of each run table that D1 lets `exam_id` see, from the rows loaded into the ledger."""
    t = _target(real["structs"], exam_id)
    course, tts, tss = t["course"], int(t["term_seq"]), int(t["session"])
    rows = {k: [r for r in v if r["course"] == course] for k, v in real["rows"].items()}
    exams = {e["exam_id"] for e in rows["exams"]
             if not e["sealed"] and e["exam_id"] != exam_id and _d1(e["term_seq"], e["session"], tts, tss)}
    hws = {h["hw_id"] for h in rows["homework_items"] if _d1(h["term_seq"], h["session"], tts, tss)}
    return {
        "courses": {course}, "topics": {x["topic_id"] for x in rows["topics"]},
        "lectures": {(x["term"], x["session"]) for x in rows["lectures"] if _d1(x["term_seq"], x["session"], tts, tss)},
        "exams": exams,
        "exam_items": {x["exam_id"] for x in rows["exam_items"]
                       if x["exam_id"] in exams and _d1(x["term_seq"], x["session"], tts, tss)},
        "homework_items": hws, "homework_vec": hws,
        "echo_pairs": {(p["hw_id"], p["exam_id"]) for p in rows["echo_pairs"] if p["hw_id"] in hws and p["exam_id"] in exams},
        "guidelines": {g["guideline_id"] for g in rows["guidelines"]
                       if _d1(g["source_term_seq"], g["source_session"], tts, tss)},
    }


def _got(tables) -> dict[str, set]:
    f = tables
    return {
        "courses": set(f["courses"]["course"]), "topics": set(f["topics"]["topic_id"]),
        "lectures": {(str(x.term), int(x.session)) for x in f["lectures"].itertuples(index=False)},
        "exams": set(f["exams"]["exam_id"]), "exam_items": set(f["exam_items"]["exam_id"]),
        "homework_items": set(f["homework_items"]["hw_id"]), "homework_vec": set(f["homework_vec"]["hw_id"]),
        "echo_pairs": {(str(x.hw_id), str(x.exam_id)) for x in f["echo_pairs"].itertuples(index=False)},
        "guidelines": set(f["guidelines"]["guideline_id"]),
    }


RUN_IDS = [r.run_id for r in ALL_RUNS]


def test_the_run_lists_name_real_targets():
    assert len(CHAIN) == 12 and len(SIDE) == 6 and len(set(RUN_IDS)) == 18
    assert {r.target_exam for r in ALL_RUNS} == set(D2_SESSIONS)


# ------------------------------------------------------------------ D1: exactly the visible rows, nothing else
@pytest.mark.parametrize("run_id", RUN_IDS)
def test_run_db_holds_exactly_the_d1_visible_rows(real, run_id):
    run = real["runs"][run_id]
    exam, course = run["row"].target_exam, run["row"].course
    got, want = _got(run["tables"]), _expected(real, exam)
    for table in want:
        assert got[table] == want[table], (run_id, table, sorted(got[table] ^ want[table])[:5])
    for table, frame in run["tables"].items():           # no other course's row, ever
        assert set(frame["course"]) <= {course}, (run_id, table)
    assert run["leak"]["leakage_ok"] and run["leak"]["violations"] == [], run["leak"]["violations"]
    assert all(c["n"] == 0 for c in run["leak"]["checks"])


def test_twins_see_what_their_main_run_sees(real):
    for twin in (r for r in CHAIN if r.cold_start):
        main = next(r for r in CHAIN if r.target_exam == twin.target_exam and not r.cold_start)
        assert _got(real["runs"][twin.run_id]["tables"]) == _got(real["runs"][main.run_id]["tables"]), twin.run_id


# ------------------------------------------------------------------ the sealed reveal
def test_the_sealed_6003_final_is_never_visible(real):
    assert any(x["exam_id"] == SEALED for x in real["rows"]["exam_items"])     # the poison really is in the ledger
    assert any(p["exam_id"] == SEALED for p in real["rows"]["echo_pairs"])
    for run_id, run in real["runs"].items():
        t = _target(real["structs"], run["row"].target_exam)
        assert not _d1(*SEALED_TIME, int(t["term_seq"]), int(t["session"])), run_id   # no real target is later
        tables = run["tables"]
        assert SEALED not in set(tables["exams"]["exam_id"]), run_id
        assert SEALED not in set(tables["exam_items"]["exam_id"]), run_id
        assert SEALED not in set(tables["echo_pairs"]["exam_id"]), run_id
        if run["row"].course == "6.003":
            assert run["made"]["excluded_sealed_exams"] == [SEALED], run_id
    reveal = [r for r in CHAIN if r.target_exam == SEALED]
    assert [r.cold_start for r in reveal] == [False, True]                       # main run + cold-start twin
    assert all(real["runs"][r.run_id]["made"]["target"]["sealed"] for r in reveal)


# ------------------------------------------------------------------ D2: sessions, shared rows, homework by due date
def test_d2_target_sessions(real):
    for run_id, run in real["runs"].items():
        exam = run["row"].target_exam
        assert run["made"]["target"]["session"] == D2_SESSIONS[exam], run_id
        assert int(_target(real["structs"], exam)["session"]) == D2_SESSIONS[exam], run_id


def test_d2_shared_row_exams_see_the_lecture_before_but_not_the_one_after(real):
    checked = 0
    for run_id, run in real["runs"].items():
        t = _target(real["structs"], run["row"].target_exam)
        session, term = int(t["session"]), t["term"]
        lectures = {s for tm, s in _got(run["tables"])["lectures"] if tm == term}
        if t["session_source"] == "calendar_shared_row":
            assert session - 1 in lectures and not {s for s in lectures if s >= session}, run_id
            checked += 1
        elif t["session_source"] == "calendar_own_row":        # 18.06: the exam's own calendar row, no lecture on it
            sessions = {int(r["session"]) for r in real["structs"][t["course"]].rows["sessions.csv"]}
            assert session not in sessions and max(lectures) < session, run_id
            checked += 1
        else:
            assert t["session_source"] == "synthetic" and not lectures, run_id   # other terms see no calendar
    assert checked == 12


def test_d2_homework_is_visible_by_due_session(real):
    def sets(run_id: str) -> set[str]:
        return {h.rsplit("-ps", 1)[1][:-3] for h in _got(real["runs"][run_id]["tables"])["homework_items"]}

    by_exam = {r.target_exam: r.run_id for r in CHAIN if not r.cold_start}
    assert sets(by_exam["6.641-quiz1-2009S"]) == {"1", "2", "3", "4", "5"}        # PS6 (due 18), PS7 (21) hidden
    assert sets(by_exam["6.641-final-2009S"]) == {"1", "2", "3", "4", "5", "6", "7"}
    assert sets(by_exam["6.003-quiz1-2011F"]) == {"1", "2", "3", "4"}             # set 5 is due AT session 9
    assert not any("psopt" in h["hw_id"] for h in real["rows"]["homework_items"])  # 6.641 'opt' has no due session
    for run_id, run in real["runs"].items():
        t = _target(real["structs"], run["row"].target_exam)
        due = {(h.term, h.set): h.due_session for h in real["structs"][t["course"]].homework_sets.itertuples()}
        for hw in _got(run["tables"])["homework_items"]:
            term, set_ = hw.split("-")[-3], hw.rsplit("-ps", 1)[1][:-3]     # <course>-<term>-ps<set>-p1
            assert term == t["term"] and _i(due[(term, set_)]) < int(t["session"]), (run_id, hw)


def test_d2_lecture_anchors(real):
    by_exam = {r.target_exam: r.run_id for r in CHAIN if not r.cold_start}

    def sessions(exam: str) -> list[int]:
        return sorted(s for _, s in _got(real["runs"][by_exam[exam]]["tables"])["lectures"])

    assert sessions("6.641-quiz1-2009S") == list(range(1, 12))
    assert sessions("6.641-final-2009S") == list(range(1, 26))
    assert sessions("6.003-quiz1-2011F") == list(range(1, 9))
    assert sessions("6.003-final-2011F") == list(range(1, 26))
    assert 24 not in sessions("18.06-final-2010S") and 36 not in sessions("18.06-final-2010S")   # review rows


# ------------------------------------------------------------------ D3: feature sets
def test_d3_full_targets_are_in_the_published_term(real):
    for r in CHAIN:
        run, meta = real["runs"][r.run_id], real["structs"][r.course].meta
        t = _target(real["structs"], r.target_exam)
        assert r.feature_set == run["made"]["feature_set"] == "full", r.run_id
        assert t["term"] == meta["published_term"] and int(t["term_seq"]) == meta["published_term_seq"], r.run_id
        assert len(run["tables"]["lectures"]) > 0, r.run_id


def test_d3_history_targets_see_nothing_from_the_published_term(real):
    for r in SIDE:
        run, meta = real["runs"][r.run_id], real["structs"][r.course].meta
        t = _target(real["structs"], r.target_exam)
        assert r.feature_set == run["made"]["feature_set"] == "exam_history" and not r.cold_start, r.run_id
        assert int(t["term_seq"]) < meta["published_term_seq"], r.run_id
        published = meta["published_term_seq"]
        tables = run["tables"]
        assert tables["lectures"].empty and tables["homework_items"].empty and tables["guidelines"].empty, r.run_id
        for table, col in (("exams", "term_seq"), ("exam_items", "term_seq")):
            assert (tables[table][col] < published).all(), (r.run_id, table)
        assert len(tables["exams"]) > 0, r.run_id                           # x1 still has earlier exams to use
