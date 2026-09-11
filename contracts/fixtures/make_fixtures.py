"""Generate SYNTHETIC fixtures for Exam Oracle (course FX.101 — not real data).

Stdlib only, deterministic. Run: python contracts/fixtures/make_fixtures.py
Writes CSVs (one per ledger table), a fake sealed answer key, a fake prediction
and fake runs, so A/B/C can build before real data exists.

Fixture story: FX.101 has 8 topics. Past terms 2020F and 2021F each had a Quiz 1
(session 8) and a Final (session 20). The published term is 2022F: 19 lectures,
problem sets due at sessions 3,6,9,12,15,18, Quiz 1 at session 8, Final at
session 20 (the fixture's sealed target). Stated guidance: the final is
cumulative; the final emphasizes sessions 13-19 (half the points).
"""
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SEALED = os.path.join(HERE, "sealed")
C = "FX.101"

TOPICS = [
    ("T01", "Complex numbers", 1, 2),
    ("T02", "Difference equations", 3, 5),
    ("T03", "Z transform", 6, 7),
    ("T04", "Convolution", 9, 10),
    ("T05", "Fourier series", 11, 12),
    ("T06", "Fourier transform", 13, 15),
    ("T07", "Sampling", 16, 17),
    ("T08", "Modulation", 18, 19),
]


def topic_of_lecture(n):
    for tid, _, a, b in TOPICS:
        if a <= n <= b:
            return tid
    return None


