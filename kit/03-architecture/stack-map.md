# Stack Map — Five Tools, One Stack

The brief: "five things working together as one cohesive stack, not five disconnected API keys." This file shows each tool's purpose, the repeated work it does, what it hands to the others, and what breaks without it. Put the "If removed" column on a slide.

## Purpose and repeated work

| Pillar | Tool | Purpose in Exam Oracle | Repeated work (when, how often) | Signature features used |
|---|---|---|---|---|
| Structure | **Cognee** | Turn syllabi, lecture notes, problem sets and exams into topic-tagged knowledge; keep course and student memory; learn from scored sessions | Every course load (hundreds of items), every syllabus upload, every study-plan request (`recall`), every backtest and every feedback (`add_feedback` + `improve`) | `remember`/`recall`/`improve`/`forget`; custom graph models; strict ontology; datasets with permissions; sessions + feedback; personalization |
| Memory | **HydraDB** (hosted) | Durable lessons, predictions and per-student memory, served to the agent | Read before every prediction and every plan; written after every backtest and every piece of feedback | Collection per user; `infer:true` preference extraction; hybrid `/query` with `recency_bias`; RocketRide node |
| Insight | **hotdata** | The seven signals, homework-echo search, leakage-proof backtests, the scoreboard | Every backtest (new throwaway database + saved queries), every plan request, every dashboard refresh | Throwaway databases in under a second with expiry; saved versioned queries; BM25 + vector search; loads with idempotency keys |
| Motion | **RocketRide** | The student app and the agent pipelines that call the data tools | Every student action and every dashboard refresh; webhooks after every course load/backtest | Sponsor nodes (`tool_cognee`, `graph_hydradb`, `db_hotdata`); Wave agent; document parsers; webhooks; tracing with token counts |
| Muscle memory | **Rote** | Replay course loading and backtests as fixed Plays | Every course after the first (`load-course`), every backtest (`run-backtest`, ~10×) | Compiled Plays with typed inputs; failure lanes; `--resume`; `rote trace --html`; `rote stats`; Community catalog |
| Security | **Snyk** | Keep the app clean and prove it continuously | Hour 1 and hour 6 routine scans; on-demand scans from the coding agent | Snyk Studio MCP; `code test --report` history; `monitor`; `aibom` |

## Handoffs (who writes what, who reads it)

| Artifact | Written by | Stored in | Read by |
|---|---|---|---|
| Raw PDFs, extracted text | Rote `load-course` | laptop `data/raw` | Rote scripts |
| Exam/homework/lecture rows | Rote `load-course` | hotdata `ledger` | hotdata saved queries; `run-backtest` |
| Topic tags, course knowledge graph | Cognee (called by `load-course`) | Cognee `course-<code>` | Cognee `recall` (RocketRide plan pipeline); projector to optional Cypher graph |
| Topic → problem tags as rows | `load-course` (reads Cognee output) | hotdata `ledger` | signal queries |
| Signals per run | hotdata saved queries | hotdata `run-<id>` | `run-backtest` ranking step |
| Lessons (β weights + statements) | `run-backtest` | HydraDB `shared` | next `run-backtest`; RocketRide plan pipeline ("why") |
| Predictions + hashes | `run-backtest` | hotdata `ledger` + HydraDB `shared` | Dashboard, reveal, plan pipeline |
| Run scores, tokens, timings | `run-backtest` | hotdata `ledger.runs` | Dashboard charts |
| Student preferences | RocketRide onboarding/feedback | HydraDB `<student>` + Cognee `student-<id>` | Plan and practice pipelines |
| Syllabus, guidelines | RocketRide upload pipeline | Cognee `student-<id>` + HydraDB `<student>` | Topic list, coverage, x7 signal, plan |
| Guideline test results | `run-backtest` | hotdata `guideline_tests` | Trust weights → lessons |

## If removed

| Remove | What breaks |
|---|---|
| Cognee | Nothing is tagged to topics, so there is nothing to predict from; no student profile reasoning; no session feedback loop |
| HydraDB | Lessons don't persist, so every course and every student starts from zero; no cross-course transfer |
| hotdata | No signals, no echo detection, no leakage proof, no charts |
| RocketRide | Nothing reaches a student; no app, no reveal screen |
| Rote | Every course load and backtest costs full agent reasoning; the "cheaper" line stays flat; no resume |
| Snyk | Points lost; no evidence of continuous security |

## How the loop compounds (say this to judges)

1. Rote loads a course cheaply because it already learned how (muscle memory).
2. Cognee structures it onto the course's topics (structure).
3. hotdata computes signals in a database that cannot see the future (insight).
4. HydraDB supplies lessons learned from every earlier course and student (memory).
5. RocketRide turns the prediction into a plan for this student, in their style (motion).
6. The result is scored or rated, and the lesson or preference goes back into memory — so the next run starts better.

## Answer for the HydraDB judge

"The brief pitches Cypher. We use HydraDB's hosted memory API as the core because RocketRide's HydraDB node speaks it and it gives every student an isolated collection with preference inference. [If built:] We also run the open-source HydraDB for the course graph and use `algo.MSpaths` to show why a topic ranked high."
