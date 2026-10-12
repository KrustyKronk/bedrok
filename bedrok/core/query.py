"""Thin SQL runner over the DuckDB store — the analytical escape hatch.

  .venv/bin/python src/query.py "SELECT * FROM macro_catalog LIMIT 5"
  .venv/bin/python src/query.py --csv "SELECT date, value FROM macro_series WHERE series_id='us_10y'"
  echo "SELECT ..." | .venv/bin/python src/query.py

Read-only connection. Prints an aligned table by default, or raw CSV with --csv
(handy for piping into a chart). Full DuckDB SQL is available.

Session-only series (macro_catalog.cache = 'session', e.g. us_hy_oas, us_vix)
are never in the store, so SQL does not see them: `bedrok pctile` fetches them
live. SQL over live data is the MCP server's job (session 5).
"""
from __future__ import annotations

import sys

import pandas as pd

from bedrok.core import config


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--csv"]
    as_csv = "--csv" in sys.argv[1:]
    sql = args[0] if args else sys.stdin.read()
    if not sql.strip():
        print("usage: query.py [--csv] \"<SQL>\"", file=sys.stderr)
        return 2

    con = config.connect(read_only=True)
    df = con.execute(sql).fetch_df()
    con.close()

    if as_csv:
        sys.stdout.write(df.to_csv(index=False))
    else:
        pd.set_option("display.width", 220)
        pd.set_option("display.max_columns", None)
        pd.set_option("display.max_rows", 200)
        print(df.to_string(index=False))
        print(f"\n[{len(df)} rows]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