def w(name, header, rows):
    with open(os.path.join(HERE, name), "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(header)
        wr.writerows(rows)


def main():
    os.makedirs(SEALED, exist_ok=True)
    w("courses.csv", ["course", "title", "ocw_url", "published_term", "published_term_seq"],
      [[C, "Synthetic Signals (FIXTURE)", "", "2022F", 20223]])
    w("topics.csv", ["course", "topic_id", "topic", "first_lecture", "last_lecture"],
      [[C, t, n, a, b] for t, n, a, b in TOPICS])

    # Lectures: published term only (as on OCW). Session 8 is Quiz 1, so lectures skip it.
    lec_rows, n = [], 0
    for session in range(1, 21):
        if session in (8, 20):
            continue
        n += 1
        lec_rows.append([C, "2022F", 20223, session, "", n, f"Lecture {n}", topic_of_lecture(n)])
    w("lectures.csv", ["course", "term", "term_seq", "session", "date", "lecture_n", "title", "topic_id"], lec_rows)

    exams = [
        # exam_id, type, term, term_seq, session, total, cov_from, cov_to, cumulative, sealed
        (f"{C}-quiz1-2020F", "quiz1", "2020F", 20203, 8, 30, 1, 7, False, False),
        (f"{C}-final-2020F", "final", "2020F", 20203, 20, 100, 1, 19, True, False),
        (f"{C}-quiz1-2021F", "quiz1", "2021F", 20213, 8, 30, 1, 7, False, False),
        (f"{C}-final-2021F", "final", "2021F", 20213, 20, 100, 1, 19, True, False),
        (f"{C}-quiz1-2022F", "quiz1", "2022F", 20223, 8, 30, 1, 7, False, False),
        (f"{C}-final-2022F", "final", "2022F", 20223, 20, 100, 1, 19, True, True),
    ]
    w("exams.csv", ["course", "exam_id", "exam_type", "term", "term_seq", "session", "date", "total_points",
                    "coverage_from_session", "coverage_to_session", "cumulative", "sealed"],
      [[C, e, t, tm, ts, s, "", tot, a, b, str(cu).lower(), str(se).lower()] for e, t, tm, ts, s, tot, a, b, cu, se in exams])

    # Tagged items for NON-sealed exams: (exam_id, problem, points, [topics])
    items = [
        (f"{C}-quiz1-2020F", "1", 10, ["T01"]), (f"{C}-quiz1-2020F", "2", 10, ["T02"]), (f"{C}-quiz1-2020F", "3", 10, ["T03"]),
        (f"{C}-final-2020F", "1", 20, ["T04"]), (f"{C}-final-2020F", "2", 20, ["T05", "T06"]), (f"{C}-final-2020F", "3", 20, ["T06"]),
        (f"{C}-final-2020F", "4", 20, ["T07"]), (f"{C}-final-2020F", "5", 20, ["T02"]),
        (f"{C}-quiz1-2021F", "1", 15, ["T02"]), (f"{C}-quiz1-2021F", "2", 15, ["T03"]),
        (f"{C}-final-2021F", "1", 25, ["T06"]), (f"{C}-final-2021F", "2", 25, ["T07", "T08"]), (f"{C}-final-2021F", "3", 25, ["T04"]),
        (f"{C}-final-2021F", "4", 25, ["T05"]),
        (f"{C}-quiz1-2022F", "1", 15, ["T02"]), (f"{C}-quiz1-2022F", "2", 15, ["T03", "T01"]),
    ]
    meta = {e: (t, tm, ts, s) for e, t, tm, ts, s, *_ in exams}
    rows = []
    for e, p, pts, tops in items:
        t, tm, ts, s = meta[e]
        for tid in tops:
            rows.append([C, e, t, tm, ts, s, p, pts, tid, round(pts / len(tops), 6), "human", f"synthetic problem {p} on {tid}"])
    w("exam_items.csv", ["course", "exam_id", "exam_type", "term", "term_seq", "session", "problem", "points", "topic_id",
                         "points_share", "tag_source", "text_clean"], rows)

    # Homework: published term only; set k due at session 3k.
    hw = []
    pset_topics = {1: ["T01", "T02"], 2: ["T02", "T03"], 3: ["T03", "T04"], 4: ["T04", "T05"], 5: ["T06", "T06"], 6: ["T07", "T08"]}
    for k, tops in pset_topics.items():
        for i, tid in enumerate(tops, 1):
            hw.append([C, "2022F", 20223, 3 * k, f"{C}-2022F-ps{k}-p{i}", tid, f"synthetic homework {k}.{i} on {tid}"])
    w("homework_items.csv", ["course", "term", "term_seq", "session", "hw_id", "topic_id", "text_clean"], hw)
    w("homework_vec.csv", ["course", "hw_id", "text_clean"], [[r[0], r[4], r[6]] for r in hw])

    w("echo_pairs.csv", ["course", "hw_id", "hw_term_seq", "hw_session", "exam_id", "problem", "exam_term_seq", "exam_session", "sim"], [
        [C, f"{C}-2022F-ps5-p1", 20223, 15, f"{C}-final-2021F", "1", 20213, 20, 0.031],
        [C, f"{C}-2022F-ps5-p2", 20223, 15, f"{C}-final-2020F", "3", 20203, 20, 0.029],
        [C, f"{C}-2022F-ps4-p2", 20223, 12, f"{C}-final-2021F", "4", 20213, 20, 0.027],
        [C, f"{C}-2022F-ps6-p1", 20223, 18, f"{C}-final-2020F", "4", 20203, 20, 0.012],
    ])

    w("guidelines.csv", ["course", "guideline_id", "kind", "applies_to_exam_type", "from_session", "to_session", "share",
                         "source_term", "source_term_seq", "source_session", "text", "source_url"], [
        [C, "G1", "cumulative", "final", "", "", "", "2022F", 20223, 1, "The final exam is cumulative. (FIXTURE)", ""],
        [C, "G2", "emphasis_window", "final", 13, 19, 0.5, "2022F", 20223, 1, "The final emphasizes material after Quiz 1, especially the last third. (FIXTURE)", ""],
    ])

    w("students.csv", ["student_id", "course", "exam_id", "exam_date", "weekly_hours"], [
        ["s1", C, f"{C}-final-2022F", "2026-09-20", 6],
        ["s2", C, f"{C}-final-2022F", "2026-09-27", 12],
    ])

    # Sealed answer key for the fixture target (human-labeled shape). Lives in the sealed folder.
    with open(os.path.join(SEALED, f"answer_key_{C}-final-2022F.csv"), "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["exam_id", "problem", "sub", "points", "topic_ids", "labeled_by", "labeled_at"])
        for p, pts, tops in [("1", 20, "T06"), ("2", 20, "T07"), ("3", 20, "T05;T04"), ("4", 20, "T08"), ("5", 20, "T02")]:
            wr.writerow([f"{C}-final-2022F", p, "", pts, tops, "fixture", "2026-09-11T09:00:00-07:00"])

    # Answer keys for every other fixture exam (fixture tags are human-labeled),
    # so end-to-end backtests on the fixture can be scored.
    by_exam = {}
    for e, p, pts, tops in items:
        by_exam.setdefault(e, []).append((p, pts, tops))
    for e, probs in by_exam.items():
        with open(os.path.join(SEALED, f"answer_key_{e}.csv"), "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["exam_id", "problem", "sub", "points", "topic_ids", "labeled_by", "labeled_at"])
            for p, pts, tops in probs:
                wr.writerow([e, p, "", pts, ";".join(tops), "fixture", "2026-09-11T09:00:00-07:00"])

    # Fake prediction + runs so C can render before B's first real run.
    weights = {"intercept": -1.0, "x1": 0.5, "x2": 0.5, "x3": 0.5, "x4": 0.5, "x5": 0.5, "x6": 0.0, "x7": 0.5}
    fake_topics = []
    for rank, (tid, name, *_ ) in enumerate(TOPICS, 1):
        fake_topics.append({"topic_id": tid, "topic": name, "p": round(0.9 - 0.08 * rank, 4), "rank": rank,
                            "in_top_k": rank <= 4, "signals": {f"x{i}": 0.0 for i in range(1, 8)}})
    pred = {"schema_version": 1, "standardization": "zscore_per_run_v1",  # D5
            "run_id": f"r0-{C}-final-2022F", "course": C, "target_exam": f"{C}-final-2022F",
            "feature_set": "full", "cold_start": True, "made_at": "2026-09-11T09:00:00-07:00", "lessons_run_seq": None,
            "k": 4, "weights": weights, "topics": fake_topics,
            "baselines": {"even": ["T06", "T02", "T04", "T05"], "last_exam": ["T06", "T07", "T08", "T04"]}}
    with open(os.path.join(HERE, "prediction.json"), "w") as f:
        json.dump(pred, f, indent=2, sort_keys=True)

    w("runs.csv", ["run_id", "run_seq", "course", "target_exam", "feature_set", "cold_start", "k", "model_pts", "even_pts",
                   "lastexam_pts", "recall_k", "brier", "tokens", "seconds", "checks_first_try", "leakage_ok",
                   "key_source", "lessons_run_seq", "hash", "made_at"], [
        ["r1-FX.101-quiz1-2021F", 1, C, f"{C}-quiz1-2021F", "exam_history", "false", 2, 0.50, 0.25, 0.50, 0.5, 0.22, 0, 3.1, "true", "true", "human", "", "FAKEHASH1", "2026-09-11T09:10:00-07:00"],
        ["r2-FX.101-final-2021F", 2, C, f"{C}-final-2021F", "exam_history", "false", 4, 0.62, 0.50, 0.55, 0.6, 0.20, 0, 3.4, "true", "true", "human", 1, "FAKEHASH2", "2026-09-11T09:20:00-07:00"],
        ["r3-FX.101-quiz1-2022F", 3, C, f"{C}-quiz1-2022F", "full", "false", 2, 0.75, 0.40, 0.50, 1.0, 0.15, 0, 2.9, "true", "true", "human", 2, "FAKEHASH3", "2026-09-11T09:30:00-07:00"],
    ])
    print("fixtures written to", HERE)


if __name__ == "__main__":
    main()
