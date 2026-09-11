# hotdata.dev — Insight layer

**Brief's role:** "ultra-high-concurrency execution layer that gives every agent an isolated, ephemeral environment to run SQL, vector, full-text, and geospatial queries"; "Load a file directly (e.g. Parquet/CSV) into an instant database."
**Exam Oracle's role:** hold every tabular fact; compute the seven signals; find homework echoes; isolate every backtest in its own database (leakage-proof); serve the scoreboard.

## What it really is `[DOCS]`

- Built in Rust on Apache Arrow and DataFusion, over Parquet.
- **Workspaces share nothing** ("not compute, not cache, not credentials"). A **database** is "a query scope inside a workspace, created and deleted in a single call … the unit of work." Creation takes "under a second"; `expires_at` deletes it automatically.
- "Results are cached and reusable within a workload"; "every result is saved and queryable with SQL."
- History: query-run list (`GET /v1/query-runs`), fetch old results by id, **saved queries with version history**. Background jobs for any query/load/index build (`GET /v1/jobs`).
- **Forks:** an instant server-side copy with a lineage endpoint — but forks drop indexes.
- **SQL is read-only.** Every write is a load.
- Pricing: $5 base, $10.24/TB scanned, $0.20 per 10 GB stored, **$100 sign-up credit**.
- The words "sandbox" and "replay history" in marketing map to per-workspace isolation + per-database scope + saved queries/results `[INFERRED]`.

## Interfaces `[DOCS]`

**CLI** (`brew install hotdata-dev/tap/cli`):
- `hotdata auth register | login`; `hotdata workspaces list`
- `hotdata databases create [--catalog] [--table]… [--expires-at 2h]`; also `fork` and `load --file|--url|--upload-id|--result-id --mode replace|append|upsert|update|delete`
- `hotdata query "<sql>" [-d db] [--dialect hotsql|postgres|duckdb|snowflake] -o json` — exit codes: 0 ok, 1 fail, 2 still running, 3 preview truncated (fetch full result with `results get`)
- `hotdata search create <name> --type text|vector|sorted --from cat.schema.tbl --column c [--metric l2|cosine|dot] [--provider id] --async`; `hotdata search "<text>" --index <name>`
- `hotdata jobs`; `hotdata manage usage`; `hotdata manage skills install` (Claude Code skill); `hotdata ingest sources add` (the brief's "connect a data source")

**SQL:**
- Full-text: `bm25_search('cat.s.t','col','text',k)` — takes the top k first, applies `WHERE` afterwards; no stemming; tokenizes on non-alphanumerics; drops tokens over 40 bytes.
- Vector: `vector_search('cat.s.t','col','text',k)` embeds the query text and returns `_distance`; also `vector_search_vector`, `l2_distance`, `cosine_distance`. The vector index is used only for `ORDER BY … ASC LIMIT k` with a literal vector; `SELECT *` disables it. A self-embedding vector index must be the **only** index on its table.
- `ST_*` geospatial, window functions, CTEs.
- No SQL hybrid function; the LangChain tool does reciprocal rank fusion when given `search_embedding=`.

**REST** (`https://api.hotdata.dev`, Bearer token + `X-Workspace-Id`): `POST /v1/databases` (`expires_at`, `if_not_exists`), `/v1/databases/bulk` (up to 10,000), `/fork`, `/lineage`, `/loads`, `/v1/query`, `/v1/queries` (saved, versioned), `POST /v1/connections/{id}/tables/{s}/{t}/indexes`; 429 carries `Retry-After`. OpenAPI: https://www.hotdata.dev/openapi.yaml.

**Python:** `hotdata` (typed SDK); `hotdata-framework` (credentials from environment, retries, async polling, DataFrames, BM25/vector index builders); `hotdata-langchain` 0.16.0 (seven tools + `HotdataVectorStore`; give each tool set a name suffix or names collide). No MCP server found `[INFERRED]`.

**Loads:** inline CSV ≤ 2 MiB with an `idempotency_key`; the first load into a table must be `replace`.

## How Exam Oracle uses hotdata fully `[DESIGN]`

1. **`ledger` database (permanent):** all problems, lectures, guidelines, guideline tests, students, predictions, runs.
2. **One throwaway database per backtest** (`run-<id>`, `--expires-at 2h`), built by filtering rows to `date < target` in the worker and loading Parquet with `load replace`. The model physically cannot see the future. (Loading a result from database A into database B via `--result-id` is `[TEST]` — nice if it works, not required.)
3. **Leakage assertion** in every run database: `SELECT max(date) FROM exam_items` < target date → `leakage_ok` in the run row.
4. **Seven signals as saved, versioned queries**, executed identically in every run database; the query-run history is the audit trail; show the lineage endpoint.
5. **Homework echo via hybrid search:** `bm25_search` on `homework_items.text_clean` and `vector_search` on `homework_vec`, fused in a CTE with `1/(60 + rank)` (reciprocal rank fusion); threshold τ chosen once and logged.
6. **Run log by idempotent loads:** append each run row as inline CSV with `idempotency_key = run_id` (replaying never duplicates).
7. **From RocketRide:** `db_hotdata` node with `database_id` = ledger (persistence), `allow_execute` for raw SQL where needed; the node needs an LLM for text-to-SQL `[DOCS]`.
8. **Database context** (server-side Markdown data model) so agents can read the schema.

## Gotchas

- No `INSERT`/`UPDATE` in SQL.
- Forks drop indexes → don't "fork master and delete future rows"; build per-run databases from filtered data.
- Index build time per run is not published `[TEST]`; if slow, build indexes once in the ledger and add date filters (BM25 filters after the limit → over-fetch k).
- Store a de-LaTeXed `text_clean` column for BM25.
- hotdata's embedding model and dimensions are undocumented → never mix its vectors with Cognee's.
- Exit code 3 = truncated preview, not failure.

## Smoke test (15 min)

1. Install; `hotdata auth register`; `hotdata workspaces list`.
2. Create an expiring database; load 50 homework rows from CSV; time it.
3. Build a text index and a vector index on separate tables with `--async`; time the jobs.
4. Run `bm25_search`, `vector_search` and the fused CTE.
5. Try loading a result from database A into database B.
6. Bulk-create 30 databases; time it.
7. Append to the ledger inline twice with the same `idempotency_key`; confirm one row.
8. Run SQL from `hotdata-framework` into a DataFrame; check `hotdata manage usage`.

Go/no-go: step 3 (index build time) and step 5 (cross-database result load).

## Sources

- https://www.hotdata.dev/ · https://www.hotdata.dev/docs/core-concepts · https://www.hotdata.dev/changelog
- https://www.hotdata.dev/docs/api-reference · https://www.hotdata.dev/docs/api-reference/databases · https://www.hotdata.dev/openapi.yaml
- https://www.hotdata.dev/docs/cli-reference · https://github.com/hotdata-dev/hotdata-cli
- https://www.hotdata.dev/docs/sql · https://www.hotdata.dev/docs/push-data · https://www.hotdata.dev/docs/pull-data
- https://www.hotdata.dev/docs/python-sdk · https://pypi.org/project/hotdata-framework/ · https://pypi.org/project/hotdata-langchain/
- https://www.hotdata.dev/docs/agent-skills · https://www.hotdata.dev/pricing
