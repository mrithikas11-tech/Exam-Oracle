# Data Sources and Sealing Protocol

## Courses (checked on the OCW exam pages, 2026-09-10)

| Course | URL | What's public `[DOCS]` | Role |
|---|---|---|---|
| 6.641 Electromagnetic Fields, Forces & Motion (Spring 2009) | https://ocw.mit.edu/courses/6-641-electromagnetic-fields-forces-and-motion-spring-2009/pages/exams/ | Finals 2006, 2008, 2009 · Quiz 1 2006 and 2008 · Quiz 2 2006 · Midterm 2009 · 2005 final review packet — all with solutions | Course 1: teaches the first lessons |
| 18.06 Linear Algebra (Spring 2010) | https://ocw.mit.edu/courses/18-06-linear-algebra-spring-2010/pages/exams/ | Exams 1–3 and Final 2010 with solutions; the page says older exams are under Study Materials — **count not checked** `[TEST]` | Course 2 if ≥ 2 earlier finals exist; otherwise 2.71 Optics (https://ocw.mit.edu/courses/2-71-optics-spring-2014/pages/exams/, also unchecked) |
| 6.003 Signals & Systems (Fall 2011) | https://ocw.mit.edu/courses/6-003-signals-and-systems-fall-2011/pages/exams/ | Quiz 1, Quiz 2, Final for Fall 2009, Spring 2010, Fall 2011 · Quiz 3 for Fall 2009 and Fall 2011 — all with solutions | Course 3 and the reveal; **Fall 2011 Final sealed** |
| 18.01 Single Variable Calculus (Fall 2006) | https://ocw.mit.edu/courses/18-01-single-variable-calculus-fall-2006/pages/exams/ | One term: Exams 1–4, each with a practice exam and practice questions; exam pages state coverage ("Sessions 1–7") | Backup course; source of stated-coverage guidelines to test |

**License:** MIT OpenCourseWare content is CC BY-NC-SA. Credit "MIT OpenCourseWare" on the demo slide and in the README. The demo is non-commercial.

## What to download per course

- Exams and their solutions (PDF).
- Lecture notes (PDF) and the calendar / syllabus page (HTML) — for the topic list, dates and coverage windows.
- Problem sets and solutions (PDF) — for the homework-echo signal.
- Download **everything in the first 45 minutes**, into `data/raw/<course>/` named by SHA-256 of content, with a `manifest.csv` mapping hash → original URL, type, term, date.
- Do not download the sealed exam into the repository at all. One teammate downloads it into a folder outside the repo for labeling.

## Answer-key labeling protocol (for every hidden exam)

1. One teammate (the labeler) opens the hidden exam **outside the repo**.
2. Fill `answer_key.csv` with: `exam_id, problem, sub, points, topics` (topics from the course's fixed list; separate multiple topics with `;`).
3. Check that points sum to the printed total.
4. Store the file in the sealed folder. Record its SHA-256 in `decisions-log.md` with the time.
5. The labeler does not touch the model code for that course until after the prediction is sealed.

For backtests on non-reveal exams, the same protocol applies; the labeler can label several exams at once in the first two hours.

## Sealing rules (all mandatory)

1. Every target exam and its solutions live outside the repo and are listed in `skip_list.txt` (by URL and by hash). `load-course` refuses to download or read them (hard fault if attempted).
2. Each backtest runs in its own hotdata database (`run-<id>`, expires in 2 h) that contains only rows dated before the target exam. The run asserts `max(date) < target_exam_date` inside that database and logs `leakage_ok`.
3. The model never sees the answer key until scoring; scoring code reads the key only after the prediction hash has been written.
4. Every prediction prints SHA-256 + timestamp. The reveal prediction's hash is shown on the dashboard all afternoon.
5. Runs go forward in time only (see the run list in `prediction-model.md`).
6. The LLM never predicts the exam directly (it may have seen OCW exams in training). It only tags and explains.

## Dates

Exams need dates to enforce "before the target exam." Use the term's exam date from the calendar page; if only a term is known, use the term's last day for finals and the calendar's quiz dates for quizzes. Record any assumed date in `decisions-log.md`.

## Known data risks

- Math-heavy PDFs extract badly with plain text extractors; topic tagging tolerates garbled equations; points are parsed from patterns like "(N points)". The answer key for hidden exams is typed by hand anyway.
- Some courses may use scanned PDFs → the `load-course` check fails → repair step (OCR) → new Play version. This is the Rote resume demo.
