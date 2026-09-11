# HydraDB — Memory layer

**Brief's role:** "object-store-native graph database purpose-built as the context substrate for AI systems … snapshot-consistent OpenCypher queries and fast multi-hop graph traversal"; "Cognee as the front door for ingestion, HydraDB as the backing store."
**Exam Oracle's role:** durable lessons, predictions and one memory collection per student, served to the agent with recency-weighted retrieval. Optionally, a Cypher course graph for the "why" view.

## The key fact: "HydraDB" is two products `[DOCS]`

| | Hosted HydraDB (primary for us) | Open-source HydraDB (optional) |
|---|---|---|
| What | A memory/context API: databases, collections, memories, knowledge ingest, hybrid retrieval, preference inference | A Rust graph database on object storage, OpenCypher subset over Neo4j Bolt |
| Access | `pip install "hydradb-sdk>=2,<3"`, `api.hydradb.com`; free "Ship" tier; MCP server with a Cypher tool `hydradb_graph_query` | Docker `ghcr.io/hydra-db/hydradb` (v0.1.1, 2026-08-12, arm64 native); Bolt `7687`, HTTP `8443`, readiness `9090` |
| Query | Endpoints: ingest, `/query` (`query_by:"hybrid"`, `recency_bias`, `graph_context`), `/context/relations`, `/context/{id}/subgraph`; no public Cypher endpoint | Cypher subset (below) |
| RocketRide | `graph_hydradb` node: `store_memory`, `recall_memory`, `query_graph`, `get_schema` (api_key, database, collection) | Only through an HTTP tool / laptop code |

Marketing's "Git-style temporal versioning" has no point-in-time query in the public API — treat as not callable; keep history yourself.

## Hosted: how we use it fully `[DOCS]` mechanics, `[DESIGN]` layout

