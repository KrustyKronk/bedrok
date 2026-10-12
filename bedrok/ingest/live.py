"""Series held for the conversation, never for the store.

A catalog row with `cache = session` is one whose terms forbid keeping its
numbers (FRED's, and the six series only FRED serves). Sync never writes it.
This module is the one way to read it: fetched from its publisher with the
user's own key, returned in memory, and dropped when the command ends.

Session 5's MCP server is the real home for this — a conversation is exactly
the lifetime the terms allow. Until then `pctile` is the command that uses it.
"""
from __future__ import annotations

import pandas as pd

from bedrok.core import licensing
from bedrok.ingest import pipeline

MissingKey = pipeline.MissingKey


def cache_of(con, series_id: str) -> str | None:
    """'store', 'session', or None when the id is not in the catalog."""
    row = con.execute("SELECT coalesce(cache, 'store') FROM macro_catalog "
                      "WHERE series_id = ?", [series_id]).fetchone()
    return row[0] if row else None


def fetch(con, series_id: str) -> pd.DataFrame:
    """Fetch a session series live: DataFrame[date, value], never written.

    Raises MissingKey when its source needs a key the profile lacks, and
    ValueError for an id that is not a session series."""
    row = con.execute("SELECT series_id, source, source_code FROM macro_catalog "
                      "WHERE series_id = ? AND cache = ?",
                      [series_id, licensing.SESSION]).fetch_df()
    if row.empty:
        raise ValueError(f"{series_id} is not a session-only series")
    return pipeline._fetch_macro(row.iloc[0], None)
