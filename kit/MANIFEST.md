# Manifest

| Path | Purpose |
|---|---|
| `START-HERE.md` | Entry point: 60-second summary, reading order, legend, first actions, NOVA notes |
| `BUILDER-RULES.md` | Binding build rules; copy to repo root as `CLAUDE.md` and `AGENTS.md` |
| `MANIFEST.md` | This file |
| **01-brief/** | |
| `hackathon-brief-original.md` | The official problem statement and builder guide, verbatim (synced from the organizers' Google Doc) |
| `rocketride-setup-guide-original.md` | RocketRide's official hackathon setup guide, verbatim |
| `judging-and-requirements.md` | What the brief requires, how judging works, what is unknown |
| **02-product/** | |
| `product-spec.md` | The product: problem, users, core loop, four learning levels, MVP vs stretch, why it wins |
| `student-layer.md` | Student profiles, syllabus and guideline uploads, feedback loop, privacy, demo personas |
| `prediction-model.md` | Topic lists, seven signals with formulas, lessons (weight refit), baselines, scoring, run list |
| `data-sources-and-sealing.md` | Which MIT courses, what to download, answer-key labeling, sealing and hash protocol |
| **03-architecture/** | |
| `architecture.md` | Where every piece runs, the system diagram, every data flow |
| `stack-map.md` | How the five tools form one stack: purpose, repeated work, handoffs, "if removed" |
| `data-model.md` | Cognee datasets and models, HydraDB collections, hotdata tables, optional Cypher graph, id conventions |
| `rocketride-pipelines.md` | Every RocketRide pipeline and app screen: nodes, inputs, outputs, guardrails |
| `rote-plays.md` | The two Rote Plays: step graphs, generic scripts, checks, recording procedure, resume demo |
| **04-tools/** | One dossier per tool: what it really is, exact interfaces, how we use it fully, gotchas, smoke test, sources |
| `cognee.md`, `hydradb.md`, `hotdata.md`, `rocketride.md`, `rote.md`, `snyk.md` | |
| **05-build/** | |
| `staging-plan.md` | Gated build order G0–G6 with entry/exit criteria and fallbacks; environments; freeze points |
| `smoke-tests.md` | Every first test, step by step, with expected results and what to do on failure |
| `timeline-and-roles.md` | 8-hour timeline, roles A/B/C, two-person and solo variants |
| `repo-layout-and-config.md` | Suggested repository structure, `.env.example`, versions, naming conventions |
| `security-checklist.md` | Snyk routine and design rules that prevent findings |
| `risks-and-fallbacks.md` | Risk register with severity and fallback for each |
| `cut-list.md` | What to cut, in order, when behind; what never to cut |
| **06-demo/** | |
| `demo-script.md` | The 3-minute script with timings and stage setup |
| `judge-qa.md` | Rehearsed answers to likely judge questions |
| `submission-checklist.md` | Everything to hand in and show |
| **07-reference/** | |
| `plan-v2.html` | The whole plan as one visual page |
| `sources.md` | Every source URL, grouped by tool |
| `decisions-log.md` | Decisions made so far and why, including ideas rejected; append new ones here |
| `open-questions.md` | Unknowns and who resolves them |
| `glossary.md` | Terms used in this kit |
