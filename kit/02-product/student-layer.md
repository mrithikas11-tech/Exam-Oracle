# Student Layer — Profiles, Syllabi, Guidelines, Feedback

Requirement from the product owner: the planner must store each student's preferences on how they like to be taught, and let students give their syllabus and other course guidelines, which are stored and used properly.

## What a student provides

| Input | Form | Example |
|---|---|---|
| Course and exam | pick from loaded courses, or upload a new course's syllabus | 6.003, Final |
| Exam date | date | 9 days from now |
| Weekly study hours | number | 6 |
| Teaching preferences | choices + free text | worked examples first / proofs first; short / detailed; visual / verbal; pace slow / normal / fast |
| Syllabus | PDF or pasted text | topic calendar, exam coverage |
| Professor's stated guidelines | free text or document | "final is cumulative", "one cheat sheet allowed", "final emphasizes the last third" |
| Feedback while studying | button + text + 1–5 rating | "too wordy", "that example helped", 2/5 |

## Where each piece is stored `[DESIGN]`, grounded in `[DOCS]`

| Input | Stored in | Why there |
|---|---|---|
| Explicit preferences | **HydraDB hosted**, the student's own collection (`collection = <student_id>`), memories with fixed ids (`s<id>_pref_style`, `s<id>_pref_length`, `s<id>_pref_modality`, `s<id>_pref_pace`), `infer:false` so they are stored verbatim and replaced on update | HydraDB docs recommend collection-per-user; fixed ids make updates idempotent `[DOCS]` |
| Same preferences, structured | **Cognee**, `StudentProfile` DataPoint in the student's private dataset | Lets Cognee recall and reason with them; passed as the system prompt `[DESIGN]` |
| Exam date, weekly hours | Cognee `StudentProfile` and the hotdata `students` table | Planner arithmetic happens in SQL `[DESIGN]` |
| Syllabus | Parsed in RocketRide (document parser, or LlamaParse for equations) → `remember`ed into the student's Cognee dataset with the `Syllabus` model → also ingested as knowledge into the student's HydraDB collection | Cognee extracts topics/exam windows/guidelines; HydraDB serves it with the student's memory `[DESIGN]` |
| Stated guidelines | Cognee `StatedGuideline` items (kind, numeric params, text, source); test results in hotdata `guideline_tests` | Makes each guideline scoreable against real exams `[DESIGN]` |
| Feedback | HydraDB memory with `infer:true` (HydraDB extracts the lasting preference) **and** Cognee `add_feedback` on the session, followed by `improve()` | Both tools learn; HydraDB's inferred preference is readable for the demo; Cognee's personalization adjusts ranking `[DOCS]` |

## How each input is used

- **Preferences → every generated answer.** The study-plan and practice pipelines read the student's preferences (HydraDB `recall_memory` on their collection) and pass them as the system prompt: order (example before theory, or theory first), length, modality, pace.
- **Exam date + weekly hours → the plan's shape.** Ranked topics are scheduled into the days left, weighted by predicted probability × points share, capped by hours.
- **Syllabus → the topic list and coverage.** The topics extracted from the syllabus become the course's fixed topic list and the Cognee strict ontology for tagging. Exam windows become the coverage signal.
- **Guidelines → the seventh signal ("professor said").** Each guideline is converted to a numeric claim (e.g., `emphasis_window from_week=10 to_week=14 share=0.5`). In backtests we test the same kind of claim against real exams (`guideline_tests`), and the trust weight learned there scales the signal for a new professor's guidelines.
- **Feedback → the next answer.** After "too wordy", HydraDB's inferred preference changes and Cognee's session is scored; the next generated explanation is shorter. The demo shows the stored preference text before and after.

## Converting a guideline into a scoreable claim `[DESIGN]`

| Guideline kind | Parameters | How tested in a backtest |
|---|---|---|
| `cumulative` | none | Share of final-exam points on topics taught before the last quiz vs after |
| `emphasis_window` | `from_week`, `to_week`, `share` | Observed share of exam points on topics taught in that window, compared with `share` |
| `coverage` | `from_session`, `to_session` | Share of exam points on topics inside the stated coverage (18.01's exam pages state coverage such as "Sessions 1–7") |
| `format` | e.g. `cheat_sheet=1` | Not scored; used only to shape the plan (e.g., "build your cheat sheet on day N") |

`guideline_tests` row: `guideline_id, run_id, predicted_share, observed_share, verdict (match | partial | miss)`. The trust weight for a guideline kind = fraction of `match` (partial = 0.5) across all tests so far. Store it as a lesson.

## Privacy and isolation

- Cognee: a private dataset per student; the course dataset is owned by a service user and granted read-only to each student (`authorized_give_permission_on_datasets`); a student reading another student's dataset raises `PermissionDeniedError` `[DOCS]`.
- HydraDB: each student is a separate collection; shared lessons live in a `shared` collection `[DOCS]`.
- "Delete my data": Cognee `forget(everything=True, user=<student>)` deletes that user's datasets; it may also prune the shared session cache `[TEST]` — do not demo deletion unless the smoke test proves other students survive.
- No real student data at the event; personas are fictional and labeled.

## Demo personas (fictional — label on screen)

| | Maya | Leo |
|---|---|---|
| Style | Worked examples first | Proofs first |
| Length | Short | Detailed |
| Modality | Visual | Verbal |
| Weekly hours | 6 | 12 |
| Exam | in 9 days | in 16 days |

Same sealed prediction → two different plans. Maya taps "too wordy" → her next explanation is shorter → the preference text in HydraDB changed.

## Acceptance tests

1. Maya's and Leo's plans for the same prediction differ in order, length and schedule.
2. After Maya's "too wordy" feedback, the next explanation is measurably shorter (word count logged) and her stored preference changed.
3. Maya cannot read Leo's data through Cognee (permission error) or HydraDB (separate collection).
4. A syllabus upload produces a topic list and at least one exam window; a guideline upload produces at least one `StatedGuideline` with numeric params.
5. The plan fits within the student's weekly hours and ends before the exam date.
