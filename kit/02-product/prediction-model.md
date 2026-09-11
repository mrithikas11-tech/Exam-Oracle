# Prediction Model

Deliberately simple enough to explain on stage and to build in hours. Every choice here is `[DESIGN]` unless marked.

## Unit of prediction: topics

- Each course has a **fixed list of 12–20 topics**, taken from its syllabus or lecture calendar **before any prediction is made**.
- Every exam problem, homework problem and lecture is tagged against that list (Cognee, strict ontology — see `04-tools/cognee.md`).
- A fixed list makes different years comparable and scoring fair. Do not let the tagger invent topics.

## Seven signals per (topic t, target exam E)

All computed in hotdata from data dated **before** E (enforced by the per-run database).

| # | Signal | Definition |
|---|---|---|
| x1 | Track record | Recency-weighted share of prior exams of the same type that included t: Σ w(e)·1[t ∈ e] / Σ w(e), with w(e) = 0.7^(terms between e and E) |
| x2 | Coverage | Fraction of t's lectures inside E's coverage window (quizzes: window from the calendar or syllabus; finals: cumulative = 1 for all taught topics) |
| x3 | Homework echo | Number of homework problems on t whose fused search score against any prior exam problem exceeds threshold τ, divided by the max over topics |
| x4 | Lecture time | Lectures on t in the term / total lectures in the window |
| x5 | Untested recent material | For finals only: 1 if t was taught after the term's last quiz and not tested on any quiz this term, else 0 |
| x6 | Already tested | Share of this term's quiz points on t (weight learned; may be positive or negative) |
| x7 | Professor said | 1 if t falls under a stated guideline that applies to E, multiplied by that guideline kind's trust weight (see `student-layer.md`) |

## From signals to probability

- p(t) = σ(β0 + Σ βk·xk).
- **The β weights are the lessons.**
- Cold start (first run ever): β = small equal positive weights, β6 = 0.
- After each run: refit β by L2-regularized logistic regression over all topic rows from all completed runs (label y = 1 if t appeared on that run's exam). With few rows, keep regularization strong (C ≈ 0.5) so weights move gradually.
- A new course starts from the current global β (that is the cross-course transfer). Optionally, after ≥ 2 runs in a course, blend course-specific β with global β.
- Store each β as a lesson in HydraDB's `shared` collection with: signal, weight, run_seq, supporting run ids, and a one-line statement the LLM writes ("Homework echo is the strongest signal across both courses so far").

## Study list size K

- K = median number of distinct topics on prior exams of the same type in this course; if none, the median across courses for that exam type.

## Scoring (per run)

- **Points covered (headline):** each exam problem's points are split equally across its tagged topics; covered = Σ points share of (problem, topic) pairs with topic in the top-K / total points.
- **Topic recall@K:** distinct topics on the exam that are in the top-K / distinct topics on the exam.
- **Brier score:** mean over all topics of (p(t) − y(t))².
- **Baseline A — study evenly:** rank topics by x4 (lecture time), take top-K.
- **Baseline B — copy last exam:** topics of the most recent exam of the same type, ranked by their points there; pad with lecture time to K.
- Log model, baseline A and baseline B scores in the `runs` table every run. Show all three on the chart even when a baseline wins.

## Cold-start comparison (the cross-course proof)

For the reveal course (6.003), make its **first** prediction twice:
1. with the global β learned from earlier courses;
2. with cold-start β.
Score both against the same answer key. The difference is the cross-course transfer, and it goes on the "smarter" slide.

## Run list (forward in time only)

6.641 (course 1):
1. Quiz 1 2008 ← Quiz 1 2006 (+ 2008 term materials before the quiz)
2. Final 2008 ← Final 2006, Quizzes 2006
3. Final 2009 ← Finals 2006, 2008; this term's Midterm 2009

Course 2 (18.06 if ≥ 2 earlier finals exist, else 2.71 Optics): 2–3 runs, decided at G0.

6.003 (course 3, the reveal):
4. Quiz 1 Spring 2010 ← Quiz 1 Fall 2009
5. Quiz 2 Spring 2010 ← Quiz 2 Fall 2009
6. Final Spring 2010 ← Final Fall 2009
7. Quiz 1 Fall 2011 ← Quiz 1 Fall 2009, Spring 2010
8. Quiz 2 Fall 2011 ← Quiz 2 Fall 2009, Spring 2010
9. Quiz 3 Fall 2011 ← Quiz 3 Fall 2009
10. **Final Fall 2011 ← Finals Fall 2009, Spring 2010, Fall 2011 quizzes — THE REVEAL (sealed)**

Roughly 10–13 runs in total. Label the charts with n.

## Prediction output (canonical JSON, then hashed)

```json
{
  "course": "6.003",
  "target_exam": "final-2011F",
  "made_at": "2026-09-11T13:12:04-07:00",
  "lessons_run_seq": 9,
  "k": 8,
  "topics": [
    {"topic": "Fourier series", "p": 0.91, "rank": 1, "signals": {"x1": 1.0, "x2": 1.0, "x3": 0.6, "x4": 0.12, "x5": 0, "x6": 0.2, "x7": 0}}
  ],
  "baselines": {"even": ["..."], "last_exam": ["..."]}
}
```

- Canonical form: keys sorted, no whitespace, UTF-8 (`json.dumps(obj, sort_keys=True, separators=(",", ":"))`).
- Hash: SHA-256 of those bytes. Print hash + `made_at`; store both in the ledger and in HydraDB `shared`.
- At the reveal: recompute from the stored file on screen and show it matches.
- Illustrative values above are placeholders, not results.

## What not to do

- Do not let an LLM "predict the exam" directly; it has probably seen OCW exams in training, which would contaminate the test. The LLM only tags problems and writes explanations; prediction is the transparent signal model.
- Do not tune β by looking at the sealed exam.
