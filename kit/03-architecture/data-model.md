# Data Model

Agree these names in the first hour so everyone builds to the same shape.

## Id and naming conventions `[DESIGN]`

- `course`: OCW code with dot, e.g. `6.003`. Dataset-safe form: `6-003`.
- `term`: `2011F`, `2010S`, `2009F`.
- `exam_id`: `<course>-<exam_type>-<term>`, e.g. `6.003-final-2011F`. `exam_type` ∈ `quiz1 | quiz2 | quiz3 | midterm | final`.
- `problem`: `<exam_id>-p<n>[<sub>]`, e.g. `6.003-final-2011F-p4b`.
- `hw`: `<course>-<term>-ps<set>-p<n>`.
- `run_id`: `r<seq>-<exam_id>`, e.g. `r10-6.003-final-2011F`.
- `student_id`: `s<number>`; demo students `s1` (Maya), `s2` (Leo).
- Dates: ISO `YYYY-MM-DD` strings in hotdata; integers (`YYYYMMDD`) if stored in open-source HydraDB (it only accepts scalars).

## Cognee — datasets `[DOCS]` mechanics, `[DESIGN]` layout

| Dataset | Owner | Access | Contents |
|---|---|---|---|
| `course-<code>` | service user `admin` | read-only to every student (`authorized_give_permission_on_datasets`) | Topics, lectures, homework problems, exam problems, stated guidelines from the official syllabus |
| `student-<id>` | the student | private | StudentProfile, the student's own Syllabus and guidelines, session history |
| `lessons` (optional) | `admin` | read-only | Lesson statements for Cognee-side recall; HydraDB is the primary lesson store |

`node_set` tags on every item: `[course, term, doctype]` with doctype ∈ `exam | homework | lecture | syllabus`.

## Cognee — DataPoint models (illustrative; verify against 1.5.4)

```python
from cognee.low_level import DataPoint   # [DOCS] import path

class Topic(DataPoint):
    course: str
    name: str
    metadata: dict = {"index_fields": ["name"], "identity_fields": ["course", "name"]}

class ExamProblem(DataPoint):
    problem_id: str
    exam_id: str
    points: float
    text: str
    topics: list[Topic]          # a field typed as another DataPoint becomes an edge [DOCS]
    metadata: dict = {"index_fields": ["text"], "identity_fields": ["problem_id"]}

class HomeworkProblem(DataPoint):
    hw_id: str
    text: str
    topics: list[Topic]
    metadata: dict = {"index_fields": ["text"], "identity_fields": ["hw_id"]}

class ExamWindow(DataPoint):
    exam_type: str
    from_week: int
    to_week: int
    cumulative: bool

class StatedGuideline(DataPoint):
    kind: str                     # cumulative | emphasis_window | coverage | format
    from_week: int | None
    to_week: int | None
    share: float | None
    text: str
    source_doc: str

class Syllabus(DataPoint):
    course: str
    topics: list[Topic]
    exams: list[ExamWindow]
    guidelines: list[StatedGuideline]

class StudentProfile(DataPoint):
    student_id: str
    style_order: str              # examples_first | proofs_first
    length: str                   # short | detailed
    modality: str                 # visual | verbal
    pace: str                     # slow | normal | fast
    weekly_hours: float
    exam_date: str
    metadata: dict = {"identity_fields": ["student_id"]}
```

Notes `[DOCS]`: duplicate mentions only merge when `identity_fields` is set; the model is used as the LLM's structured-output schema; keep property values scalar or DataPoint references so they project cleanly.

## HydraDB (hosted) — collections `[DOCS]` mechanics, `[DESIGN]` layout

```
database  exam-oracle
  collection shared
    lesson memories      id = lesson_<signal>_r<seq>   text = statement; metadata: signal, weight, run_seq, runs
    prediction memories  id = pred_<exam_id>           text = top-K summary; metadata: hash, made_at, run_id
    run summaries        id = run_<run_id>             text = scores vs baselines
  collection s<id>
    s<id>_pref_style | _pref_length | _pref_modality | _pref_pace   infer:false (verbatim, replaced on update)
    feedback memories                                              infer:true  (HydraDB extracts the preference)
    syllabus / guidelines                                          ingested as knowledge
```

Budget two queries for "student + shared" context; merging collections in one query is `[TEST]`.

## hotdata — databases and tables

```
ledger (permanent)
  exam_items      course, term, exam_type, exam_id, problem, points, topic, date, text, text_clean
  homework_items  course, term, hw_id, topic, date, text_clean               -- BM25 (text) index
  homework_vec    course, term, hw_id, text_clean                            -- vector index only (must be the sole index)
  lectures        course, term, n, date, topic
  guidelines      guideline_id, course, kind, from_week, to_week, share, source
  guideline_tests guideline_id, run_id, predicted_share, observed_share, verdict
  students        student_id, course, exam_date, weekly_hours
  predictions     run_id, exam_id, topic, p, rank, x1..x7, hash, made_at
  runs            run_id, run_seq, course, target_exam, model_pts, even_pts, lastexam_pts,
                  recall_k, brier, k, tokens, seconds, checks_first_try, leakage_ok, lessons_run_seq

run-<id> (expires 2h)
  exam_items, homework_items, homework_vec, lectures, guidelines — only rows dated before the target exam
```

- One row per (problem, topic) in `exam_items` when a problem has several topics; `points` holds the split share.
- Writes are loads, not `INSERT` (SQL is read-only) `[DOCS]`. First load into a table must be `replace`; later appends use inline CSV ≤ 2 MiB with `idempotency_key = run_id` `[DOCS]`.
- `text_clean` = LaTeX stripped / normalized, because BM25 splits on non-alphanumerics and drops tokens over 40 bytes `[DOCS]`.

## Open-source HydraDB — optional Cypher graph

```
(:Topic {id, key, course})
(:ExamProblem {id, key, points, date})-[:TESTS]->(:Topic)
(:HomeworkProblem {id, key, date})-[:PRACTICES]->(:Topic)
(:ExamProblem)-[:ECHOES {sim}]->(:HomeworkProblem)
(:Lesson {id, signal, weight, run_seq})-[:SUPERSEDES]->(:Lesson)
```

Rules `[DOCS]`: integer node ids (keep the readable key as a `key` string); scalar properties only; directed patterns; bounded variable-length paths (`*1..3`); `MERGE` matches on `id` only (then `SET`); `UNWIND $rows` bulk writes over Bolt only, one relationship type per batch; no problem text (strings over ~32 KiB crash a worker). Loaded by a ~100-line projector from Cognee's `get_graph_engine().get_graph_data()`; map each UUID to an integer id (e.g., first 63 bits) and keep the UUID as `uid`.

Useful queries:
- Current lesson for a signal: `MATCH (l:Lesson) WHERE l.signal = $s RETURN l.weight, l.run_seq ORDER BY l.run_seq DESC LIMIT 1` (no `max()` available).
- "What did resurfaced homework share?": `MATCH (h:HomeworkProblem)-[:PRACTICES]->(t:Topic), (e:ExamProblem)-[:TESTS]->(t), (e)-[x:ECHOES]->(h) WHERE x.sim > 0.8 RETURN t.key, count(e)` `[INFERRED shape]`.
- "Why" paths: `CALL algo.MSpaths(...)` from this term's topics to past exam problems, `maxLen: 3` `[DOCS: procedure exists; exact signature TEST]`.
