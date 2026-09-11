-- Exam Oracle — ledger schema contract (owner: Person B)
--
-- IMPORTANT: hotdata SQL is READ-ONLY. This DDL is NOT executed in hotdata.
-- It is the column contract: every file loaded into the hotdata `ledger`
-- database must have exactly these columns, names and types.
--
-- How loads work (verified in hotdata-framework 0.14.0 source, 2026-09-11):
--   * HotdataClient.load_managed_table(database, table, file=<path>.parquet,
--     mode='replace'|'append'|'upsert'|'update'|'delete', key=[...])
--     REQUIRES PARQUET ("Managed table loads require a parquet file").
--     Write rows with pyarrow/pandas to .parquet, then load. (The CLI can also
--     load; CSV inline loads are a CLI/REST path, not this client.)
--   * Idempotency = key columns + mode 'upsert'. Declare keys when the
--     database is created: create_managed_database(tables=[...],
--     keys={table: [cols]}, expires_at=...). Keys per table are listed in
--     contracts/README.md ("Loading and querying").
--   * Inside a managed database, SQL must reference tables as
--     "default"."public"."<table>" and pass database=<db> to execute_sql.
--     Local DuckDB tests use bare table names; oracle/backend.py rewrites.
-- The local test backend (DuckDB) DOES execute this file, so keep it valid
-- DuckDB SQL.
--
-- Time ordering: see contracts/README.md. Leakage decisions use
-- (term_seq, session), never `date`.

-- ---------------------------------------------------------------- reference
CREATE TABLE courses (
  course              VARCHAR NOT NULL,   -- '6.003'
  title               VARCHAR,
  ocw_url             VARCHAR,
  published_term      VARCHAR NOT NULL,   -- term whose lectures/psets OCW publishes, e.g. '2011F'
  published_term_seq  INTEGER NOT NULL    -- 20113
);

CREATE TABLE topics (                     -- the FIXED topic list (course structure)
  course         VARCHAR NOT NULL,
  topic_id       VARCHAR NOT NULL,        -- 'T01'..'T20'
  topic          VARCHAR NOT NULL,        -- 'Fourier series'
  first_lecture  INTEGER,                 -- lecture range in the published term
  last_lecture   INTEGER
);

-- ---------------------------------------------------------------- loaded by A
CREATE TABLE lectures (
  course      VARCHAR NOT NULL,
  term        VARCHAR NOT NULL,           -- '2011F'
  term_seq    INTEGER NOT NULL,
  session     INTEGER NOT NULL,           -- lecture/session number in the term
  date        VARCHAR,                    -- ISO 'YYYY-MM-DD' if stated, else NULL
  lecture_n   INTEGER,
  title       VARCHAR,
  topic_id    VARCHAR                     -- one topic per lecture (from the topic list)
);

CREATE TABLE exams (                      -- one row per exam, including the sealed one
  course                VARCHAR NOT NULL,
  exam_id               VARCHAR NOT NULL, -- '6.003-final-2011F'
  exam_type             VARCHAR NOT NULL, -- quiz1 | quiz2 | quiz3 | midterm | final
  term                  VARCHAR NOT NULL,
  term_seq              INTEGER NOT NULL,
  session               INTEGER,          -- session at which it was given (NULL if unknown -> treat as end of term)
  date                  VARCHAR,
  total_points          DOUBLE,
  coverage_from_session INTEGER,          -- stated coverage window, NULL if not stated
  coverage_to_session   INTEGER,
  cumulative            BOOLEAN,          -- finals: TRUE if stated or assumed (log the assumption)
  sealed                BOOLEAN NOT NULL  -- TRUE for the reveal target; its items are NEVER loaded
);

CREATE TABLE exam_items (                 -- one row per (problem, topic); NEVER contains sealed exams
  course        VARCHAR NOT NULL,
  exam_id       VARCHAR NOT NULL,
  exam_type     VARCHAR NOT NULL,
  term          VARCHAR NOT NULL,
  term_seq      INTEGER NOT NULL,
  session       INTEGER,
  problem       VARCHAR NOT NULL,         -- '4' or '4b'
  points        DOUBLE,                   -- the problem's full points
  topic_id      VARCHAR NOT NULL,
  points_share  DOUBLE NOT NULL,          -- points / number of topics tagged on this problem
  tag_source    VARCHAR NOT NULL,         -- 'cognee' | 'human'
  text_clean    VARCHAR                   -- LaTeX-stripped text for BM25
);

CREATE TABLE homework_items (             -- BM25 (text) index lives on this table
  course      VARCHAR NOT NULL,
  term        VARCHAR NOT NULL,
  term_seq    INTEGER NOT NULL,
  session     INTEGER NOT NULL,           -- session at which the set was due
  hw_id       VARCHAR NOT NULL,
  topic_id    VARCHAR NOT NULL,
  text_clean  VARCHAR
);

CREATE TABLE homework_vec (               -- vector index ONLY (must be the sole index on its table)
  course      VARCHAR NOT NULL,
  hw_id       VARCHAR NOT NULL,
  text_clean  VARCHAR
);

CREATE TABLE echo_pairs (                 -- precomputed homework<->past-exam similarity (fused BM25+vector)
  course          VARCHAR NOT NULL,
  hw_id           VARCHAR NOT NULL,
  hw_term_seq     INTEGER NOT NULL,
  hw_session      INTEGER NOT NULL,
  exam_id         VARCHAR NOT NULL,
  problem         VARCHAR NOT NULL,
  exam_term_seq   INTEGER NOT NULL,
  exam_session    INTEGER,
  sim             DOUBLE NOT NULL         -- reciprocal-rank-fused score, higher = more similar
);

