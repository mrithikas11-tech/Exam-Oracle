# Decisions Log

Append new entries at the bottom: time, decision, why, who. Entries below were made while planning (2026-09-10/11) before anything was built.

## Idea selection

| # | Decision | Why |
|---|---|---|
| 1 | Rejected the brief's ten suggested ideas (support desk, research assistant, recruiting, fraud, finance, legal, networking, coding agent, smart home, sales) | Every team gets the same list; entries converge |
| 2 | Rejected "Told Once" (data analyst corrected once, with a replay "flinch") | Solid fit but least surprising; weak live reveal |
| 3 | Rejected "Patch Once" (vulnerability fix replayed across repos) | Product owner found the gap: an identical fix across repos is just automation; redesigned into pattern learning, then set aside for a student-facing idea |
| 4 | Rejected stock-market ideas ("Broken Promises", "Hidden Exposure Map", "Why did it move?") | Product owner wanted a student-geared direction |
| 5 | Rejected "Mistake Memory", "Degree Path", "Application Autopilot", "Office Hours Memory" | Useful but no moment where the room reacts |
| 6 | **Chose Exam Oracle** | Five-second hook; a sealed, checkable reveal; measurable "smarter" vs baselines; natural fit for all five tools |
| 7 | Added the student layer (preferences, syllabus, guidelines, feedback) | Product owner requirement, 2026-09-11 |

## Architecture (from reading the vendors' docs and source)

| # | Decision | Why |
|---|---|---|
| 8 | Build the app from RocketRide pipelines using the `tool_cognee`, `graph_hydradb`, `db_hotdata` nodes | These nodes exist in RocketRide's docs/source; sponsor-native; judges check real use |
| 9 | Hosted HydraDB is primary; open-source HydraDB optional | RocketRide's node speaks the hosted API; per-student collections and `infer:true` preferences fit the student layer |
| 10 | Do not connect Cognee to open-source HydraDB through the Neo4j provider | Adapter uses APOC, constraints, UUIDs, list properties; HydraDB rejects all; a ~100-line projector if the Cypher graph is wanted |
| 11 | Use Cognee's v1 API (`remember`/`recall`/`improve`/`forget`) with custom models, strict ontology, datasets with permissions, sessions + feedback | Current API; the brief asks for remember/recall; these features carry lessons and preferences |
| 12 | One throwaway hotdata database per backtest | Creation under a second with expiry; makes leakage impossible by construction |
| 13 | Writes to hotdata are loads with idempotency keys | hotdata SQL is read-only |
| 14 | Course-specific logic in generic scripts; Rote records inside a workspace | A Play replays a fixed graph; improvisation would be baked in |
| 15 | The LLM never predicts the exam | It may have seen OCW exams in training; predictions come from the signal model |
| 16 | Reveal exam = 6.003 Fall 2011 Final | 6.003 has Quiz 1, Quiz 2, Final across three terms with solutions (verified) |
| 17 | Where things run: RocketRide staging, hotdata cloud, hosted HydraDB, one reachable Cognee server, Rote on the laptop | RocketRide's Python node can't run subprocesses; deployed apps run on staging |
| 18 | Snyk MCP added via project config, not the global configure command | The configure command writes Claude's global rules file |

## Open at G0 (fill in during the event)

- Cognee location: ____ (Cloud / laptop + tunnel)
- Sponsor nodes present in `services-catalog.json`: ____
- Hosted HydraDB `query_graph` accepts Cypher: ____
- Cross-dataset Cognee recall merges: ____
- Rote replay from Python works: ____
- hotdata per-run index build time: ____
- Course 2: ____ (18.06 earlier finals counted: __ ) / 2.71 / none
- Answer key SHA-256 for 6.003 Final Fall 2011: ____ (time: ____)
