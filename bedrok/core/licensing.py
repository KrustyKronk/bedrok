"""The publisher's terms, recorded per catalog row.

Bedrok is a local store: each user fetches every series from its publisher with
their own keys, for their own use, and nothing is redistributed. The `license`
class records what the publisher's terms say — what you may do with a number in
your store, and what an assistant should say when it quotes one.

The vocabulary is deliberately five values:

  public_domain            US federal works taken from the producing agency
                           itself (BLS, BEA, Census, the Fed Board, FHFA, EIA,
                           CFTC). No conditions; cite anyway.
  attribution              CC BY / OGL-family sources (ECB, Eurostat, DG ECFIN,
                           World Bank, OECD, ONS, Bank of England, the NY Fed's
                           Markets API, survey and research files, NBER's
                           business-cycle dates).
  noncommercial            Reuse allowed with credit, but not for gain: the
                           Chicago, Cleveland, Dallas and Atlanta Feds' files.
                           Fine in your own store; credit them when you quote. Reusable *with* the source's
                           attribution line — see ATTRIBUTIONS.
  attribution_fred_channel Anything pulled through the FRED API. The underlying
                           series may be public domain, but FRED's own terms
                           forbid storing or archiving FRED content in any
                           database and using it in connection with developing
                           machine-learning or LLM systems. Since session 3b the
                           rows with no other source are `cache = session`:
                           fetched with your key when asked for, never stored.
                           Every FRED row is session; nothing from FRED is
                           stored (tests/test_macro_seed.py pins it).
  personal_use             The publisher forbids redistribution or reserves all
                           rights (Moody's, ICE, the University of Michigan,
                           the Philadelphia Fed). For your own use only. Never
                           pass the numbers on.

Whether a row may be STORED at all is a separate column, `cache` (store or
session) — a licence class says what you may do with a number you hold; the
cache says whether your store may hold it.

Adding a series? Set `license` in the seed CSV *and* in every runtime cat_rows
builder (pipeline.sync_hicp_europe / sync_pink_sheet / sync_eurostat_multi /
sync_cot, derived.sync_derived) — roughly two thirds of the catalog is built by
code, not by the seed, and a NULL license trips LICENSE-UNREVIEWED in validate.
"""
from __future__ import annotations

from bedrok.core import sources

PUBLIC_DOMAIN = "public_domain"
ATTRIBUTION = "attribution"
NONCOMMERCIAL = "noncommercial"
FRED_CHANNEL = "attribution_fred_channel"
RESTRICTED = "personal_use"

LICENSE_VALUES = frozenset({PUBLIC_DOMAIN, ATTRIBUTION, NONCOMMERCIAL, FRED_CHANNEL,
                            RESTRICTED})

# The cache column: may this row's observations be written to the store?
STORE = "store"
SESSION = "session"
CACHE_VALUES = frozenset({STORE, SESSION})

# Per-source attribution lines live in the source list
# (bedrok/seeds/sources.csv, `attribution`), verbatim where the licensor mandates
# specific wording (FRED, Bank of England SONIA) — do not paraphrase those.
# DG ECFIN is credited here because its surveys arrive through Eurostat rather
# than as a source of their own.
ATTRIBUTIONS: dict[str, str] = {
    **{s: r["attribution"] for s, r in sources.load().items() if r["attribution"]},
    "dg_ecfin": (
        "Source: European Commission, Directorate-General for Economic and "
        "Financial Affairs (DG ECFIN) Business and Consumer Surveys, via "
        "Eurostat. Reused under Commission Decision 2011/833/EU (CC BY 4.0)."
    ),
}

# Default licence class per source, from the source list's `license` column,
# used to backfill rows that predate the licence column. Deliberately NOT a
# catch-all: a source missing from the list keeps a NULL licence and trips
# LICENSE-UNREVIEWED, which is exactly what should happen when someone wires up
# a new source without deciding its terms. A per-series licence (from the seed
# CSV or a cat_rows builder) always wins — this only ever fills NULLs.
SOURCE_DEFAULT_LICENSE: dict[str, str] = {
    s: r["license"] for s, r in sources.load().items() if r["license"]}

# Series needing an attribution line of their own, beyond their source's.
SERIES_ATTRIBUTIONS: dict[str, str] = {
    "us_vix": (
        "Source: Cboe via FRED. The VIX index is copyright Cboe Global Markets "
        "('Copyrighted: Citation Required' on FRED); fetched live for the "
        "conversation and never stored."
    ),
    "us_house_prices_fhfa": (
        "This product uses FHFA Data but is neither endorsed nor certified by "
        "FHFA."
    ),
    "us_recession": (
        "Recession dates: National Bureau of Economic Research, Business Cycle "
        "Dating Committee (nber.org), copied with attribution."
    ),
    "uk_sonia": (
        "SONIA and/or SONIA Compounded Index data licensed under the Open "
        "Government Licence v3.0 and copyright the Governor and Company of the "
        "Bank of England."
    ),
}


def backfill_source_defaults(con) -> int:
    """Give NULL-licence rows their source's default class; return rows touched.

    Two thirds of the catalog is registered by bulk sync stages, which only set
    a licence when their fetch succeeds. Without this, a store that predates the
    licence column would trip LICENSE-UNREVIEWED until every bulk stage had run
    at least once — so a single Eurostat outage on the wrong day would look like
    a licensing failure. Unknown sources stay NULL by design.
    """
    before = con.execute(
        "SELECT count(*) FROM macro_catalog WHERE license IS NULL").fetchone()[0]
    for source, lic in SOURCE_DEFAULT_LICENSE.items():
        con.execute(
            "UPDATE macro_catalog SET license = ? WHERE source = ? AND license IS NULL",
            [lic, source])
    after = con.execute(
        "SELECT count(*) FROM macro_catalog WHERE license IS NULL").fetchone()[0]
    return before - after


def restricted_series(con) -> list[str]:
    """series_ids whose publisher forbids redistribution (fetched only with
    your own key)."""
    return [r[0] for r in con.execute(
        "SELECT series_id FROM macro_catalog WHERE license = ? "
        "AND series_id IS NOT NULL ORDER BY series_id", [RESTRICTED]).fetchall()]


def session_series(con) -> list[str]:
    """series_ids that may never be written to the store (cache = session)."""
    return [r[0] for r in con.execute(
        "SELECT series_id FROM macro_catalog WHERE cache = ? ORDER BY series_id",
        [SESSION]).fetchall()]


def unreviewed_series(con) -> list[tuple[str, str | None]]:
    """(series_id, license) rows whose license is NULL or not in the vocabulary.

    A NULL license usually means a new cat_rows builder forgot to set one, so
    it is counted as a validate finding rather than shown as info."""
    rows = con.execute(
        "SELECT series_id, license FROM macro_catalog ORDER BY series_id"
    ).fetchall()
    return [(sid, lic) for sid, lic in rows if lic not in LICENSE_VALUES]

