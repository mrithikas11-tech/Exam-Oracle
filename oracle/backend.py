"""Ledger and per-run databases behind one API: LocalDuckDBBackend (offline) and HotdataBackend (live).

Spec: contracts/README.md ("Loading and querying": Parquet loads, table keys, "default"."public"."<table>"),
contracts/ledger-schema.sql (the column contract), kit/04-tools/hotdata.md (read-only SQL; one throwaway
database per backtest with an expiry), kit/02-product/data-sources-and-sealing.md (sealing rule 2).

SQL conventions (identical on both backends):
  * Tables are {{table}} placeholders (contract tables only), rendered by Backend.table_ref():
    local -> bare name; hotdata -> "default"."public"."<table>".
  * Parameters are named: $name. DuckDB binds them natively. hotdata has no bind API, so values are rendered
    as literals, and ONLY through sql_literal(): NULL, bools, ints, finite floats, and strings matching
    ^[A-Za-z0-9._-]+$. Local mode runs the same validator, so SQL that works offline also renders live.
  * query() is read-only on both (hotdata SQL is read-only; the local connection is opened read_only).
  * Writes are loads: load_table(db, table, rows, mode) with mode replace | append | upsert. Rows are first
    coerced to the contract columns and types (to_contract_table), then inserted from Arrow in one
    transaction (local) or written to Parquet and loaded (hotdata).
"""
from __future__ import annotations

import csv
import math
import numbers
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, NamedTuple, Protocol, Sequence, Union, runtime_checkable

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from oracle import config

# Key columns per table: contracts/README.md "Loading and querying". run_labels was added 2026-09-11
# (score.py writes it after the seal; Rote replays must be idempotent). homework_vec has no key in the
# contract: it is loaded with replace and carries only the vector index.
TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "courses": ("course",),
    "topics": ("course", "topic_id"),
    "lectures": ("course", "term", "session"),
    "exams": ("exam_id",),
    "exam_items": ("exam_id", "problem", "topic_id"),
    "homework_items": ("hw_id", "topic_id"),
    "echo_pairs": ("hw_id", "exam_id", "problem"),
    "guidelines": ("guideline_id",),
    "predictions": ("run_id", "topic_id"),
    "run_labels": ("run_id", "topic_id"),
    "runs": ("run_id",),
    "lessons": ("run_seq", "signal"),
    "guideline_tests": ("guideline_id", "run_id"),
    "students": ("student_id",),
}

# Tables a per-run database holds (data-model.md "run-<id>", plus the course structure and exam metadata
# the signal queries join against). Callers filter every row with ordering.visible_sql() before loading.
RUN_TABLES: tuple[str, ...] = ("courses", "topics", "exams", "exam_items", "lectures",
                               "homework_items", "homework_vec", "echo_pairs", "guidelines")
LOAD_MODES = ("replace", "append", "upsert")
RUN_DB_PREFIX = "run-"
MAX_RUN_DB_HOURS = 168.0

_ARROW_TYPES: dict[str, pa.DataType] = {"VARCHAR": pa.string(), "INTEGER": pa.int32(), "BIGINT": pa.int64(),
                                        "DOUBLE": pa.float64(), "BOOLEAN": pa.bool_()}
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# String literals, quoted identifiers and comments are matched first so a $ inside them is never a parameter.
_SQL_SCAN_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/"
                          r"|\$([A-Za-z_][A-Za-z0-9_]*)|\$(\d+)", re.S)
_LOCK_WAIT_S = 15.0


# ------------------------------------------------------------------ the column contract
class Column(NamedTuple):
    name: str
    type: str       # DuckDB type from the DDL: VARCHAR | INTEGER | DOUBLE | BOOLEAN
    nullable: bool