CREATE TABLE guidelines (                 -- professor's stated guidance, as numeric claims
  course               VARCHAR NOT NULL,
  guideline_id         VARCHAR NOT NULL,
  kind                 VARCHAR NOT NULL,  -- cumulative | emphasis_window | coverage | format
  applies_to_exam_type VARCHAR NOT NULL,  -- 'final', 'quiz1', ...
  from_session         INTEGER,
  to_session           INTEGER,
  share                DOUBLE,            -- claimed share of exam points in the window
  source_term          VARCHAR NOT NULL,
  source_term_seq      INTEGER NOT NULL,
  source_session       INTEGER NOT NULL,  -- when it was stated (visibility follows the ordering rule)
  text                 VARCHAR,           -- verbatim quote
  source_url           VARCHAR
);

CREATE TABLE course_loads (               -- one row per load-course run: the "cheaper" chart (written by A's summary step)
  load_id           VARCHAR NOT NULL,     -- 'load-6.003-20260911T213739Z'
  course            VARCHAR NOT NULL,
  mode              VARCHAR NOT NULL,     -- agent (the recorded first load) | replay (a Rote Play replay)
  started_at        VARCHAR NOT NULL,     -- ISO timestamp with offset
  finished_at       VARCHAR NOT NULL,
  seconds           DOUBLE NOT NULL,      -- wall clock, index start -> summary
  docs              INTEGER NOT NULL,     -- OCW documents downloaded (or cached)
  items             INTEGER NOT NULL,     -- exam problems + homework problems loaded
  degraded          INTEGER NOT NULL,     -- degraded checks/steps (exit 0 + warning)
  sealed_excluded   INTEGER NOT NULL,     -- skip-listed resources the index refused
  checks_first_try  BOOLEAN NOT NULL,     -- validate passed on its first attempt of this load
  agent_tokens      INTEGER               -- coding-agent reasoning tokens (agent loads); NULL when not measured
);

-- ---------------------------------------------------------------- written by B
CREATE TABLE predictions (                -- one row per (run, topic)
  run_id      VARCHAR NOT NULL,
  exam_id     VARCHAR NOT NULL,
  topic_id    VARCHAR NOT NULL,
  p           DOUBLE NOT NULL,
  rank        INTEGER NOT NULL,
  in_top_k    BOOLEAN NOT NULL,
  x1 DOUBLE, x2 DOUBLE, x3 DOUBLE, x4 DOUBLE, x5 DOUBLE, x6 DOUBLE, x7 DOUBLE,
  hash        VARCHAR NOT NULL,           -- SHA-256 of the canonical prediction JSON
  made_at     VARCHAR NOT NULL,           -- ISO timestamp with offset
  feature_set VARCHAR NOT NULL            -- full | exam_history
);

CREATE TABLE run_labels (                 -- written by score.py AFTER the seal; training labels for refit
  run_id      VARCHAR NOT NULL,
  topic_id    VARCHAR NOT NULL,
  y           INTEGER NOT NULL,           -- 1 if the topic appears on the target exam's answer key
  key_points  DOUBLE NOT NULL,            -- share of the exam's points on this topic (0 if absent)
  key_source  VARCHAR NOT NULL            -- human | machine
);

CREATE TABLE runs (                       -- one row per backtest; the dashboard reads this
  run_id            VARCHAR NOT NULL,
  run_seq           INTEGER NOT NULL,     -- global order in which runs were made
  course            VARCHAR NOT NULL,
  target_exam       VARCHAR NOT NULL,
  feature_set       VARCHAR NOT NULL,     -- full | exam_history
  cold_start        BOOLEAN NOT NULL,     -- TRUE for the blank-weights twin of the cold-start comparison
  k                 INTEGER NOT NULL,
  model_pts         DOUBLE,               -- share of exam points covered by the top-K (0..1); NULL until scored
  even_pts          DOUBLE,               -- baseline A (study evenly)
  lastexam_pts      DOUBLE,               -- baseline B (copy last exam)
  recall_k          DOUBLE,
  brier             DOUBLE,
  tokens            INTEGER,              -- model tokens spent on this run (0 for pure replays)
  seconds           DOUBLE,
  checks_first_try  BOOLEAN,
  leakage_ok        BOOLEAN NOT NULL,
  key_source        VARCHAR NOT NULL,     -- human | machine  (machine = scored against Cognee tags: not a fair test; label it)
  lessons_run_seq   INTEGER,              -- which lessons version the prediction used (NULL = cold start)
  hash              VARCHAR NOT NULL,
  made_at           VARCHAR NOT NULL
);

CREATE TABLE lessons (                    -- mirror of HydraDB `shared` lessons, for charts
  run_seq          INTEGER NOT NULL,      -- lessons version = run_seq that produced them
  signal           VARCHAR NOT NULL,      -- 'intercept' | 'x1'..'x7'
  weight           DOUBLE NOT NULL,
  statement        VARCHAR,
  supporting_runs  VARCHAR                -- ';'-separated run_ids
);

CREATE TABLE guideline_tests (
  guideline_id    VARCHAR NOT NULL,
  run_id          VARCHAR NOT NULL,
  predicted_share DOUBLE,
  observed_share  DOUBLE,
  verdict         VARCHAR NOT NULL        -- match | partial | miss
);

-- ---------------------------------------------------------------- written by C (via B's stores)
CREATE TABLE students (
  student_id    VARCHAR NOT NULL,         -- 's1'
  course        VARCHAR NOT NULL,
  exam_id       VARCHAR NOT NULL,
  exam_date     VARCHAR NOT NULL,         -- ISO date
  weekly_hours  DOUBLE NOT NULL
);
