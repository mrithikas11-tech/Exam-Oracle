# Course structure — the A/B boundary

Owner: **Person B**. Consumer: **Person A**'s loader. Checked by `python -m oracle.structure --course <c> --check`
(exit 0 = pass, 3 = any check fails, 1 = usage/hard fault; one JSON object on stdout) and `tests/test_structure.py`.
Where this file and the kit disagree, this file wins (as for every contract).

## 1. Who owns what

| Who | Writes | Reads |
|---|---|---|
| **B** | `data/courses/<course>/{course.json, topics.csv, sessions.csv, exams.csv, homework_sets.csv, guidelines.csv, skip_list.txt}`, `data/run_lists/real_chain.csv`, `data/run_lists/history_side_track.csv` | — |
| **A** | ledger rows (`contracts/ledger-schema.sql`) | the course folder, **only** through `oracle.structure.load_course(course)` |

A's loader takes **every** time, coverage, cumulative, sealing and topic fact from the folder: `term`, `term_seq`,
`session`, `date`, `coverage_*`, `cumulative`, `sealed`, `exam_type`, `topic_id`, lecture rows, guideline rows,
homework due sessions. From the PDFs A extracts **only**: problem numbers, sub-part letters, per-problem points,
problem text, and homework problem text. It joins exam PDFs on `exam_id` and homework PDFs on `(term, set)`.
A never infers a session, a date, a coverage window or a topic from a PDF, and never writes to `data/courses/`.

`load_course()` returns (raises `StructureError` listing every problem if the folder fails):

- `tables` / `frames`: the ledger tables `courses`, `topics`, `lectures`, `exams`, `guidelines`, typed exactly as
  `ledger-schema.sql` (`to_contract_table`), ready for Parquet.
- `exam_sources`: the exams whose PDFs A may fetch for `exam_items` — roles `target_full`, `target_history`,
  `x1_input`, never a sealed exam — with `problems_url`, `solutions_url`, `same_file`.
- `homework_sets` and `homework_due_session(term, set)`: the due session that becomes `homework_items.session`
  (`None` = not loaded; `loadable` column).
