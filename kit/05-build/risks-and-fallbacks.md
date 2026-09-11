# Risks and Fallbacks

| # | Risk | Severity | What happens | Fallback |
|---|---|---|---|---|
| 1 | RocketRide's cloud engine can't reach Cognee | Critical | The deployed app runs in the cloud; a laptop Cognee server is invisible to it | Cognee Cloud (Plan A) or laptop Cognee server behind an authenticated tunnel (Plan B) |
| 2 | Sponsor nodes missing from the installed RocketRide extension (1.2.0) | Critical | Docs list them; the installed catalog may not | Wave agent calls REST APIs via `tool_http_request` (agent-only) |
| 3 | Cognee ingest too slow or expensive | High | Per-chunk LLM extraction at ~60 requests/minute | Pre-split items; cheaper `LLM_EXTRACTION_MODEL`; `dry_run` first; `self_improvement=False`; start course 1 by hour 1; reduce homework set size |
| 4 | Hosted HydraDB has no Cypher | High | Only store/recall memory | Core unaffected; "why" view via Cognee recall or optional open-source HydraDB |
| 5 | Rote Play doesn't transfer between courses | High | Course 1's improvised parsing is baked in | Generic scripts driven by inputs; record only inside the workspace; if still failing, scripts are the recipe and Rote records run-and-check |
| 6 | Rote replay can't be called from Python | High | No automation | Run Plays from the terminal during the demo; charts still fed by ledger |
| 7 | Model loses to "copy last exam" | High | Professors repeat themselves | Show it honestly; lessons state which signals matter per course; pitch "it learns what each professor does" |
| 8 | Math PDFs extract badly | High | pypdf garbles equations | Topic tagging tolerates it; points from "(N points)" patterns; LlamaParse for student uploads; answer keys typed by hand |
| 9 | Leakage bug | High | Invalidates the demo | Per-run database with pre-exam rows; leakage assertion; skip list; hash commitment; audit at G5 |
| 10 | Cross-dataset `recall` doesn't merge | Medium | Plan answers miss one side | Two recalls, merged by the agent |
| 11 | Wave agent misbehaves | Medium | Experimental node | Cap `max_waves`; LangChain agent node instead |
| 12 | hotdata per-run index builds slow | Medium | Unpublished timings | Build indexes once in the ledger; date-filter; over-fetch BM25 k |
| 13 | "Delete my data" prunes others' sessions | Medium | Cognee full prune | Test first; exclude deletion from the live demo |
| 14 | RocketRide auth keeps dropping | Medium | Empty key in `.env` | `pnpm exec rocketride login`; report if recurring |
| 15 | Credits run out (RocketRide, hotdata, LLM) | Medium | Pipelines stop | Cap waves; expire databases; price Cognee ingests; monitor `hotdata manage usage` |
| 16 | Snyk quota or late findings | Medium | 100 Code tests/month on free | On-demand scans + two routine scans; design out findings |
| 17 | Open-source HydraDB write failures | Medium | HTTP writes 500 (#196), Bolt decode errors (#98) | Bolt only; retry idempotent MERGEs; or drop the Cypher graph |
| 18 | Too few runs for a convincing chart | Medium | ~10–13 runs | Label with n; say "backtests", not "proven" |
| 19 | Quiz coverage windows unclear | Medium | Coverage signal weak | Use finals only (cumulative) |
| 20 | Venue Wi-Fi | Medium | Downloads and cloud calls stall | Download all PDFs in G0; backup video offline; hotspot |
| 21 | The LLM has seen OCW exams in training | Medium | Contamination if it predicts directly | The LLM never predicts; only tags and explains; predictions come from the signal model |
| 22 | 18.06 has too few earlier finals | Low | Course 2 weak | 2.71 Optics (check at G0) or skip course 2 |
