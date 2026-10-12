"""Dump the table/column layout of the store — kills the column-name guessing.

  bedrok schema

Read-only. Prints every table and view in the store with its columns and types
via DESCRIBE, so `frequency` (not `freq`), `value`, `rate` and the rest are
right here and there are no more Binder errors from a plausible-but-wrong
column name.
"""
from __future__ import annotations

import sys

from bedrok.core import config


def _describe(con, name: str) -> None:
    cols = con.execute(f"DESCRIBE {name}").fetch_df()
    print(f"\n  {name}")
    for _, r in cols.iterrows():
        print(f"    {r['column_name']:<22} {r['column_type']}")


def main() -> int:
    con = config.connect(read_only=True)
    print(f"\n=== store ({config.DB_PATH}) ===")
    tables = con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE NOT internal "
        "AND database_name = current_database() ORDER BY 1").fetch_df()
    for name in tables["table_name"]:
        _describe(con, name)
    # `NOT internal` drops DuckDB's own information_schema/pg_catalog views.
    views = con.execute(
        "SELECT view_name FROM duckdb_views() WHERE NOT internal "
        "AND database_name = current_database() ORDER BY 1").fetch_df()
    if len(views):
        print("\n=== views ===")
        for name in views["view_name"]:
            _describe(con, name)
    con.close()
    print("\nUnified long view: all_series(kind, series_id, date, value) — macro ∪ fx.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