@lru_cache(maxsize=4)
def _parse_schema(path: str, _mtime_ns: int) -> dict[str, tuple[Column, ...]]:
    ddl = Path(path).read_text(encoding="utf-8")
    order = re.findall(r"(?im)^\s*CREATE\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)", ddl)
    con = duckdb.connect(":memory:")
    try:
        con.execute(ddl)
        rows = con.execute("SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns "
                           "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position").fetchall()
    finally:
        con.close()
    found: dict[str, list[Column]] = {}
    for table, name, dtype, nullable in rows:
        if dtype not in _ARROW_TYPES:
            raise ValueError(f"ledger-schema.sql: unsupported type {dtype} for {table}.{name}")
        found.setdefault(table, []).append(Column(name, dtype, nullable == "YES"))
    if set(order) != set(found):
        raise ValueError("ledger-schema.sql: could not match CREATE TABLE statements to the parsed tables")
    return {table: tuple(found[table]) for table in order}


def ledger_schema() -> dict[str, tuple[Column, ...]]:
    """{table: columns} parsed from contracts/ledger-schema.sql by DuckDB itself (exact types), in file order."""
    path = config.LEDGER_SCHEMA_PATH
    if not path.is_file():
        raise FileNotFoundError(f"ledger schema contract not found at {path} (set ORACLE_REPO_ROOT?)")
    return _parse_schema(str(path), path.stat().st_mtime_ns)


def ledger_tables() -> tuple[str, ...]:
    return tuple(ledger_schema())


def columns(table: str) -> tuple[Column, ...]:
    """Contract columns of `table`; ValueError for a table that is not in ledger-schema.sql."""
    schema = ledger_schema()
    if table not in schema:
        raise ValueError(f"unknown ledger table {table!r}")
    return schema[table]


def arrow_schema(table: str) -> pa.Schema:
    """Arrow/Parquet schema of a contract table (VARCHAR->string, INTEGER->int32, DOUBLE->float64, BOOLEAN->bool)."""
    return pa.schema([pa.field(c.name, _ARROW_TYPES[c.type]) for c in columns(table)])


def _q(ident: str) -> str:
    if not _IDENT_RE.fullmatch(ident):
        raise ValueError(f"invalid SQL identifier {ident!r}")
    return f'"{ident}"'


def _check_columns(table: str, got: Sequence[str], want: Sequence[str]) -> None:
    got = list(got)
    duplicated = sorted({c for c in got if got.count(c) > 1})
    missing = [c for c in want if c not in got]
    extra = [c for c in got if c not in want]
    if duplicated or missing or extra:
        raise ValueError(f"{table}: columns do not match the contract "
                         f"(missing={missing}, extra={extra}, duplicated={duplicated})")


Rows = Union[pd.DataFrame, pa.Table, Iterable[Mapping[str, Any]]]


def to_contract_table(table: str, rows: Rows) -> pa.Table:
    """Coerce rows (DataFrame, Arrow table, or iterable of dicts) to exactly the contract columns and types of
    `table`. ValueError on missing/extra columns, values that do not cast, or NULLs in NOT NULL columns."""
    cols = columns(table)
    names = [c.name for c in cols]
    schema = arrow_schema(table)
    try:
        if isinstance(rows, pa.Table):
            _check_columns(table, rows.column_names, names)
            tbl = rows.select(names).cast(schema)
        else:
            if isinstance(rows, pd.DataFrame):
                frame = rows
            else:
                records = [dict(r) for r in rows]
                frame = pd.DataFrame.from_records(records) if records else pd.DataFrame(columns=names)
            _check_columns(table, [str(c) for c in frame.columns], names)
            if len(frame) == 0:
                return schema.empty_table()
            tbl = pa.Table.from_pandas(frame[names], schema=schema, preserve_index=False)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError) as exc:
        raise ValueError(f"{table}: rows do not fit the contract types: {exc}") from exc
    tbl = tbl.replace_schema_metadata(None)
    for c in cols:
        nulls = tbl.column(c.name).null_count
        if nulls and not c.nullable:
            raise ValueError(f"{table}.{c.name} is NOT NULL but {nulls} row(s) are null")
    return tbl