- `skip_list`, `meta` (course.json, including `assumptions` and 6.641's `packet_sections`), `non_ocw_urls`.

## 2. Files and columns (as the files are today)

Common: UTF-8 CSV, header exactly as below (extra columns are refused: structure files carry no problem
content), booleans `true`/`false`, empty cell = NULL, ids `[A-Za-z0-9._-]`, every row's `course` = the folder name.

### course.json

| Key | Meaning |
|---|---|
| `course`, `title`, `ocw_url` | code (= folder name), title, the OCW course URL |
| `published_term`, `published_term_seq` | the one term whose lectures and homework OCW publishes (`2011F`/`20113`, `2010S`/`20101`, `2009S`/`20091`) |
| `instructor`, `calendar_has_real_dates` | optional, informational |
| `assumptions` | the assumptions register: `[{id, text}]`, ids unique per course (§9) |
| `sources_note`, `license` | how and when each file was built; the licence of every source |
| `packet_sections` | optional (6.641): page map of the 2005 review packet; A slices by it (`LOADER-PACKET`) |

No key anywhere in the tree may be one of `uncertainties, checks, problems, problem_text, questions, items,
exam_items, answers, answer_key, solution_text, points, sub_parts, subparts, topics_tagged, tags`.

### topics.csv — the fixed topic list (course structure, shared by all terms)

| Column | Meaning |
|---|---|
| `topic_id` | `T00`–`Tnn`. `T00` = "Off-list (not in topic list)" (§5) |
| `topic` | name, from published-term lecture titles |
| `first_session`, `last_session` | **session** numbers bounding the topic's published-term sessions (outer bounds, not membership); blank for T00 and untaught topics |
| `lecture_ns` | `;`-separated **lecture** numbers of the topic (differ from sessions in 6.641) |
| `taught_in_published_term` | false for T00 and for real topics not taught that term (6.641 T15/T16) |
| `grounding` | the pre-exam sources the topic was built from; never exam content |

### sessions.csv — one row per published-term calendar session

| Column | Meaning |
|---|---|
| `term`, `term_seq` | always the published term |
| `session` | the calendar session index (unique). Exams are placed on this scale (§3) |
| `date` | ISO date if a source states it, else blank; informational only |
| `lecture_n` | lecture number (= session in 6.003/18.06; multi-session lectures repeat it in 6.641); blank on review rows |
| `title` | verbatim calendar / lecture-notes title |
| `topic_id` | exactly one listed topic, never T00; blank on review rows |
| `is_review` | true for review-only sessions (18.06 SES 24, 36): structure only, **not** loaded as lectures |

### exams.csv — one row per exam paper, all terms

| Column | Meaning |
|---|---|
| `exam_id` | `<course>-<exam_type>-<term>` |
| `exam_type` | `quiz1 / quiz2 / quiz3 / midterm / final`, **by position** (§4) |
| `original_name` | what the sources call it (informational) |
| `term`, `term_seq` | the exam's own term |
| `session`, `session_source` | §3 (D2) |
| `date` | from the paper header / calendar; informational |
| `total_points` | printed total, or blank (A sums problem points; sealed: always blank) |
| `coverage_from_session`, `coverage_to_session` | a **stated** window only, else blank. Known exception: 6.003 Quiz 2/3 are PINNED assumed windows until `sql/coverage_window.sql` is fixed (6.003 `W-QUIZ`) |
| `cumulative`, `cumulative_basis` | true/false/blank(unknown) and why ("stated: …" / "assumed: …") |
| `sealed` | true only for the reveal (§8) |
| `problems_url`, `solutions_url` | the PDFs A fetches; equal for combined files (18.06 2007S, the 6.641 packet) |
| `url_verified` | the URL was checked (HEAD/range) when the folder was built |
| `role` | `target_full` (published-term target), `target_history` (earlier-term target), `x1_input`, `sealed_reveal`, `excluded` (never loaded) |

### homework_sets.csv — published-term homework

| Column | Meaning |
|---|---|
| `term`, `term_seq`, `set` | key `(term, set)`; `set` is a number or `opt` (6.641 "Additional problems") |
| `issued_session` | when stated, else blank; never used for visibility |
| `due_session` | the visibility session: a set is visible to exam `E` iff `due_session < E.session`. Blank = not loaded (must be `is_practice = true`) |
| `is_practice` | not collected (6.003 HW4/7/10/14, 6.641 opt) |
| `problems_url`, `solutions_url`, `url_verified` | as for exams |

### guidelines.csv — the professor's stated guidance, verbatim

| Column | Meaning |
|---|---|
| `guideline_id` | `<course>-G<nn>` (the ledger key is `guideline_id` alone) |
| `kind` | `coverage`, `cumulative`, `emphasis_window`, `format`, `homework_analogous` (scored, D7) |
| `applies_to_exam_type`, `applies_to_term` | the exam slot and term the statement is about |
| `from_session`, `to_session`, `share` | the claimed window and share of points (blank when the kind has none) |
| `source_term`, `source_term_seq`, `source_session` | when students could first see it (visibility follows the ordering rule) |
| `source_session_basis` | why that session (e.g. "assumed posted at term start") |
| `text`, `source_url` | verbatim quote and where it is |

### skip_list.txt

One URL per line (`#` comments allowed). The loader refuses these URLs before any fetch (exit 2). It lists every
URL of the sealed exam (landing pages, PDFs, host variants, the whole-course zip and its `/download/` page). No URL
on it may appear in any other structure file.

## 3. Time: visibility (D1) and exam sessions (D2)

- **D1.** A row is visible to target `T` iff `row.term_seq < T.term_seq` or (same `term_seq` and
  `row.session < T.session`). Dates never decide.
- **D2, published term.** Lectures carry their calendar session index. An exam gets:
  - `calendar_own_row`: it has its own calendar row → that row's session; no sessions.csv row carries it
    (18.06 S10: Exam 1 = 12, Exam 2 = 25, Exam 3 = 37, Final = 40).
  - `calendar_shared_row`: it shares a row with (or falls between) lectures → **last delivered session + 1**, so a
    lecture carrying that session was given **after** the exam (6.003 F11: Q1 = 9, Q2 = 14, Q3 = 20, Final = 26;
    6.641 S09: Midterm = 12, Final = 26). The check requires a lecture row at `session − 1`.
  - Consequence for every query: "before exam E" is `session < E.session`; "after exam E" is `session >= E.session`.
- **D2, other terms.** No calendar: `synthetic` ordinals that only order a term's exams — quiz1 = 1000,
  quiz2 = 2000, quiz3 = 3000, final = 9000. Cross-term visibility is decided by `term_seq` alone.
- **Homework** is visible by **due** session (`due_session < E.session`).
- **Feature set (D3).** `full` iff the target is in the published term, else `exam_history`. Nothing is forced to
  zero: an earlier-term target simply sees no published-term lectures or homework.

## 4. exam_type by position (A1)

The first in-term exam of a term is `quiz1`, the second `quiz2`, the third `quiz3`, the final `final`, whatever the
sources call them ("Quiz n", "Exam n", "Midterm n", 6.641's single "Midterm" → `quiz1`). One exam per slot per term;
slots follow session order. Makeup/conflict papers collapse into their slot; practice papers are never exams.
`midterm` is allowed by the schema but unused.

## 5. Topics

- The topic list is fixed from published-term lecture titles **before any label exists** and is shared by all terms
  as labels only (for earlier terms it never builds x2/x4). Its sha256 after freezing is recorded in the course's
  `A-TOPIC` assumption. Every non-review session carries exactly one topic; each taught topic's `lecture_ns` and
  session bounds equal what sessions.csv says (the partition check).
- **T00 (D6)** exists in every course, has no sessions, bounds or lectures, is loaded into the ledger `topics` table
  (the Cognee tagger's strict ontology needs it), and is **never ranked, predicted, or counted in K or any signal**;
  answer-key points tagged T00 stay in the scoring denominator. Identify it by `topic_id = 'T00'`, never by
  `taught_in_published_term = false`.

## 6. Mapping into the ledger

| Ledger table | From | Rule |
|---|---|---|
| `courses` | course.json | 1 row |
| `topics` | topics.csv, all rows incl. T00 | `first_lecture ← first_session`, `last_lecture ← last_session` (SESSION numbers, 6.641 `LOADER-TOPICS`) |
| `lectures` | sessions.csv | skip `is_review = true`; blank date → NULL |
| `exams` | exams.csv | skip `role = excluded`; drops `original_name, session_source, cumulative_basis, *_url, url_verified, role` |
| `guidelines` | guidelines.csv | drops `applies_to_term, source_session_basis` |
| `exam_items` (A) | PDFs of `exam_sources` | `exam_type, term, term_seq, session` copied from the exams row by `exam_id`; `problem, points, text_clean` from the PDF; `topic_id` from `oracle.cognee_tag` or a human; never a sealed exam |
| `homework_items` (A) | PDFs of `homework_sets` | `hw_id = <course>-<term>-ps<set>-p<n>`; `session = homework_due_session(term, set)`; skip sets that are not `loadable` |
| `homework_vec`, `echo_pairs` | from `homework_items` / `exam_items` | as in `ledger-schema.sql` |

Course-specific loader rules live in the assumptions register and bind A: 6.641 `LOADER-PACKET` (slice the packet,
strip solutions, keep printed numbers), `LOADER-ROLES`; 18.06 `A-X1-FILES` (2007S combined files: strip solution
text; six papers need OCR), `A-HWTEXT` (homework statements come from the OCW solution PDFs), `A-SES`; 6.003
`T5-CURATION` (the excluded Spring 2010 Quiz 3 gets no rows).

## 7. Sources

- Every URL must be `https://ocw.mit.edu/…` (`oracle.config.validate_ocw_url`, on the first URL and every redirect).
- **One documented exception: 18.06** x1 papers (2006S–2009F) and the 2010S pset PDFs at
  `https://web.mit.edu/18.06/www/` (64 URLs). That archive is **not** OCW and states **no Creative Commons licence**;
  the OCW 18.06 material is CC BY-NC-SA 4.0. `oracle.structure.SOURCE_EXCEPTIONS` lets the *check* accept these URLs
  and counts them (`non_ocw_urls`); it does **not** let anything fetch them. A extends the fetch allowlist
  **deliberately**: a decisions-log line naming host + path prefix, used only for this non-commercial backtest,
  no redistribution of the PDFs, credit to the MIT 18.06 course pages. Without it, 18.06 targets have no x1 papers
  (every 18.06 x1 paper is on the archive) and 18.06 homework text still comes from the OCW solution PDFs.

## 8. Sealing and the skip list

- `sealed = true` exactly for `role = sealed_reveal` (today only `6.003-final-2011F`). Its row carries no URLs, no
  total and `url_verified = false`; it is loaded as an `exams` row only, never items. Its answer key lives in
  `SEALED_DIR` outside the repo and is read only by `score.py` after the prediction hash exists.
- A course with a sealed exam must have a non-empty `skip_list.txt`. 18.06 and 6.641 have none (`A-NOSEAL`,
  `SKIP`): their backtest targets are protected per run by `sql/leakage.sql`, and labellers open them outside the repo.

## 9. Assumptions register

Each course's `course.json.assumptions` is the register: one `{id, text}` per assumption, cited by id on stage and
in the decisions log. Cross-course ids:

| Subject | 6.003 | 18.06 | 6.641 |
|---|---|---|---|
| exam_type by position | A1 | A1 | A1 |
| exam sessions (D2) | S-SESSION, S-SOURCE | A-EXSES | S-EXAM |
| synthetic sessions | S-SYNTH | A-SYN | S-SYNTH |
| homework by due session | S-HW-DUE, S-HW-PRACTICE | A-HW | S-HW |
| coverage windows | W-QUIZ | A-COV, A-COV-GAPS | C-MID |
| finals cumulative (assumed) | W-FINAL | A-FINAL | C-FIN |
| topic list frozen / T00 | TOPIC-LIST, A-TOPIC | A-TOPIC, A-T00, A-T02 | A-TOPIC, T00-RANK, LOADER-TOPICS |
| earlier exams available to students | A-AVAIL | A-AVAIL | A-AVAIL |
| sealing / skip list | SEALED | A-NOSEAL | SKIP |
| curated or partial papers | T5-CURATION | A-X1BOUND, A-PRACTICE | A-PACKET |
| sources | X1-SCOPE | license, A-X1-FILES | NO-CONTENT |

## 10. Run lists (D8)

`data/run_lists/real_chain.csv` and `history_side_track.csv`, columns `run_seq, course, target_exam, feature_set,
cold_start, notes` (read by `oracle.run_list`; `notes` is ignored). exam_ids are exactly those of exams.csv.

- **Real chain (all `full`):** 6.641 Midterm 2009 (M1, run 10) → 6.641 Final 2009 (20) → 18.06 S10 Exams 1–3 +
  Final (30–60) → 6.003 F11 Q1 (70) + cold-start twin (71) → Q2 (80) → Q3 (90) → 6.003 F11 Final **sealed** (100)
  + cold-start twin (101). Both reveal predictions are sealed before either is scored (keep the key out of
  `SEALED_DIR` until both `.sha256` files exist; B_README §6). The frozen-beta_G arm is **deferred**.
- **History side track (all `exam_history`, never refits under D4):** 6.641 2006 F (1) → 2008 Q1 (2) → 2008 F (3);
  6.003 S10 Q1 (21) → Q2 (22) → F (23). The run_seq gaps interleave it with the chain in time order, so every run
  reads only lessons learned from earlier terms (`read_lessons --before-run-seq N`), and both lists pass
  `run_list`'s forward-in-time checks together. The side-track rows are ordinary `exam_history` runs with
  `cold_start = false`: there is no side-track column and no exemption. D4 is in `refit_lessons`, so they never
  train; each still writes a lessons version carrying the weights of the full runs before it.
- `tests/test_real_visibility.py` loads the three folders into a temporary ledger and checks every target of both
  lists against D1/D2/D3 (exact visible rows, the sealed Final never visible).
- `REAL_TEMPLATE.csv` is superseded (kept as a record of the kit's list; nothing reads its rows).

## 11. Change protocol

1. Only B edits `data/courses/*` and the run lists. A asks through a CHANGE REQUEST or a decisions-log line.
2. Every edit re-runs `python -m oracle.structure --course <c> --check` (exit 0) and `tests/test_structure.py` in the
   same change. New columns, files, kinds, roles or session sources update this document, `oracle/structure.py`
   (`HEADERS`, vocabularies) and the test together, plus one decisions-log line.
3. `topics.csv` is frozen once any label exists (sha256 in `A-TOPIC`); a later change means re-labelling and a
   decisions-log line. A sealed exam's row and `skip_list.txt` change only with a decisions-log line.
4. Any change to a ledger contract column updates `ledger-schema.sql` and its fixture in the same commit
   (`contracts/README.md`). `kit/07-reference/decisions-log.md` is append-only.
