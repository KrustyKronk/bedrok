"""DuckDB store: schema DDL + idempotent upserts.

Every writer is an UPSERT keyed on the table's primary key, so backfill and
refresh can be re-run any number of times without duplicating rows.

One file, four tables — macro_catalog, macro_series, fx_rates and
release_calendar — plus the persisted `all_series` view over macro ∪ fx. Since session 2 of the rethink
there is no market pack, no personal layer and no price data: every install
fetches each series from its publisher into this one store.
"""
from __future__ import annotations

import duckdb
import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS macro_catalog (
    series_id      TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    source_code    TEXT NOT NULL,
    name           TEXT NOT NULL,
    frequency      TEXT,
    units          TEXT,
    notes          TEXT,
    staleness_days INTEGER,
    license        TEXT
);

CREATE TABLE IF NOT EXISTS macro_series (
    series_id    TEXT NOT NULL,
    date         DATE NOT NULL,
    value        DOUBLE,
    vintage_date DATE,
    PRIMARY KEY (series_id, date)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    pair   TEXT NOT NULL,
    date   DATE NOT NULL,
    rate   DOUBLE,
    source TEXT,
    PRIMARY KEY (pair, date)
);

-- The publishers' own release dates (ingest/calendars.py). due_at is UTC
-- without a zone (DuckDB's TIMESTAMPTZ arithmetic needs an extension); tz is
-- the publisher's zone, for saying it back. period is the label of the period
-- released, when the publisher names it (the ONS does; BEA's schedule does not).
CREATE TABLE IF NOT EXISTS release_calendar (
    feed         TEXT NOT NULL,
    release_name TEXT NOT NULL,
    due_at       TIMESTAMP NOT NULL,
    tz           TEXT NOT NULL,
    period       DATE,
    fetched_at   TIMESTAMP NOT NULL,
    PRIMARY KEY (feed, release_name, due_at)
);
"""


# Catalog columns added after the first deployment; migrated in via
# ADD COLUMN IF NOT EXISTS so pre-existing stores gain them without a rebuild.
#   staleness_days — per-series freshness allowance override (NULL → frequency default)
#   license        — the publisher's terms class (see core/licensing.py)
#   filled_from    — "<source>:<source_code>" the stored observations came from;
#                    written only by sync, never by a catalog load, so a seed
#                    that re-points a series is visible as a mismatch
#   cache          — 'store' or 'session': may this row's observations be
#                    written here at all (see core/licensing.py)
_CATALOG_MIGRATIONS = [
    ("staleness_days", "INTEGER"),
    ("license", "TEXT"),
    ("filled_from", "TEXT"),
    ("cache", "TEXT"),
]


def _migrate_macro_catalog(con) -> None:
    have = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'macro_catalog'").fetchall()}
    for col, typ in _CATALOG_MIGRATIONS:
        con.execute(f"ALTER TABLE macro_catalog ADD COLUMN IF NOT EXISTS {col} {typ}")
    if "filled_from" not in have:
        # A store from before this column: whatever it holds came from the source
        # its catalog names right now (init_db runs before the seed is reloaded).
        con.execute("""
            UPDATE macro_catalog SET filled_from = source || ':' || source_code
            WHERE series_id IN (SELECT DISTINCT series_id FROM macro_series)""")
    # Rows registered at runtime (the bulk stages, derived) never name a cache;
    # everything Bedrok builds itself from stored inputs is storable.
    con.execute("UPDATE macro_catalog SET cache = 'store' WHERE cache IS NULL")


def _all_series_view(con) -> None:
    """One (kind, series_id, date, value) shape over macro and FX — kills the
    rate-vs-value column trap in ad-hoc SQL. CREATE OR REPLACE, so a store made
    before session 2, whose view also unioned prices_eod, is repointed."""
    con.execute("""
        CREATE OR REPLACE VIEW all_series AS
        SELECT 'macro' AS kind, series_id, date, value FROM macro_series
        UNION ALL
        SELECT 'fx' AS kind, pair AS series_id, date, rate AS value FROM fx_rates
    """)


def init_db(con: duckdb.DuckDBPyConnection) -> None:
    """Create the four tables (idempotent), migrate the catalog, and (re)create
    the all_series view."""
    con.execute(SCHEMA)
    _migrate_macro_catalog(con)
    _all_series_view(con)


def _upsert(con, table: str, df: pd.DataFrame, pk: list[str]) -> int:
    """Insert df into table, updating existing rows on primary-key conflict."""
    if df is None or df.empty:
        return 0
    cols = list(df.columns)
    non_pk = [c for c in cols if c not in pk]
    set_clause = ", ".join(f"{c} = excluded.{c}" for c in non_pk)
    conflict = f"({', '.join(pk)})"
    action = f"DO UPDATE SET {set_clause}" if non_pk else "DO NOTHING"
    con.register("_staging", df)
    con.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"SELECT {', '.join(cols)} FROM _staging "
        f"ON CONFLICT {conflict} {action}"
    )
    con.unregister("_staging")
    return len(df)


# ---- Typed writers ---------------------------------------------------------
def upsert_macro_catalog(con, df: pd.DataFrame) -> int:
    return _upsert(con, "macro_catalog", df, ["series_id"])


def add_missing_catalog_rows(con, df: pd.DataFrame) -> int:
    """Insert catalog rows whose series_id is not there yet; never overwrite.
    For rows known before a download (pipeline.static_catalog_rows), which the
    download later refines — a geography's full name, for one."""
    if df is None or df.empty:
        return 0
    before = con.execute("SELECT count(*) FROM macro_catalog").fetchone()[0]
    cols = list(df.columns)
    con.register("_staging", df)
    con.execute(f"INSERT INTO macro_catalog ({', '.join(cols)}) "
                f"SELECT {', '.join(cols)} FROM _staging ON CONFLICT (series_id) DO NOTHING")
    con.unregister("_staging")
    return con.execute("SELECT count(*) FROM macro_catalog").fetchone()[0] - before


def upsert_macro_series(con, series_id: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    out = df[["date", "value"]].copy()
    out.insert(0, "series_id", series_id)
    out["vintage_date"] = pd.NaT
    return _upsert(con, "macro_series", out, ["series_id", "date"])


def replace_macro_series(con, series_id: str, df: pd.DataFrame, filled_from: str) -> int:
    """Swap a series' whole history for df and record where it came from.

    Used when a series is re-pointed to a new source. Its new history replaces
    the old one outright rather than being upserted over it: an upsert would
    leave old-source points wherever the new source has none, a splice of two
    publishers' levels nobody chose (CLAUDE.md rule 6)."""
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM macro_series WHERE series_id = ?", [series_id])
        n = upsert_macro_series(con, series_id, df)
        con.execute("UPDATE macro_catalog SET filled_from = ? WHERE series_id = ?",
                    [filled_from, series_id])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return n


def forget_macro_series(con, series_id: str) -> int:
    """Delete a series' stored history and the record of where it came from.

    For a row that has become session-only: its numbers may be fetched for a
    conversation but must not stay in the store."""
    n = con.execute("SELECT count(*) FROM macro_series WHERE series_id = ?",
                    [series_id]).fetchone()[0]
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM macro_series WHERE series_id = ?", [series_id])
        con.execute("UPDATE macro_catalog SET filled_from = NULL WHERE series_id = ?",
                    [series_id])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return n


def upsert_fx(con, pair: str, df: pd.DataFrame, source: str) -> int:
    if df.empty:
        return 0
    out = df[["date", "value"]].rename(columns={"value": "rate"}).copy()
    out.insert(0, "pair", pair)
    out["source"] = source
    return _upsert(con, "fx_rates", out, ["pair", "date"])


def close_checkpointed(con) -> None:
    """CHECKPOINT then close, so no `.wal` survives the command.

    DuckDB REMOVES the write-ahead log on the next connect, and the Cowork mount
    permits write and truncate but not unlink — so a `.wal` left behind by one
    command makes the next `config.connect()` die with `IO Error: Could not
    remove file ... .wal: Operation not permitted`. CHECKPOINT folds the log into
    the database file, which means there is nothing left to unlink.

    Best-effort on both halves: a read-only connection cannot checkpoint, and a
    connection that is already closed or broken must not turn cleanup into the
    error the caller sees. Closing is what matters most, so it happens either way.
    """
    try:
        con.execute("CHECKPOINT")
    except Exception:                                             # noqa: BLE001
        pass
    try:
        con.close()
    except Exception:                                             # noqa: BLE001
        pass


def last_date(con, table: str, key_col: str, key_val: str, date_col: str = "date"):
    """Most recent stored date for a series/pair (None if absent)."""
    row = con.execute(
        f"SELECT max({date_col}) FROM {table} WHERE {key_col} = ?", [key_val]
    ).fetchone()
    return row[0] if row else None


def table_range(con, table: str, date_col: str = "date"):
    """(row count, min date, max date) for a table — the coverage line that
    doctor and validate both print."""
    return con.execute(
        f"SELECT count(*), min({date_col}), max({date_col}) FROM {table}"
    ).fetchone()


def replace_release_calendar(con, feed: str, df: pd.DataFrame,
                             release_names: list[str] | None = None) -> int:
    """Swap a feed's dates (or only those of `release_names`) for df's, in one
    transaction: a publisher that moves a date drops the old one, and a stale
    date left behind would read as the next release."""
    con.execute("BEGIN TRANSACTION")
    try:
        if release_names is None:
            con.execute("DELETE FROM release_calendar WHERE feed = ?", [feed])
        else:
            ph = ", ".join("?" for _ in release_names)
            con.execute(f"DELETE FROM release_calendar WHERE feed = ? AND release_name IN ({ph})",
                        [feed, *release_names])
        n = _upsert(con, "release_calendar", df, ["feed", "release_name", "due_at"])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return n