def read_contract_csv(path: str | os.PathLike, table: str) -> pd.DataFrame:
    """Read a CSV whose header names exactly the contract columns of `table`, typed from the DDL (DuckDB
    read_csv with explicit types; nullstr='' so an empty cell is NULL). Columns come back in contract order."""
    cols = columns(table)
    with open(path, newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh), [])
    _check_columns(table, header, [c.name for c in cols])
    select = ", ".join(_q(c.name) for c in cols)
    con = duckdb.connect(":memory:")
    try:
        return con.execute(
            f"SELECT {select} FROM read_csv($path, header = true, delim = ',', quote = '\"', escape = '\"', "
            f"nullstr = '', types = $types)",
            {"path": str(path), "types": {c.name: c.type for c in cols}},
        ).df()
    finally:
        con.close()


# ------------------------------------------------------------------ SQL rendering and parameters
def render_tables(sql: str, table_ref: Callable[[str], str]) -> str:
    """Replace every {{table}} placeholder with table_ref(table); unknown tables raise ValueError."""
    rendered = _PLACEHOLDER_RE.sub(lambda m: table_ref(m.group(1)), sql)
    if "{{" in rendered:
        raise ValueError("malformed {{table}} placeholder in SQL")
    return rendered


def _scan_params(sql: str) -> list[str]:
    names: list[str] = []
    for match in _SQL_SCAN_RE.finditer(sql):
        if match.group(2):
            raise ValueError(f"positional parameter ${match.group(2)} is not supported; use $name")
        if match.group(1):
            names.append(match.group(1))
    return names


def param_names(sql: str) -> list[str]:
    """Distinct $name parameters used in `sql` (string literals, quoted identifiers and comments skipped)."""
    return list(dict.fromkeys(_scan_params(sql)))


def _plain(value: object) -> object:
    return value.item() if isinstance(value, np.generic) else value


def sql_literal(value: object) -> str:
    """Render one parameter value as a SQL literal through a strict validator (hotdata has no bind API).
    Allowed: None, bool, integers, finite floats, strings matching ^[A-Za-z0-9._-]+$. Anything else raises."""
    value = _plain(value)
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"non-finite SQL parameter {value!r}")
        return repr(number)
    if isinstance(value, str):
        return "'" + config.validate_id(value, what="SQL string parameter") + "'"
    raise TypeError(f"unsupported SQL parameter type {type(value).__name__}")


def render_params(sql: str, params: Mapping[str, object] | None = None) -> str:
    """Substitute every $name (outside strings/comments) with sql_literal(params[name]) — the hotdata path."""
    params = params or {}

    def substitute(match: re.Match[str]) -> str:
        if match.group(2):
            raise ValueError(f"positional parameter ${match.group(2)} is not supported; use $name")
        name = match.group(1)
        if name is None:
            return match.group(0)
        if name not in params:
            raise ValueError(f"missing SQL parameter ${name}")
        return sql_literal(params[name])

    return _SQL_SCAN_RE.sub(substitute, sql)


def bind_params(sql: str, params: Mapping[str, object] | None = None) -> dict[str, object]:
    """Validated {name: value} for the $names used in `sql` — the DuckDB path. Unused params are dropped
    (DuckDB rejects extras); every value passes sql_literal() so offline and live accept the same inputs."""
    params = params or {}
    bound: dict[str, object] = {}
    for name in param_names(sql):
        if name not in params:
            raise ValueError(f"missing SQL parameter ${name}")
        sql_literal(params[name])
        bound[name] = _plain(params[name])
    return bound


# ------------------------------------------------------------------ handles and the protocol
@dataclass(frozen=True)
class DbHandle:
    """A ledger or run database; JSON-safe (to_dict / from_dict) so Rote steps can pass it along.

    Local handles are always re-derived from `name` (path is informational). hotdata handles carry the
    database id and default connection id: pass the handle (or the id) between steps, not the name —
    hotdata names are free-form labels and not unique."""
    name: str
    backend: str
    path: str | None = None
    database_id: str | None = None
    connection_id: str | None = None
    expires_at: str | None = None
    tables: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tables"] = list(self.tables)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DbHandle":
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown DbHandle fields: {sorted(unknown)}")
        kwargs = dict(data)
        kwargs["tables"] = tuple(kwargs.get("tables") or ())
        return cls(**kwargs)


DbRef = Union[DbHandle, str]


