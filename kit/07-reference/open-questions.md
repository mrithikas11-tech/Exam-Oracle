# Open Questions

| Question | Why it matters | Who resolves | How |
|---|---|---|---|
| Team size, skills, start and demo times | Timeline and cuts | Humans | Ask at start |
| Submission format and demo length | Demo script, video length | Organizers | Ask at check-in |
| Is Cognee Cloud available at the venue? | Plan A vs Plan B | Cognee sponsor | Ask at check-in / Discord |
| Are sponsor nodes in RocketRide 1.2.0's `services-catalog.json`? | Pipelines vs HTTP fallback | C | Smoke test |
| Does hosted HydraDB `query_graph` / MCP `hydradb_graph_query` accept Cypher? | "Why" view path | B | Smoke test |
| Can one HydraDB query span a student collection and `shared`? | Query count | B | Smoke test |
| Does `recall(dataset_ids=[course, student])` merge results? | Plan pipeline design | B | Smoke test |
| Does Cognee `forget` for one user prune others' sessions? | Whether to demo deletion | B | Smoke test |
| Can Rote Plays be run from Python with `--yes --output=json`, and does the JSON include tokens? | Automation; cheaper chart | A | Smoke test |
| hotdata index build time per database; cross-database `--result-id` load | Per-run design | A | Smoke test |
| hotdata SDK environment variable names | Config | A | hotdata-framework docs |
| How many earlier finals does 18.06 publish? | Course 2 | A | Open Study Materials |
| Exact exam dates for each term | Leakage cut-off | A / Labeler | OCW calendar pages |
| Does the Wave agent handle three sponsor tools reliably within `max_waves`? | Pipeline stability | C | Smoke test |
