# Contracts — the shapes A, B and C build against

Owner: **Person B**. Change protocol: if you change a contract, update its fixture in the **same commit** and add one line to `kit/07-reference/decisions-log.md`. A silent schema change breaks all three people.

| File | Seam | Producer → Consumer |
|---|---|---|
| `ledger-schema.sql` | hotdata ledger tables | A loads (and B's scripts load `predictions`, `runs`, `lessons`, `guideline_tests`) → B's signal queries, C's dashboard |
| `prediction.schema.json` | the sealed prediction | B's `rank_and_seal.py` → C's dashboard + reveal |
| `student.md` | student profile, preferences, feedback, plan | C's app writes/reads ↔ B's Cognee/HydraDB stores |
| `fixtures/` | small **synthetic** data in every shape | everyone, until real data lands (M1) |

## The time-ordering rule (applies to every table)

OCW calendars often give session numbers instead of calendar dates, and OCW publishes lecture notes and problem sets for only **one** term per course. So time is ordered by two integers, not by dates:

- `term_seq` = `year * 10 + season` with season `1` = spring, `2` = summer, `3` = fall. Example: Spring 2009 → `20091`, Fall 2011 → `20113`.
- `session` = the lecture/session number within that term. An exam carries the session at which it was given; a problem set carries the session at which it was due.
- `date` is kept when the source states one, but it is **informational**; leakage decisions use `(term_seq, session)`.

A row is **visible** to a prediction for target exam `T` if and only if:

```
row.term_seq < T.term_seq
OR (row.term_seq = T.term_seq AND row.session < T.session)
```

Consequences (deliberate):
- A target in the course's **published term** gets all seven signals, from that term's sessions before the exam (`feature_set = "full"`).
- A target in an **earlier term** cannot see the published term's lectures or problem sets (they are in its future), so x2–x6 are unavailable; only x1 (earlier exams) and x7 (guidance stated before it) apply (`feature_set = "exam_history"`).
- The fixed **topic list** is course structure, not exam content, and is shared by all terms of a course. This is an explicit assumption — state it on stage if asked.

## Loading and querying (verified against the installed SDKs, 2026-09-11)

- **hotdata** (`hotdata-framework` 0.14.0, env `HOTDATA_API_KEY`, `HOTDATA_WORKSPACE`, `HOTDATA_API_URL`): managed-table loads **require Parquet**; idempotency is key columns + `mode="upsert"` (keys declared at `create_managed_database(tables=..., keys=..., expires_at=...)`); SQL inside a managed database names tables `"default"."public"."<table>"`. Table keys: `exam_items(exam_id, problem, topic_id)`, `homework_items(hw_id, topic_id)`, `lectures(course, term, session)`, `exams(exam_id)`, `echo_pairs(hw_id, exam_id, problem)`, `guidelines(guideline_id)`, `predictions(run_id, topic_id)`, `runs(run_id)`, `lessons(run_seq, signal)`, `guideline_tests(guideline_id, run_id)`, `students(student_id)`, `topics(course, topic_id)`, `courses(course)`, `run_labels(run_id, topic_id)`. `homework_vec` has no key (loaded with `replace`; it carries only the vector index). The same map is `oracle/backend.py` `TABLE_KEYS`.
- **HydraDB** (`hydradb-sdk` 2.1.4, import `hydra_db`, `HydraDB(token=...)`): memories are ingested with `client.context.ingest(database=..., collection=..., type="memory", memories=<JSON array string>, upsert="true")`; an item is `{"id", "title", "text", "infer", "custom_instructions", "additional_metadata"}`; `upsert` replaces by `id`. **A query searches one collection only** (`client.query(database=..., collection=..., query=..., query_by="hybrid", recency_bias=0..1)`), so "student + shared" is two queries.
- **Cognee** (1.5.4): feedback is `cognee.session.add_feedback(session_id, qa_id, feedback_text, feedback_score, user)` — not top-level `cognee.add_feedback`. Set `DATA_ROOT_DIRECTORY` and `SYSTEM_ROOT_DIRECTORY` to project folders (the default is inside the installed package). Default posture is authentication required + multi-tenant.

## Ground truth

- Scoring ground truth for every target is an **answer key labeled by a human** (`fixtures/sealed/answer_key_*.csv` shows the shape). It lives in the sealed folder outside the repo, never in the ledger, and is read only by `score.py` after the prediction hash is written. The reveal answer key is loaded into the ledger only by C's reveal pipeline on stage.
- Topic tags on non-target exams (used as model **inputs**, e.g. x1) may come from Cognee. The AI never grades itself.
- `score.py` writes `run_labels` (per run × topic: `y`, `key_points`, `key_source`) **after** the seal; `refit_lessons.py` trains only on these. The sealed prediction itself is never edited.
- If a target has no human answer key, scoring refuses by default. `--allow-machine-key` scores against Cognee tags and stamps `runs.key_source = "machine"`; such runs must be labeled as machine-graded on every chart.
- `SEALED_DIR` must be **outside** the repository. The only exception is `contracts/fixtures/sealed/`, which holds synthetic keys for the fixture course FX.101.

## Ids

As in `kit/03-architecture/data-model.md`: `exam_id = <course>-<exam_type>-<term>` (e.g. `6.003-final-2011F`), `problem` = problem number (+ sub-part letter), `hw_id = <course>-<term>-ps<set>-p<n>`, `run_id = r<seq>-<exam_id>`, `student_id = s<n>`, `topic_id = T01…T20` per course.