def run_db_name(run_id: str) -> str:
    """'r3-FX.101-quiz1-2022F' -> 'run-r3-FX.101-quiz1-2022F'."""
    return RUN_DB_PREFIX + config.validate_id(run_id, what="run_id")


@runtime_checkable
class Backend(Protocol):
    name: str
    ledger_name: str

    def table_ref(self, table: str) -> str: ...
    def ledger(self) -> DbHandle: ...
    def ensure_ledger(self) -> DbHandle: ...
    def load_table(self, db: DbRef, table: str, df: Rows, mode: str = "append",
                   key: Sequence[str] | None = None) -> int: ...
    def query(self, db: DbRef, sql: str, params: Mapping[str, object] | None = None) -> pd.DataFrame: ...
    def create_run_db(self, run_id: str, expires_in_hours: float = 2,
                      tables: Sequence[str] = RUN_TABLES) -> DbHandle: ...
    def drop_run_db(self, handle: DbRef) -> None: ...


def _load_key(table: str, mode: str, key: Sequence[str] | None) -> tuple[str, ...] | None:
    if mode not in LOAD_MODES:
        raise ValueError(f"mode must be one of {LOAD_MODES}, got {mode!r}")
    names = {c.name for c in columns(table)}
    if mode != "upsert":
        return None
    key_cols = tuple(key) if key else TABLE_KEYS.get(table, ())
    if not key_cols:
        raise ValueError(f"upsert into {table} needs key columns (none declared in TABLE_KEYS)")
    unknown = [c for c in key_cols if c not in names]
    if unknown:
        raise ValueError(f"upsert key columns {unknown} are not columns of {table}")
    return key_cols


def _check_unique(table: str, tbl: pa.Table, key_cols: tuple[str, ...] | None) -> None:
    if key_cols and tbl.num_rows:
        duplicated = tbl.select(list(key_cols)).to_pandas().duplicated()
        if duplicated.any():
            raise ValueError(f"{table}: {int(duplicated.sum())} duplicate key row(s) on {key_cols} in one upsert batch")


def _check_tables(tables: Sequence[str]) -> tuple[str, ...]:
    chosen = tuple(dict.fromkeys(tables))
    if not chosen:
        raise ValueError("a run database needs at least one table")
    for table in chosen:
        columns(table)
    return chosen


def _expiry_iso(hours: float) -> str:
    if not 0 < float(hours) <= MAX_RUN_DB_HOURS:
        raise ValueError(f"expires_in_hours must be in (0, {MAX_RUN_DB_HOURS:g}]")
    return (datetime.now(timezone.utc) + timedelta(hours=float(hours))).strftime("%Y-%m-%dT%H:%M:%SZ")


def _refuse_non_run(name: str, ledger_name: str) -> None:
    if not name.startswith(RUN_DB_PREFIX) or name == ledger_name:
        raise ValueError(f"refusing to drop {name!r}: only {RUN_DB_PREFIX}<run_id> databases can be dropped")


