"""python -m oracle.fixture_ledger --replace

(Re)build the LOCAL ledger from the synthetic fixture course FX.101: every contract table is replaced with the rows
of contracts/fixtures/<table>.csv (typed from the DDL), or emptied when there is no CSV. For the offline demo and the
tests only (contracts/README.md: the fixtures are small synthetic data "until real data lands").

--replace is required because this wipes EVERY table of the local ledger under ORACLE_LOCAL_DIR; the hotdata
backend is refused. The fixture ledger includes three fake `runs` rows (FAKEHASH1..3, for C's dashboard); a
backtest of the same run_id replaces its row.

Prints ONE JSON object {ok, ledger, rows: {table: n}}. Exit 0 ok, 1 hard fault.
"""
from __future__ import annotations

import sys
from typing import Any

from oracle import config
from oracle.backend import Backend, DbHandle, get_backend, ledger_tables, read_contract_csv


def load_fixture_ledger(backend: Backend | None = None) -> tuple[Backend, DbHandle, dict[str, int]]:
    """Replace every contract table of the local ledger with its fixture CSV (or no rows). Returns
    (backend, ledger handle, rows loaded per table)."""
    be = backend if backend is not None else get_backend()
    if be.name != "local":
        raise config.ConfigError("fixture ledgers are built on the local backend only (ORACLE_BACKEND=local)")
    handle = be.ensure_ledger()
    counts: dict[str, int] = {}
    for table in ledger_tables():
        csv_path = config.FIXTURES_DIR / f"{table}.csv"
        rows: Any = read_contract_csv(csv_path, table) if csv_path.is_file() else []
        counts[table] = be.load_table(handle, table, rows, mode="replace")
    return be, handle, counts


def main(argv: list[str] | None = None) -> int:
    parser = config.ScriptParser(prog="python -m oracle.fixture_ledger",
                                 description="Rebuild the LOCAL ledger from contracts/fixtures (synthetic FX.101).")
    parser.add_argument("--replace", action="store_true", required=True,
                        help="confirm: every table of the local ledger is replaced")
    parser.parse_args(argv)
    try:
        _, handle, counts = load_fixture_ledger()
    except Exception as exc:  # CLI boundary: one JSON object, hard-fault lane
        config.emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return config.EXIT_HARD
    config.emit({"ok": True, "ledger": handle.path, "rows": counts})
    return config.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
