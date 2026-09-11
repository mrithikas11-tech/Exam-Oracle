# Timeline and Roles

## Roles (three people)

| Role | Owns | Tools |
|---|---|---|
| **A — Loader** | Generic scripts, Rote Plays, data downloads, hotdata ledger loads, the resume demo, catalog publish | Rote, hotdata, laptop |
| **B — Memory & model** | Topic lists, Cognee models/datasets/feedback, HydraDB collections and lessons, signal queries, scoring, refit, sealing | Cognee, HydraDB, hotdata SQL |
| **C — App & demo** | RocketRide app and pipelines, student layer, dashboard, reveal screen, Snyk, demo script, video | RocketRide, Snyk |
| **Labeler** (any one person, ~30 min total) | Answer keys for hidden exams, outside the repo, before predictions | — |

## Eight hours

| Time | A | B | C | Milestone |
|---|---|---|---|---|
| 0:00–0:45 | Rote + hotdata smoke tests; download all PDFs; count 18.06 finals | Cognee + HydraDB smoke tests; label and seal hidden exams | RocketRide + Snyk smoke tests; decide Cognee location | G0 passed |
| 0:45–2:00 | Generic split/check scripts; record `load-course` on 6.641; export + lint | 6.641 topic list → strict ontology; Cognee models + datasets; ledger tables + saved signal queries | App skeleton; upload-syllabus pipeline; Snyk snapshot 1 | G1 |
| 2:00–3:00 | Replay `load-course` on course 2; log cost | Record `run-backtest`; run 6.641 Final 2008 ← 2006 | Dashboard pipeline over the ledger | **M1** |
| 3:00–4:30 | Replay on 6.003 with skip list; capture resume moment | Lesson refit → HydraDB; Cognee feedback loop; guideline tests; echo search | Maya + Leo profiles; study-plan + feedback pipelines | G3 |
| 4:30–5:30 | Publish `load-course`; export trace | All 6.003 runs; cold-start comparison; seal final prediction | Reveal screen; "why" view | **M2** |
| 5:30–6:30 | Help C | Freeze model; leakage audit | Practice questions (stretch); full demo run | G5 |
| 6:30–7:15 | All: Snyk scan 2 + `aibom`; rehearse ×2; record backup video | | | **M3** |
| 7:15–8:00 | All: buffer; submission; credit OCW | | | Submitted |

## Two people

- Person 1 = A + B's hotdata work (loader, Rote, signals, scoring). Person 2 = C + B's Cognee/HydraDB work.
- Two courses only (6.641 and 6.003); one demo student (Maya); skip catalog publish and open-source HydraDB.

## Solo

- 6.003 only; its Spring 2010 runs stand in for "another course" (cross-exam-type transfer instead of cross-course). One student. No catalog publish, no practice questions, no open-source HydraDB. Say so on stage.

## Check-ins

- 0:45, 2:00, 3:00, 4:30, 5:30, 6:30: five-minute stand-up — gate status, blockers, cuts. Update `decisions-log.md`.