# ------------------------------------------------------------------ local (offline) backend
@contextmanager
def _duckdb(path: Path, *, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a DuckDB file, waiting briefly while another process holds its write lock."""
    deadline = time.monotonic() + _LOCK_WAIT_S
    while True:
        try:
            con = duckdb.connect(str(path), read_only=read_only)
            break
        except duckdb.IOException as exc:
            if "lock" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(0.2)
    try:
        yield con
    finally:
        con.close()


def _create_tables(con: duckdb.DuckDBPyConnection, tables: Iterable[str]) -> None:
    """CREATE TABLE IF NOT EXISTS from the parsed contract (same names, types and NOT NULLs as the DDL)."""
    for table in tables:
        cols = ", ".join(f"{_q(c.name)} {c.type}{'' if c.nullable else ' NOT NULL'}" for c in columns(table))
        con.execute(f"CREATE TABLE IF NOT EXISTS {_q(table)} ({cols})")


def _check_drift(con: duckdb.DuckDBPyConnection, tables: Iterable[str], where: Path) -> None:
    rows = con.execute("SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns "
                       "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position").fetchall()
    have: dict[str, list[Column]] = {}
    for table, name, dtype, nullable in rows:
        have.setdefault(table, []).append(Column(name, dtype, nullable == "YES"))
    for table in tables:
        if tuple(have.get(table, ())) != columns(table):
            raise RuntimeError(f"{where}: table {table} no longer matches contracts/ledger-schema.sql; "
                               f"delete the file to rebuild it")


def _remove_duckdb_files(path: Path) -> None:
    for candidate in (path, path.with_name(path.name + ".wal")):
        candidate.unlink(missing_ok=True)


class LocalDuckDBBackend:
    """Offline backend: one DuckDB file per database under LOCAL_DIR —
    <LOCAL_DIR>/<ledger>.duckdb and <LOCAL_DIR>/runs/run-<run_id>.duckdb. Local run databases do not
    expire; drop_run_db() deletes them and create_run_db() always starts from an empty file."""

    name = "local"

    def __init__(self, local_dir: str | os.PathLike | None = None, ledger_name: str | None = None) -> None:
        if local_dir is None or ledger_name is None:
            settings = config.get_settings()
            local_dir = settings.local_dir if local_dir is None else local_dir
            ledger_name = settings.hotdata_ledger_db if ledger_name is None else ledger_name
        self.local_dir = Path(local_dir).expanduser().resolve()
        self.ledger_name = config.validate_id(ledger_name, what="ledger name")
        if self.ledger_name.startswith(RUN_DB_PREFIX):
            raise ValueError(f"ledger name may not start with {RUN_DB_PREFIX!r}")

    def table_ref(self, table: str) -> str:
        columns(table)
        return table

    def path_for(self, name: str) -> Path:
        config.validate_id(name, what="database name")
        if name.startswith(RUN_DB_PREFIX):
            return self.local_dir / "runs" / f"{name}.duckdb"
        return self.local_dir / f"{name}.duckdb"

    def _name(self, db: DbRef) -> str:
        if isinstance(db, DbHandle):
            if db.backend != self.name:
                raise ValueError(f"a {db.backend!r} handle was passed to the local backend")
            return db.name
        return db

    def _existing(self, db: DbRef) -> Path:
        path = self.path_for(self._name(db))
        if not path.is_file():
            raise FileNotFoundError(f"database {self._name(db)!r} does not exist at {path} "
                                    f"(call ensure_ledger() or create_run_db() first)")
        return path

    def ledger(self) -> DbHandle:
        return DbHandle(name=self.ledger_name, backend=self.name, path=str(self.path_for(self.ledger_name)),
                        tables=ledger_tables())

    def ensure_ledger(self) -> DbHandle:
        """Create <LOCAL_DIR>/<ledger>.duckdb with every contract table that is missing; fail on schema drift."""
        path = self.path_for(self.ledger_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _duckdb(path) as con:
            _create_tables(con, ledger_tables())
            _check_drift(con, ledger_tables(), path)
        return self.ledger()

    def load_table(self, db: DbRef, table: str, df: Rows, mode: str = "append",
                   key: Sequence[str] | None = None) -> int:
        """Load rows into `table` in one transaction. replace = delete all then insert; append = insert;
        upsert = delete rows whose key matches an incoming row, then insert (key defaults to TABLE_KEYS).
        Returns the number of rows sent."""
        key_cols = _load_key(table, mode, key)
        tbl = to_contract_table(table, df)
        _check_unique(table, tbl, key_cols)
        path = self._existing(db)
        target = _q(table)
        cols = ", ".join(_q(c) for c in tbl.column_names)
        with _duckdb(path) as con:
            present = con.execute("SELECT count(*) FROM information_schema.tables "
                                  "WHERE table_schema = 'main' AND table_name = $t", {"t": table}).fetchone()
            if not present or not present[0]:
                raise ValueError(f"table {table} does not exist in database {self._name(db)!r}")
            con.register("_oracle_incoming", tbl)
            try:
                con.execute("BEGIN TRANSACTION")
                try:
                    if mode == "replace":
                        con.execute(f"DELETE FROM {target}")
                    elif mode == "upsert":
                        match = " AND ".join(f"{target}.{_q(k)} = s.{_q(k)}" for k in key_cols or ())
                        con.execute(f"DELETE FROM {target} WHERE EXISTS "
                                    f"(SELECT 1 FROM _oracle_incoming AS s WHERE {match})")
                    con.execute(f"INSERT INTO {target} ({cols}) SELECT {cols} FROM _oracle_incoming")
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
            finally:
                con.unregister("_oracle_incoming")
        return tbl.num_rows

    def query(self, db: DbRef, sql: str, params: Mapping[str, object] | None = None) -> pd.DataFrame:
        """Run read-only SQL ({{table}} placeholders, $name params) and return a DataFrame."""
        rendered = render_tables(sql, self.table_ref)
        binds = bind_params(rendered, params)
        path = self._existing(db)
        with _duckdb(path, read_only=True) as con:
            cursor = con.execute(rendered, binds) if binds else con.execute(rendered)
            return cursor.df()

    def create_run_db(self, run_id: str, expires_in_hours: float = 2,
                      tables: Sequence[str] = RUN_TABLES) -> DbHandle:
        """A fresh, empty database run-<run_id> holding `tables` (contract schema). Replaces any earlier file."""
        name = run_db_name(run_id)
        chosen = _check_tables(tables)
        expires = _expiry_iso(expires_in_hours)
        path = self.path_for(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        _remove_duckdb_files(path)
        with _duckdb(path) as con:
            _create_tables(con, chosen)
        return DbHandle(name=name, backend=self.name, path=str(path), expires_at=expires, tables=chosen)

    def drop_run_db(self, handle: DbRef) -> None:
        """Delete a run database (idempotent). The ledger and any non-run name are refused."""
        name = self._name(handle)
        _refuse_non_run(name, self.ledger_name)
        _remove_duckdb_files(self.path_for(name))


# ------------------------------------------------------------------ hotdata (live) backend
class HotdataBackend:
    """Live backend on hotdata managed databases (hotdata-framework 0.14.0). The client comes from
    HotdataClient.from_env() (HOTDATA_API_KEY, HOTDATA_WORKSPACE, HOTDATA_API_URL) and is created on first
    use, so constructing this class never touches the network. Tests only ever inject a fake client.
    Every SDK call is marked LIVE [TEST] until the G0 smoke test (kit/04-tools/hotdata.md) confirms it."""

    name = "hotdata"

    def __init__(self, client: Any | None = None, ledger_name: str | None = None) -> None:
        self._client = client
        self.ledger_name = config.validate_id(
            ledger_name if ledger_name is not None else config.get_settings().hotdata_ledger_db, what="ledger name")
        if self.ledger_name.startswith(RUN_DB_PREFIX):
            raise ValueError(f"ledger name may not start with {RUN_DB_PREFIX!r}")
        self._databases: dict[str, Any] = {}

    @property
    def client(self) -> Any:
        if self._client is None:
            config.get_settings()  # loads <repo>/.env once
            from hotdata_framework import HotdataClient
            self._client = HotdataClient.from_env()  # LIVE [TEST] raises RuntimeError without HOTDATA_API_KEY
        return self._client

    def table_ref(self, table: str) -> str:
        columns(table)
        return f'"default"."public"."{table}"'

    def _managed(self, db: DbRef) -> Any:
        from hotdata_framework import ManagedDatabase
        if isinstance(db, DbHandle):
            if db.backend != self.name:
                raise ValueError(f"a {db.backend!r} handle was passed to the hotdata backend")
            if db.database_id and db.connection_id:
                return ManagedDatabase(id=db.database_id, description=db.name, default_connection_id=db.connection_id)
            ref = db.database_id or db.name
        else:
            ref = db
        config.validate_id(ref, what="hotdata database")
        if ref not in self._databases:
            # LIVE [TEST] id lookup first, then the first database whose name matches
            self._databases[ref] = self.client.resolve_managed_database(ref)
        return self._databases[ref]

    def ledger(self) -> DbHandle:
        """Handle by name, resolved on first use. Once the ledger exists, set HOTDATA_LEDGER_DB to its id."""
        return DbHandle(name=self.ledger_name, backend=self.name, tables=ledger_tables())

    def ensure_ledger(self) -> DbHandle:
        """Find the permanent ledger database, or create it with every contract table and TABLE_KEYS."""
        try:
            db = self.client.resolve_managed_database(self.ledger_name)  # LIVE [TEST]
        except KeyError:
            db = self.client.create_managed_database(  # LIVE [TEST] permanent: no expires_at
                self.ledger_name, tables=list(ledger_tables()),
                keys={table: list(key) for table, key in TABLE_KEYS.items()})
        self._databases[self.ledger_name] = db
        return DbHandle(name=self.ledger_name, backend=self.name, database_id=db.id,
                        connection_id=db.default_connection_id, tables=ledger_tables())

    def load_table(self, db: DbRef, table: str, df: Rows, mode: str = "append",
                   key: Sequence[str] | None = None) -> int:
        """Coerce rows to the contract, write a temporary .parquet, and load_managed_table() it.
        Returns the number of rows sent."""
        key_cols = _load_key(table, mode, key)
        tbl = to_contract_table(table, df)
        _check_unique(table, tbl, key_cols)
        target = self._managed(db)
        with tempfile.TemporaryDirectory(prefix="oracle-load-") as tmp:
            parquet_path = os.path.join(tmp, f"{table}.parquet")
            pq.write_table(tbl, parquet_path)
            self.client.load_managed_table(  # LIVE [TEST]
                target, table, file=parquet_path, mode=mode, key=list(key_cols) if key_cols else None)
        return tbl.num_rows

    def query(self, db: DbRef, sql: str, params: Mapping[str, object] | None = None) -> pd.DataFrame:
        """Render {{table}} and $params (validated literals) and run it with execute_sql(database=...)."""
        rendered = render_params(render_tables(sql, self.table_ref), params)
        result = self.client.execute_sql(rendered, database=self._managed(db))  # LIVE [TEST]
        if getattr(result, "error_message", None):
            raise RuntimeError(f"hotdata query failed: {result.error_message}")
        frame = result.to_pandas()
        if getattr(result, "warning", None):
            frame.attrs["hotdata_warning"] = result.warning  # LIVE [TEST] e.g. a truncated preview
        return frame

    def create_run_db(self, run_id: str, expires_in_hours: float = 2,
                      tables: Sequence[str] = RUN_TABLES) -> DbHandle:
        """A throwaway managed database run-<run_id> with `tables` declared (keys from TABLE_KEYS) and an
        RFC 3339 expires_at. Replays create a new database each time; the old one expires on its own."""
        name = run_db_name(run_id)
        chosen = _check_tables(tables)
        expires = _expiry_iso(expires_in_hours)
        db = self.client.create_managed_database(  # LIVE [TEST]
            name, tables=list(chosen), keys={t: list(TABLE_KEYS[t]) for t in chosen if t in TABLE_KEYS},
            expires_at=expires)
        self._databases[db.id] = db
        return DbHandle(name=name, backend=self.name, database_id=db.id, connection_id=db.default_connection_id,
                        expires_at=expires, tables=chosen)

    def drop_run_db(self, handle: DbRef) -> None:
        """Delete a run database. The ledger and any database not named run-<run_id> are refused."""
        if isinstance(handle, DbHandle):
            _refuse_non_run(handle.name, self.ledger_name)
        target = self._managed(handle)
        if not isinstance(handle, DbHandle):
            _refuse_non_run(target.description or "", self.ledger_name)
        self.client.delete_managed_database(target)  # LIVE [TEST]
        self._databases = {k: v for k, v in self._databases.items() if v is not target}


def get_backend(settings: config.Settings | None = None) -> Backend:
    """The backend selected by ORACLE_BACKEND (local by default)."""
    s = settings or config.get_settings()
    if s.backend == "hotdata":
        return HotdataBackend(ledger_name=s.hotdata_ledger_db)
    return LocalDuckDBBackend(s.local_dir, s.hotdata_ledger_db)
