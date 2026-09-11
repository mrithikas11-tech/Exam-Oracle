"""Exam Oracle — Person B's package (model & memory).

Foundation modules (other scripts build on these):
  config        paths, env (.env), backend/store selection, SEALED_DIR rule, validators, script conventions
  ordering      the time-ordering rule (term_seq, session) and feature_set
  backend       ledger + per-run databases: LocalDuckDBBackend (offline) | HotdataBackend (live)
  lessons_store the beta weights ("lessons"): LocalJSONLessonsStore | HydraDBLessonsStore

The contracts in contracts/ override kit/ wherever they differ.
"""

__version__ = "0.1.0"
