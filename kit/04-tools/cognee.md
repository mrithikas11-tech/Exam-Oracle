# Cognee — Structure layer

**Brief's role:** memory construction; "ingests raw, messy data … ECL pipeline (Extract, Cognify, Load)"; "Use Cognee's remember / recall primitives"; the front door for ingestion.
**Exam Oracle's role:** tag every problem, lecture and syllabus onto the course's fixed topic list; hold shared course datasets and private student datasets; turn scored sessions into lessons and student preferences; answer "why" questions.

Research date 2026-09-11 against Cognee **v1.5.4** (released 2026-09-04). Pin it.

## What it really is `[DOCS]`

- An open-source memory engine. You hand it data; an LLM extracts the entities/relations you define; results are stored in three stores: relational metadata (SQLite), vectors (LanceDB), graph (Ladybug — the docs page still says Kuzu; the source treats `ladybug` and `kuzu` as the same option).
- **Current v1 API:** `remember`, `recall`, `improve`, `forget`. The docs list `add`, `cognify`, `memify`, `search` under "Legacy Operations." The brief's "remember / recall" means this API.
- `remember(data)` without `session_id` runs `add()` → `cognify()` → `improve()` (because `self_improvement=True` by default). With `session_id`, it writes to a session cache that is bridged into the graph in the background.
- `recall` routes each question to a search type by fixed rules (not an LLM): "why" → `GRAPH_COMPLETION_COT`, "when" → `TEMPORAL`, otherwise `HYBRID_COMPLETION`.
- Keys: only `LLM_API_KEY` is required; embeddings reuse it unless `EMBEDDING_API_KEY` is set. Template defaults: `openai/gpt-5-mini` and `text-embedding-3-large`. Route extraction to a cheaper model with `LLM_EXTRACTION_MODEL`. Python 3.10–3.14.
- Surfaces: Python library, REST API, MCP server (`remember`/`recall`/`forget` tools), Cognee Cloud.

## Interfaces `[DOCS]`

- `remember(data, dataset_name="main_dataset", *, session_id, chunk_size, custom_prompt, self_improvement=True, dry_run=False, **kwargs)` — accepts `graph_model=`.
- `cognify(datasets, graph_model=KnowledgeGraph, chunk_size, custom_prompt, temporal_cognify=False, config, chunk_attachment, dry_run, ...)`.
- `add(data, dataset_name, node_set=[...], preferred_loaders, importance_weight=0.5, ...)`.
- `recall(query_text, query_type=None, *, datasets, dataset_ids, auto_route=True, node_name, session_id, feedback_influence, response_model, only_context, ...)`.
- `search` default `HYBRID_COMPLETION`; `SearchType` has 20 values incl. `GRAPH_COMPLETION`, `GRAPH_COMPLETION_COT`, `GRAPH_COMPLETION_CONTEXT_EXTENSION`, `TRIPLET_COMPLETION`, `CYPHER`, `NATURAL_LANGUAGE`, `TEMPORAL`, `CODING_RULES`, `CHUNKS_LEXICAL`, `AGENTIC_COMPLETION`, `SKILLS`.
- **Custom models:** `from cognee.low_level import DataPoint`; `metadata={"index_fields": [...], "identity_fields": [...]}`; a field typed as another DataPoint becomes an edge; duplicates merge only when `identity_fields` is set; the model is the LLM's structured-output schema.
- **Ontology:** `config={"ontology_config": {"ontology_resolver": RDFLibOntologyResolver(ontology_file=...), "ontology_mode": "strict" | "annotate"}}` or env `ONTOLOGY_FILE_PATH`. Fuzzy matching (difflib, 0.8 cutoff). `strict` drops unmatched nodes. The resolver accepts in-memory file objects; the ontology config is per `cognify` call; over REST an uploaded `.owl` is selected with `ontology_key`. (Docs say passing `ontology_file_path` to cognify raises an error; trust the docs over the stale docstring.)
- **Node sets:** tag at ingest with `node_set=[...]`, filter with `recall(node_name=[...])`.
- **Temporal:** `temporal_cognify=True` + `SearchType.TEMPORAL`; only calendar-anchored dates become timestamps; temporal tasks take no `graph_model` (so probably not combinable with our custom model) `[INFERRED]`.
- **Feedback:** `add_feedback(session_id, qa_id, feedback_text, feedback_score 1–5)`, then `improve(session_ids=[...])` writes `feedback_weight` onto nodes/edges and distils sessions "into entity-anchored lessons" tagged `session_learnings`. Retrieval parameter `feedback_influence` defaults to 0 (off).
- Extras: `dry_run` cost estimate; `CONTRADICTION_DETECTION`.