- `database = exam-oracle`; a `database` is fully isolated.
- **Per-student collection:** `collection = <student_id>` (the docs' recommended pattern). Memories hold "preferences, conversation history, behavioral signals"; each has a stable `id` that `upsert` replaces.
- **Explicit preferences:** `infer:false` (stored verbatim) under fixed ids `s<id>_pref_style|length|modality|pace`.
- **Feedback:** `infer:true` extracts the lasting preference from raw text ("too wordy"); optional `custom_instructions`.
- **Syllabus and guidelines:** uploaded files ingested as knowledge in the student's collection.
- **Shared collection:** lessons (one memory per signal per run_seq, with supporting runs), predictions (with hash), run summaries. Read with hybrid `/query` and `recency_bias` so the newest lessons count most.
- Merging a student's collection with `shared` in one `/query` is `[TEST]` — budget two queries.
- Whether hosted `query_graph` (RocketRide node) or MCP `hydradb_graph_query` runs Cypher, and which subset, is `[TEST]`.

## Open-source: how it works `[DOCS]`

- Storage on SlateDB over S3-compatible storage (a local directory in dev). `graph-node` serves queries and writes (Bolt + HTTP); `graph-indexer` builds traversal indexes in the background; reads fill gaps from the recent write log.
- Snapshot-consistent: each query reads one fixed point in time. Read modes: `causal` (default; can wait on a bookmark from an earlier write) and `strong` (refresh from storage before reading). Each statement commits on its own — no multi-statement transactions.
- Scopes: root namespace + up to 7 nested sub-namespaces + graph id, selected over Bolt by the session's database name (smoke script uses `default.scope1.<b64>.<b64>`; format undocumented). No preference primitives.

### Local run `[DOCS]`

```
mkdir -p hydradb-data/{store,cache}
printf '%s\n' 'local-development-token-32-bytes' > hydradb-data/auth-token   # ≥ 32 bytes
docker run --rm --user "$(id -u):$(id -g)" -p 7687:7687 -p 8443:8443 -p 9090:9090 \
 -v "$PWD/hydradb-data:/data" -e CLOUD_PROVIDER=local -e LOCAL_PATH=/data/store \
 -e GRAPH_NAMESPACE=default -e GRAPH_ID=default -e GRAPH_CELL_ID=cell-0 -e GRAPH_CELLS=cell-0 \
 -e GRAPH_NODE_ID=node-0 -e GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 \
 -e GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687 -e GRAPH_DATA_CACHE_DIR=/data/cache \
 -e GRAPH_AUTH_TOKEN_FILE=/data/auth-token -e GRAPH_ALLOW_PLAINTEXT=true \
 -e RUST_MIN_STACK=33554432 ghcr.io/hydra-db/hydradb:latest
```

- Bolt: Python `neo4j` driver, `bolt://127.0.0.1:7687`, `auth=("neo4j", TOKEN)`, `session(database="default")`; read mode via `consistency` in query metadata.
- HTTP: `POST :8443/v1/graphs/default/query`, Bearer token, `X-Graph-Namespace`, body `{cell_id, query, consistency}` (parameter field name not found).

### Cypher subset `[DOCS]` (cypher-compat.md)

| Feature | Status |
|---|---|
| MERGE | Matches on `id` only; no `ON CREATE`/`ON MATCH` → MERGE then SET |
| CREATE | Relationship paths only; nothing may follow it |
| SET / REMOVE / DELETE | Need a MATCH first |
| UNWIND batch writes | Only from a `$param` list of maps, only over Bolt, one directed hop and one hard-coded relationship type per batch |
| Variable-length paths | Must be bounded (`*1..3`) |
| OPTIONAL MATCH | Reads only |
| WITH | Pass-through only (no aliases/filtering) |
| WHERE | No `IN`, `CONTAINS`, `ENDS WITH`, `IS NULL` |
| Aggregates | `count`, `sum`, `avg`, `collect` only (no `min`/`max`) |
| Patterns | Directed only; one statement per request |
| Node ids | Non-negative integers |
| Property values | Integers, floats, booleans, strings only |
| Projections | `binding.property` or an aggregate (no `properties(n)`, no `RETURN *`) |
| Procedures | Only `CALL algo.SPpaths`, `algo.SSpaths`, `algo.MSpaths` (no APOC) |
| Indexes | Automatic property indexes; `CREATE INDEX`/constraints not documented |
| Vector / full-text | Not in open source |

Documented bulk-load forms:
- `UNWIND $rows AS row MERGE (n {id: row.vertex}) SET n:Source, n.x = row.x`
- `UNWIND $rows AS row MATCH (s:Entity {id: row.src}), (d:Entity {id: row.dst}) CREATE|MERGE (s)-[r:TYPE {id: row.rid}]->(d) SET r.p = row.p`
- Deletes: `UNWIND … MATCH … DELETE` or `DETACH DELETE`. Extra properties inside a MERGE pattern are rejected.

### Why Cognee can't write to it `[DOCS]`

Cognee's Neo4j adapter uses three APOC procedures, `CREATE CONSTRAINT`, `ON CREATE SET`, `SET n += $props`, UUID string ids and list properties — all rejected. Use the projector in `cognee.md` if the Cypher graph is built.

### Signature features worth showing (open source)

- `algo.MSpaths`: multi-hop paths from many sources at once (GraphBLAS-accelerated) — run from this term's topics to past exam problems (`maxLen: 3`) for the "why" view; path counts can be an extra signal.
- Snapshot bookmarks: write lessons, read them back consistently (`strong`).
- Lesson history as a `SUPERSEDES` chain; current lesson via `ORDER BY run_seq DESC LIMIT 1`.

## Gotchas

- HTTP writes return 500 on CREATE/DETACH DELETE with `:latest` (issue #196, opened 2026-09-10) → write over Bolt only.
- Intermittent Bolt decode errors in the JavaScript driver (issue #98) → retry; MERGE by id is idempotent.
- Without `RUST_MIN_STACK` the node crashes on the first query; `LOCAL_PATH` must exist; plain CREATE duplicates edges on re-run.
- Strings over ~32 KiB crash the query worker (issue #157) → keep problem text out.
- No transactions → a run can be left partly written; make writes idempotent.
- Similarity must be computed outside HydraDB.

## Smoke test (15 min)

Hosted (required):
1. `pip install "hydradb-sdk>=2,<3"`; create database `exam-oracle`.
2. In `collection=s1`: store one explicit preference (`infer:false`, fixed id) and one raw feedback line (`infer:true`); query both back; update the preference by id and confirm replacement.
3. Store one lesson in `shared`; query `shared` with `recency_bias`; try one query across `s1` + `shared`.
4. From RocketRide: call `get_schema` and `query_graph` once through the node to learn what it really accepts.

Open source (only if the "why" view is kept):
5. Start the container; readiness on 9090; over Bolt run the vertex and edge UNWIND batches; read back with `*1..2`, OPTIONAL MATCH and `algo.SSpaths`; write a two-version lesson chain and read the latest with `strong`; restart and re-read; try one HTTP CREATE to see whether #196 affects you; push one Cognee node through the projector.

**If it fails:** hosted `query_graph` has no Cypher → "why" view via Cognee or open source; cross-collection query fails → two queries; open-source writes fail → drop the Cypher graph (cut-list item 1).

## Sources

- https://github.com/hydra-db/hydradb (README, architecture.md, cypher-compat.md, scripts/runtime_smoke.sh, AGENTS.md, tags, issues #196, #157, #98)
- https://hydradb.com
- https://docs.hydradb.com/get-started/v2/quickstart
- https://docs.hydradb.com/llms.txt
- https://docs.hydradb.com/api-reference/v2/openapi.json
- https://docs.hydradb.com/plugins/mcp
- https://docs.rocketride.org/ (graph_hydradb node)