## Multi-user and preferences `[DOCS]`

- **Datasets are the privacy boundary.** Multi-user mode gives each dataset its own backend and enforces read/write/share/delete per dataset. It is on by default when storage providers support it (Ladybug and LanceDB do). A plain Neo4j provider breaks multi-user mode.
- Shared course knowledge: one dataset per course owned by a service user; grant students read with `authorized_give_permission_on_datasets`; pass shared datasets as `dataset_ids` ("dataset names only look for datasets owned by the current user").
- `recall(dataset_ids=[course, student])` accepts a list; whether results from two backends merge into one answer is `[TEST]`. `AGENTIC_COMPLETION` and `SKILLS` refuse multiple datasets.
- **Built-in preference pattern:** `improve()` runs `_update_user_preferences`; one internal `UserPreference` node per (user, dataset) holding up to 2,000 characters of preference text; rated turns move weights on `prefers` edges (with decay); stated preferences ("too wordy") are folded into the text. Off by default: `PERSONALIZATION_ENABLED=false`; when on it shifts ranking by at most `PERSONALIZATION_INFLUENCE` (default 0.3). The node never appears in retrieval output and changes ranking, not answer style → keep explicit style in our `StudentProfile` and pass it as the system prompt.
- **Deletion:** `forget(everything=True, user=student)` deletes that user's datasets; the same code clears the session cache with a "full prune", which may wipe other students' sessions `[TEST]`.
- Each dataset is its own Ladybug file → lock contention; keep the queue enabled. `DEFAULT_FEEDBACK_INFLUENCE > 0` pushes every hybrid search to graph completion.

## Graph backend reality — do NOT wire Cognee into open-source HydraDB `[DOCS]`

- Built-in graph providers: neo4j, postgres, ladybug/kuzu (local and remote), neptune, neptune_analytics, turso; community adapters: arcadedb, memgraph, networkx, pggraph, spanner, turbopuffer, turingdb. **No HydraDB adapter.**
- The Neo4j adapter runs `CREATE CONSTRAINT IF NOT EXISTS …`, `apoc.coll.toSet`, `apoc.create.addLabels`, `apoc.merge.relationship`, reads with `labels(n)`, `properties(n)`, `TYPE(r)`, `ID()`, and uses UUID ids and list properties. Open-source HydraDB rejects all of these (only `CALL algo.*`; integer ids; scalar properties; plain projections). The first write fails.
- A custom adapter means ~50 `GraphDBInterface` methods — too big for the day.
- **If the Cypher graph is wanted:** keep Cognee on Ladybug; after each load, read `get_graph_engine().get_graph_data()`, map UUIDs to integer ids, write to HydraDB with `UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Label, …` over Bolt (a ~100-line "projector").

## How Exam Oracle uses Cognee fully `[DESIGN]`

1. **Topic list first:** from the syllabus / calendar, build the course's 12–20 topics; create `Topic` DataPoints in code (`identity_fields=["course","name"]`) and an OWL file with rdflib passed as a file object with `ontology_mode="strict"`.
2. **Pre-split inputs:** one exam problem + its solution per item; one homework problem per item; one lecture per item. Chunking otherwise cuts problems in half.
3. **Metadata in code, LLM only for tagging:** course, term, exam, dates come from the loader; the LLM only maps problem → topics.
4. **Ingest:** `remember(item, dataset_name="course-<code>", graph_model=ExamProblem, node_set=[course, term, doctype], self_improvement=False)`; price first with `dry_run=True`.
5. **Backtests as sessions:** `recall(..., session_id=f"run-{n}", node_name=<training terms>, response_model=...)` for explanations; after scoring, `add_feedback` with a score derived from points covered; `improve(session_ids=[...])`.
6. **Students:** private `student-<id>` dataset with `StudentProfile` and their `Syllabus`; `PERSONALIZATION_ENABLED=true`; every plan chat is a session; feedback → `add_feedback` → `improve()`.
7. **Plan explanations:** `recall(dataset_ids=[course, student], query_text="why is <topic> likely on the final?")` → `GRAPH_COMPLETION_COT`.
8. From RocketRide: `tool_cognee` exposes `cognee.remember`, `cognee.recall`, `cognee.memory_status`; `base_url` default `http://localhost:8000`; agent-only (no data lanes).

## Syllabus ingestion recipe `[DESIGN]`

1. `remember(syllabus_pdf, dataset_name="student-<id>", graph_model=Syllabus, self_improvement=False)`.
2. Read extracted topics back; show them to the student to confirm.
3. Create Topic DataPoints in code with identity fields; build the course OWL; use strict mode when tagging problems.

## Gotchas

- Cost is per-chunk extraction; limiter budget 60 requests/minute (engages on errors/timeouts); ~2,000 chunks ≈ 30+ minutes `[INFERRED]`. Pre-split, use a cheaper extraction model, `self_improvement=False` in bulk, start course 1 by hour 1.
- Default PDF loader (pypdf) garbles math; alternatives: `AdvancedPdfLoader` (needs poppler + tesseract) or `cognee[docling]`. Topic tagging tolerates garbling.
- Ladybug/Kuzu file locks: one Cognee process only; RocketRide and the loader both call that one server.
- Docs lag the source (Kuzu vs Ladybug; `ontology_file_path`). When in doubt, read the source at the pinned tag.

## Smoke test (15 min)

1. Python 3.12 venv; `pip install "cognee[docs]==1.5.4"`; set `LLM_API_KEY`.
2. `forget(everything=True)`; define `Topic(identity_fields=["name"])` and `ExamProblem(text, topics: list[Topic])`.
3. `remember` two problems with `node_set=["6.003","2009F","exam"]`, the custom model, a prompt listing five topics, `dry_run=True`; then for real.
4. `get_graph_data()`: every Topic on the list; a shared topic is one node.
5. Multi-user: create `admin`, `s1`, `s2`; `admin` remembers "Final is cumulative" into a course dataset; `s1`/`s2` remember preferences into their own datasets; grant course read to both; `recall(user=s1, dataset_ids=[course, s1])` returns both sources; `s1` recalling `s2` raises `PermissionDeniedError`.
6. Feedback: `PERSONALIZATION_ENABLED=true`; `recall` with `session_id`; `add_feedback(feedback_text="too wordy", feedback_score=2)`; `improve(session_ids=[...])`; preference text changed (`load_preference_text()`).
7. `forget(everything=True, user=s2)`; confirm `s1`'s data and sessions survive.
8. One real MIT PDF through pypdf; look at the math.

**If it fails:** cross-dataset merge fails → two recalls and merge in the agent. Personalization invisible → rely on HydraDB's inferred preference for the demo. Forget prunes others → exclude deletion from the demo.

## Sources

- https://github.com/topoteretes/cognee (remember.py, recall.py, improve.py, cognify.py, add.py, search.py, SearchType.py, .env.template, graph config, neo4j_driver/adapter.py, graph_db_interface.py, get_graph_engine.py, use_graph_adapter.py, DataPoint.py, session.py, user_preferences/, base_config.py, forget.py, RDFLibOntologyResolver.py, get_cognify_router.py, examples/demos/permissions/data_access_control_example.py, releases)
- https://docs.cognee.ai/core-concepts/main-operations/recall
- https://docs.cognee.ai/guides/custom-graph-model
- https://docs.cognee.ai/core-concepts/further-concepts/ontologies
- https://docs.cognee.ai/guides/feedback-system
- https://docs.cognee.ai/guides/time-awareness
- https://docs.cognee.ai/core-concepts/further-concepts/loaders
- https://docs.cognee.ai/core-concepts/further-concepts/node-sets
- https://docs.cognee.ai/core-concepts/multi-user-mode/multi-user-mode-overview
- https://docs.cognee.ai/setup-configuration/permissions
- https://docs.cognee.ai/setup-configuration/graph-stores
- https://github.com/topoteretes/cognee-community/tree/main/packages/graph
