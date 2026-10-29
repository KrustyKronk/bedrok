"""Shared sync logic for backfill (full history) and refresh (incremental).

Each sync_* function loops its catalog, fetches via the adapters, upserts into
DuckDB, and returns a list of (name, status, detail) rows for reporting. A single
series failure is logged and skipped — it never aborts the whole run.
"""
from __future__ import annotations

import importlib
import os
import time
from datetime import date, timedelta

import pandas as pd
import requests

from bedrok.core import config
from bedrok.core import licensing
from bedrok.core import releases
from bedrok.core import sources
from bedrok.core import store
from bedrok.core import units
from bedrok.core.aliases import ALIASES
from bedrok.ingest import calendars
from bedrok.ingest.adapters import cftc, eurostat, nbk, ons, pxweb, sdmx, worldbank

# FX pairs come from the profile's base currency (config.fx_pairs()); pairs with
# source 'derived' are computed from already-synced ECB EUR-reference rates
# (e.g. USDEUR = 1/EURUSD) — the best-effort path for non-EUR bases.

# Incremental refresh re-fetches a small overlap window to catch revisions.
OVERLAP_DAYS = 7

# Per-source inter-request pacing (seconds) comes from the source list's
# `pacing_s`, to stay polite to free public APIs. Override any value at runtime
# with $BEDROK_PACE_<SOURCE> (e.g. BEDROK_PACE_FRED=0 for local test runs).
# HTTP statuses worth a retry (transient); anything else fails fast.
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _default_pace(source: str) -> float:
    row = sources.load().get(source)
    return row["pacing_s"] if row else 0.5


def _pace(source: str) -> None:
    env = os.getenv(f"BEDROK_PACE_{source.upper()}")
    try:
        secs = float(env) if env is not None else _default_pace(source)
    except ValueError:
        secs = _default_pace(source)
    if secs > 0:
        time.sleep(secs)


def _with_retry(fn, *, tries: int = 3, base: float = 10.0):
    """Call fn(); on a transient network error retry with exponential backoff,
    honouring Retry-After. Non-transient errors (bad series code, 404) raise at once."""
    for attempt in range(tries):
        try:
            return fn()
        except requests.HTTPError as e:
            resp = getattr(e, "response", None)
            code = getattr(resp, "status_code", None)
            if code not in _RETRY_STATUS or attempt == tries - 1:
                raise
            ra = resp.headers.get("Retry-After") if resp is not None else None
            wait = float(ra) if (ra and str(ra).isdigit()) else base * (2 ** attempt)
            time.sleep(wait)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == tries - 1:
                raise
            time.sleep(base * (2 ** attempt))


def _incremental_start(con, table, key_col, key_val) -> str | None:
    """Re-fetch from OVERLAP_DAYS before the last stored date — but never from a
    date after today. Forecast series (the ECB's Survey of Professional
    Forecasters) store observations dated in the future, so their 'last date'
    minus a week is still in the future, and the ECB rejects a future
    startPeriod with HTTP 400. Capping at today also picks up the next forecast
    round, which revises those future-dated points."""
    last = store.last_date(con, table, key_col, key_val)
    if last is None:
        return None  # nothing stored yet → full pull
    start = min(last, date.today()) - timedelta(days=OVERLAP_DAYS)
    return start.isoformat()


# The catalog ships with the code, not with the profile: every install reads the
# same curated list. (Session 4 of the rethink replaces it with a registry.)
CATALOG_CSV = config.SEEDS_DIR / "macro_catalog.csv"
# The rows only a bulk download names (Pink Sheet, CFTC, broad HICP), written
# from a refreshed store so a fresh install can find them by words: before
# session G, "gold" found nothing and "turkey inflation" nothing until a
# refresh had run. The download still refines them; these never overwrite it.
BULK_CATALOG_CSV = config.SEEDS_DIR / "bulk_catalog.csv"


# Series a publisher has stopped, removed from the catalog and the store on the
# next load. Bulk stages register their rows at runtime, so a series they keep
# returning must be named here to stay gone.
#   wb_barley, wb_sorghum — the World Bank's Pink Sheet shows "…" for both after
#     August 2020 (an earlier edition carried values to 2026). Their stale last
#     date also dragged every incremental Pink Sheet download back to 2020.
#   wb_shrimp — ends 2023-10 (still so on 2026-10-16) and set every incremental
#     Pink Sheet download back to 2023 the same way; retired by the owner.
RETIRED = frozenset({"wb_barley", "wb_sorghum", "wb_shrimp", "hicp_yoy_xk"})


def load_catalog(con) -> None:
    """Upsert the repo's macro catalog into the store, give any row that
    predates the licence column its source's default class, and remove what
    should not be there: retired series, ids that are now aliases of another
    (core/aliases.py), and Eurostat family rows no family produces any more
    that never filled (a euro-area code the dataset does not use)."""
    static = static_catalog_rows()
    store.upsert_macro_catalog(con, pd.read_csv(CATALOG_CSV, dtype=str))
    store.add_missing_catalog_rows(con, static)
    store.add_missing_catalog_rows(con, bulk_catalog_rows())
    licensing.backfill_source_defaults(con)
    ids = sorted(set(RETIRED) | set(ALIASES))
    ph = ", ".join("?" for _ in ids)
    con.execute(f"DELETE FROM macro_series WHERE series_id IN ({ph})", ids)
    con.execute(f"DELETE FROM macro_catalog WHERE series_id IN ({ph})", ids)
    prune_empty_family_rows(con, set(static["series_id"]))


def prune_empty_family_rows(con, produced: set[str]) -> list[str]:
    """Delete Eurostat family rows that hold no data and that no family
    produces any more. A produced row that simply has not filled yet stays."""
    families = tuple(s["prefix"] for s in EUROSTAT_BULK_SETS)
    empty = [r[0] for r in con.execute(
        "SELECT mc.series_id FROM macro_catalog mc "
        "LEFT JOIN macro_series ms USING (series_id) "
        "WHERE mc.source = 'eurostat' GROUP BY mc.series_id HAVING count(ms.date) = 0"
    ).fetchall()]
    gone = [sid for sid in empty if sid.startswith(families) and sid not in produced]
    if gone:
        ph = ", ".join("?" for _ in gone)
        con.execute(f"DELETE FROM macro_catalog WHERE series_id IN ({ph})", gone)
    return gone


class MissingKey(Exception):
    pass


def error_detail(e: Exception, width: int = 70) -> str:
    """One-line failure text with any API key removed BEFORE it is cut to width
    (a requests error names its URL, and some URLs carry a key). Line breaks
    become spaces: GENESIS answers in several lines, and the report is one."""
    text = " ".join(f"{type(e).__name__}: {e}".split())
    return sources.redact(text)[:width + len(type(e).__name__) + 2]


def _fetcher(source: str, row: dict):
    """A (code, start) → DataFrame fetcher for one source-list row.

    sdmx and pxweb rows go to their generic client with the row as its configuration;
    adapter rows to their module's fetch, handed the key when the row names one.
    A required key that is missing raises MissingKey before any network call."""
    if row["protocol"] == "sdmx":
        return lambda code, start: sdmx.fetch(row, code, start=start)
    if row["protocol"] == "pxweb":
        return lambda code, start: pxweb.fetch(row, code, start=start)
    module = importlib.import_module(f"bedrok.ingest.adapters.{row['adapter']}")
    if not row["key_env"]:
        return lambda code, start: module.fetch(code, start=start)

    def keyed(code, start):
        key = sources.key(source)
        if not key and row["key_required"] == "yes":
            raise MissingKey(f"no {row['key_env']} (keyless mode)")
        return module.fetch(code, key, start=start)
    return keyed


# Macro source registry, built from the source list: source → a
# (code, start) → DataFrame[date, value] fetcher. Bulk sources (World Bank,
# CFTC, derived) have stages of their own and no per-series entry.
MACRO_FETCHERS = {s: _fetcher(s, row) for s, row in sources.load().items()
                  if row["protocol"] in ("sdmx", "pxweb", "adapter")}

# Sources that answer many series per request are told up front which codes
# the run will ask for, so they can batch: any adapter with a prime().
MACRO_PRIMERS = {
    s: mod.prime for s, row in sources.load().items() if row["protocol"] == "adapter"
    for mod in [importlib.import_module(f"bedrok.ingest.adapters.{row['adapter']}")]
    if hasattr(mod, "prime")}


def _fetch_macro(row, start):
    fn = MACRO_FETCHERS.get(row["source"])
    if fn is None:
        raise ValueError(f"unknown source {row['source']}")
    return _with_retry(lambda: fn(row["source_code"], start))


# Broad-Europe HICP: one bulk call for all geos (ECOICOP v2 dataset; the old
# prc_hicp_manr closed at 2025-12). Series land as hicp_yoy_{geo}; their catalog
# rows are auto-registered and excluded from the per-series sync_macro loop.
HICP_BULK_CODE = "prc_hicp_minr?unit=RCH_A&coicop18=TOTAL"
HICP_PREFIX = "hicp_yoy_"
WB_PREFIX = "wb_"  # World Bank Pink Sheet commodities (bulk via sync_pink_sheet)
COT_PREFIX = "cot_"  # CFTC positioning (bulk via sync_cot)

# Series owned by a bulk stage rather than the per-series sync_macro loop.
# Two exclusion axes because the sources differ in shape:
#   * BULK_SOURCES — the whole source is bulk-fetched, so exclude by `source`.
#   * BULK_PREFIXES — bulk and per-series rows SHARE a source (hicp_yoy_* is
#     bulk while ea_hicp_yoy is per-series, both source='eurostat'), so those
#     can only be told apart by series_id prefix.
BULK_SOURCES = tuple(s for s, row in sources.load().items() if row["protocol"] == "bulk")
# BULK_PREFIXES is assembled below, once the Eurostat set registry is defined.


def sync_hicp_europe(con, incremental=False) -> list[tuple]:
    if not config.PROFILE["macro"].get("hicp_europe", True):
        return [("hicp_europe", "skip", "disabled in profile")]
    start = None
    if incremental:
        last = con.execute(
            "SELECT min(hi) FROM (SELECT max(date) hi FROM macro_series "
            "WHERE series_id LIKE ? GROUP BY series_id)", [HICP_PREFIX + "%"]
        ).fetchone()[0]
        if last:
            start = f"{last.year}-{last.month:02d}"
    try:
        per_geo, labels = eurostat.fetch_multi_geo(HICP_BULK_CODE, start=start)
    except Exception as e:  # noqa: BLE001
        return [("hicp_europe_bulk", "FAIL", error_detail(e, 70))]
    # Drop structurally-dead geos (e.g. UK stopped contributing post-Brexit,
    # ending 2020; US is stale and out of scope) so they never pollute the store,
    # and any geo outside Eurostat's commercial reuse grant (REUSE_GEOS).
    from datetime import date as _date, timedelta as _td
    cutoff = _date.today() - _td(days=400)

    report = []
    cat_rows = []
    for geo, df in sorted(per_geo.items()):
        if geo not in REUSE_GEOS:
            report.append((f"{HICP_PREFIX}{geo.lower()}", "skip", "outside Eurostat's reuse grant"))
            continue
        if df["date"].max() < cutoff:
            report.append((f"{HICP_PREFIX}{geo.lower()}", "skip", "dead/stale geo"))
            continue
        sid = f"{HICP_PREFIX}{geo.lower()}"
        if sid in ALIASES:
            continue                          # stored under its canonical id
        n = store.upsert_macro_series(con, sid, df)
        cat_rows.append({
            "series_id": sid, "source": "eurostat",
            "source_code": f"{HICP_BULK_CODE}&geo={geo}",
            "name": f"HICP annual rate — {labels.get(geo, geo)}",
            "frequency": "monthly", "units": "percent",
            "notes": "broad-Europe HICP; bulk-synced via sync_hicp_europe",
            "license": licensing.ATTRIBUTION,
        })
        report.append((sid, "ok", f"{n} rows"))
    if cat_rows:
        store.upsert_macro_catalog(con, pd.DataFrame(cat_rows))
    return report


# ---- Eurostat multi-geo bulk sets ------------------------------------------
#
# One geo-unfiltered API call per indicator, split per geo — the same trick
# sync_hicp_europe uses. Unlike HICP (deliberately full-geo, as a breadth set),
# these are CURATED: 11 indicators × 40-odd geos would add ~450 series, most of
# them Albanian retail trade nobody will ever query, and every one of them a
# line in the staleness report. The core list is the euro-area aggregates plus
# the countries whose divergence actually matters to a EUR-based investor.
#
# Both 'EA' and 'EA20' appear because the datasets disagree: irt_lt_mcby_m and
# une_rt_m carry 'EA', the STS/national-accounts sets carry 'EA20'. A geo a
# dataset does not publish is skipped silently (prc_hpi_q has no Greece).
EU_CORE_GEOS = ["EA", "EA20", "EU27_2020", "DE", "FR", "IT", "ES", "NL", "BE",
                "AT", "PT", "IE", "EL", "FI"]

# Eurostat's copyright notice grants CC BY 4.0 reuse, commercial included,
# except for data on countries other than EU members, EFTA members and official
# candidate countries (its examples: US, Japan, China). une_rt_m carries US and
# JP geos. These curated families iterate their own list, never the API
# response — do not "simplify" this into a loop over per_geo. The broad HICP
# set does loop over the response, as a breadth set, and keeps only REUSE_GEOS.
_NON_EU_GEOS = {"US", "JP", "CN"}

# Inside the grant: the aggregates, the EU 27, EFTA, and the official candidate
# countries (Georgia since December 2023). Kosovo is a potential candidate only
# and stays out (owner's reading of the terms, 2026-10-12).
REUSE_GEOS = frozenset({
    "EA", "EA19", "EA20", "EA21", "EU", "EU27_2020", "EEA",
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR", "HR",
    "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK",
    "CH", "IS", "LI", "NO",
    "AL", "BA", "GE", "MD", "ME", "MK", "RS", "TR", "UA",
})

# The euro area under each code a dataset may use: EA (changing composition),
# EA20 and, since Bulgaria joined in 2026, EA21. Datasets disagree and move, so
# each family names the codes its dataset fills (`ea_codes`, default EA and
# EA20) and the curated countries it lacks (`missing`). Registering a code a
# dataset does not fill leaves an empty catalog row that search finds and
# describe reports empty: eleven of them by 2026-10.
_EA_CODES = ("EA", "EA20", "EA21")


def family_geos(spec: dict) -> list[str]:
    """The geographies one Eurostat family is registered and fetched for."""
    countries = [g for g in spec.get("geos", EU_CORE_GEOS)
                 if g not in _EA_CODES and g not in spec.get("missing", ())]
    return list(spec.get("ea_codes", ["EA", "EA20"])) + countries


DG_ECFIN_NOTE = ("European Commission DG ECFIN Business and Consumer Surveys "
                 "via Eurostat")

EUROSTAT_BULK_SETS = [
    {"prefix": "ltgov_yield_", "ea_codes": ["EA"], "code": "irt_lt_mcby_m?int_rt=MCBY",
     "label": "EMU 10y govt bond yields", "frequency": "monthly", "units": "percent",
     "name_tmpl": "10y government bond yield (Maastricht) — {geo}",
     "notes": "monthly average; the input to the sovereign-spread derived series. "
              "DE/FR/IT/ES from 1980 PT 1986 EL 1992"},
    {"prefix": "esi_", "ea_codes": ["EA21"], "code": "ei_bssi_m_r2?indic=BS-ESI-I&s_adj=SA",
     "label": "Economic Sentiment Indicator", "frequency": "monthly", "units": "index long-term average=100",
     "name_tmpl": "Economic Sentiment Indicator — {geo}", "notes": DG_ECFIN_NOTE},
    {"prefix": "ind_conf_", "ea_codes": ["EA21"], "code": "ei_bssi_m_r2?indic=BS-ICI-BAL&s_adj=SA",
     "label": "Industrial confidence", "frequency": "monthly", "units": "balance",
     "name_tmpl": "Industrial confidence — {geo}", "notes": DG_ECFIN_NOTE},
    {"prefix": "cons_conf_", "ea_codes": ["EA21"], "code": "ei_bssi_m_r2?indic=BS-CSMCI-BAL&s_adj=SA",
     "label": "Consumer confidence", "frequency": "monthly", "units": "balance",
     "name_tmpl": "Consumer confidence — {geo}", "notes": DG_ECFIN_NOTE},
    {"prefix": "indprod_yoy_", "ea_codes": ["EA20"],
     "code": "sts_inpr_m?indic_bt=PRD&nace_r2=B-D&s_adj=CA&unit=PCH_SM",
     "label": "Industrial production YoY", "frequency": "monthly", "units": "percent",
     "staleness_days": 110,
     "name_tmpl": "Industrial production YoY — {geo}",
     "notes": "mining/manufacturing/utilities (B-D) calendar-adjusted"},
    {"prefix": "retail_yoy_", "ea_codes": ["EA20"],
     "code": "sts_trtu_m?indic_bt=VOL_SLS&nace_r2=G47&s_adj=CA&unit=PCH_SM",
     "label": "Retail trade volume YoY", "frequency": "monthly", "units": "percent",
     "staleness_days": 110,
     "name_tmpl": "Retail trade volume YoY — {geo}",
     "notes": "retail excluding motor vehicles (G47) calendar-adjusted"},
    {"prefix": "gdp_yoy_",
     "code": "namq_10_gdp?na_item=B1GQ&unit=CLV_PCH_SM&s_adj=SCA",
     "label": "Real GDP YoY", "frequency": "quarterly", "units": "percent",
     "staleness_days": 260,
     "name_tmpl": "Real GDP YoY — {geo}",
     "notes": "chain-linked volumes, on the same quarter a year earlier. The "
              "average of a year's four quarters is close to that year's growth "
              "but is not it; gdp_annual_ for the same geography is. Its parts "
              "by expenditure are gdp_contrib_q_"},
    {"prefix": "gdp_qoq_",
     "code": "namq_10_gdp?na_item=B1GQ&unit=CLV_PCH_PRE&s_adj=SCA",
     "label": "Real GDP QoQ", "frequency": "quarterly",
     "units": "percent change on previous quarter", "staleness_days": 260,
     "name_tmpl": "Real GDP QoQ — {geo}",
     "notes": "chain-linked volumes, seasonally and calendar adjusted; the annual "
              "rate is gdp_yoy_ for the same geography"},
    {"prefix": "hpi_yoy_", "missing": ["EL"], "code": "prc_hpi_q?purchase=TOTAL&unit=RCH_A",
     "label": "House price index YoY", "frequency": "quarterly", "units": "percent",
     "staleness_days": 260,
     "name_tmpl": "House price index YoY — {geo}",
     "notes": "total purchases; Eurostat carries no Greek HPI"},
    {"prefix": "lci_yoy_", "missing": ["BE"],
     "code": "lc_lci_r2_q?lcstruct=D1_D4_MD5&nace_r2=B-S&unit=PCH_SM&s_adj=CA",
     "label": "Labour cost index YoY", "frequency": "quarterly", "units": "percent",
     "staleness_days": 260,
     "name_tmpl": "Labour cost index YoY — {geo}",
     "notes": "business economy (B-S); the euro-area wage-pressure read"},
    {"prefix": "govdebt_gdp_", "ea_codes": ["EA20"],
     "code": "gov_10q_ggdebt?na_item=GD&sector=S13&unit=PC_GDP",
     "label": "Government debt/GDP", "frequency": "quarterly", "units": "percent of GDP",
     # Out about 112 days after the quarter (2026-Q1 on 2026-07-21), so a
     # quarter dated at its start is ~295 days old when the next one lands.
     "staleness_days": 310,
     "name_tmpl": "General government gross debt — {geo}", "notes": "consolidated"},
    {"prefix": "hicp_core_", "code": "prc_hicp_minr?unit=RCH_A&coicop18=TOT_X_NRG_FOOD",
     "label": "HICP core annual rate", "frequency": "monthly", "units": "percent",
     "name_tmpl": "HICP core annual rate — {geo}",
     "notes": "all items excluding energy, food, alcohol and tobacco: Eurostat's core. INE's own core for Spain (es_cpi_core_ine) excludes only unprocessed food and energy, so the two differ. A component of headline HICP (hicp_yoy_ for the same geography); "
              "annual rate, ECOICOP v2 dataset"},
    {"prefix": "hicp_energy_", "code": "prc_hicp_minr?unit=RCH_A&coicop18=NRG",
     "label": "HICP energy annual rate", "frequency": "monthly", "units": "percent",
     "name_tmpl": "HICP energy annual rate — {geo}",
     "notes": "energy: fuels, electricity, gas, heating. A component of headline HICP (hicp_yoy_ for the same geography); "
              "annual rate, ECOICOP v2 dataset"},
    {"prefix": "hicp_food_", "code": "prc_hicp_minr?unit=RCH_A&coicop18=FOOD",
     "label": "HICP food annual rate", "frequency": "monthly", "units": "percent",
     "name_tmpl": "HICP food annual rate — {geo}",
     "notes": "food including alcohol and tobacco, processed and unprocessed. A component of headline HICP (hicp_yoy_ for the same geography); "
              "annual rate, ECOICOP v2 dataset"},
    {"prefix": "hicp_goods_", "code": "prc_hicp_minr?unit=RCH_A&coicop18=IGD_NNRG",
     "label": "HICP goods annual rate", "frequency": "monthly", "units": "percent",
     "name_tmpl": "HICP goods annual rate — {geo}",
     "notes": "non-energy industrial goods. A component of headline HICP (hicp_yoy_ for the same geography); "
              "annual rate, ECOICOP v2 dataset"},
    {"prefix": "hicp_services_", "code": "prc_hicp_minr?unit=RCH_A&coicop18=SERV",
     "label": "HICP services annual rate", "frequency": "monthly", "units": "percent",
     "name_tmpl": "HICP services annual rate — {geo}",
     "notes": "services. A component of headline HICP (hicp_yoy_ for the same geography); "
              "annual rate, ECOICOP v2 dataset"},
    {"prefix": "youth_unemp_", "ea_codes": ["EA21"],
     "code": "une_rt_m?s_adj=SA&age=Y_LT25&unit=PC_ACT&sex=T",
     "label": "Youth unemployment", "frequency": "monthly", "units": "percent",
     "name_tmpl": "Youth unemployment rate (under 25) — {geo}",
     "notes": "high-signal for ES/EL/IT; the US and JP geos in this dataset "
              "are not fetched, under Eurostat's non-EU reuse carve-out"},
]


# GDP by expenditure (session C, 2026-10-12): each part is one Eurostat item,
# published as its contribution to real GDP growth, in percentage points, so
# the six add up to growth (imports are already negative). One family per part
# and frequency, because fetch_multi_geo splits by geography only.
#
#   annual     nama_10_gdp CON_PPCH_PRE: contribution to the year's growth
#   quarterly  namq_10_gdp CON_PPCH_SM:  to growth on the same quarter a year
#              earlier, the basis of gdp_yoy_. Eurostat publishes no seasonally
#              adjusted yoy contributions for Germany or the Netherlands
#              (owner's decision: leave them out; gaps.md says so).
#
# The euro area is EA21 only: annual EA20 equals EA21 exactly, and quarterly
# EA21 equals the changing-composition EA where EA20 drifts by up to 0.03 pp.
GDP_PARTS = [  # (part, Eurostat item, words in the name)
    ("hh", "P31_S14_S15", "household consumption"),
    ("gov", "P3_S13", "government consumption"),
    ("gfcf", "P51G", "fixed investment"),
    ("inv", "P52_P53", "inventories and valuables"),
    ("exp", "P6", "exports"),
    ("imp", "P7", "imports"),
]
GDP_CONTRIB_MISSING_Q = ["DE", "NL"]
# A year is dated to its first day and the next one arrives about fifteen
# months after it ends: 2025 (dated 2025-01-01) lands in early 2027.
_ANNUAL_STALENESS = 800


def _gdp_breakdown_sets() -> list[dict]:
    out = [{"prefix": "gdp_annual_", "ea_codes": ["EA21"],
            "code": "nama_10_gdp?na_item=B1GQ&unit=CLV_PCH_PRE",
            "label": "Real GDP growth, annual", "frequency": "annual", "units": "percent",
            "staleness_days": _ANNUAL_STALENESS,
            "name_tmpl": "Real GDP growth, annual — {geo}",
            "notes": "chain-linked volumes, percent change on the previous year. The "
                     "quarterly rate is gdp_yoy_; its parts by expenditure are "
                     "gdp_contrib_ (annual) and gdp_contrib_q_ (quarterly)"}]
    for part, item, words in GDP_PARTS:
        out.append({
            "prefix": f"gdp_contrib_{part}_", "ea_codes": ["EA21"],
            "code": f"nama_10_gdp?na_item={item}&unit=CON_PPCH_PRE",
            "label": f"GDP contribution, {words}, annual", "frequency": "annual",
            "units": "percentage points", "staleness_days": _ANNUAL_STALENESS,
            "name_tmpl": f"Contribution of {words} to annual GDP growth — {{geo}}",
            "notes": f"contribution of {words} ({item}) to the year's real GDP growth, "
                     "as Eurostat publishes it. Imports are negative, so the six parts "
                     "(gdp_contrib_hh_, _gov_, _gfcf_, _inv_, _exp_, _imp_) add up to "
                     "gdp_annual_ for the same geography; net trade is gdp_contrib_net_"})
        out.append({
            "prefix": f"gdp_contrib_q_{part}_", "ea_codes": ["EA21"],
            "missing": GDP_CONTRIB_MISSING_Q,
            "code": f"namq_10_gdp?na_item={item}&unit=CON_PPCH_SM&s_adj=SCA",
            "label": f"GDP contribution, {words}, quarterly", "frequency": "quarterly",
            "units": "percentage points", "staleness_days": 260,
            "name_tmpl": f"Contribution of {words} to GDP growth on a year earlier — {{geo}}",
            "notes": f"contribution of {words} ({item}) to real GDP growth on the same "
                     "quarter a year earlier, seasonally and calendar adjusted, as "
                     "Eurostat publishes it. The six parts add up to gdp_yoy_ for the "
                     "same geography only approximately: each is adjusted on its own, "
                     "so for Greece the gap reaches 1.3 points (2011) and exceeds 0.05 "
                     "in most quarters, for Portugal in about half; the annual parts "
                     "(gdp_contrib_) add up within 0.06. Net trade is "
                     "gdp_contrib_q_net_. Eurostat publishes no such series for "
                     "Germany or the Netherlands"})
    return out


EUROSTAT_BULK_SETS[EUROSTAT_BULK_SETS.index(next(s for s in EUROSTAT_BULK_SETS
                                                  if s["prefix"] == "youth_unemp_")):0] = \
    _gdp_breakdown_sets()

# Bulk families whose rows share source='eurostat' with per-series catalog rows,
# so sync_macro can only tell them apart by series_id prefix.
BULK_PREFIXES = [HICP_PREFIX] + [s["prefix"] for s in EUROSTAT_BULK_SETS]


def _bulk_incremental_start(con, prefix: str) -> str | None:
    """Oldest 'latest date' across a bulk family, as YYYY-MM.

    Eurostat accepts YYYY-MM for quarterly and annual datasets as happily as for
    monthly ones (verified against namq_10_gdp, prc_hpi_q and, for annual,
    nama_10_gdp on 2026-10-12), so one format covers every set."""
    last = con.execute(
        "SELECT min(hi) FROM (SELECT max(date) hi FROM macro_series "
        "WHERE series_id LIKE ? GROUP BY series_id)", [prefix + "%"]).fetchone()[0]
    return f"{last.year}-{last.month:02d}" if last else None


def sync_eurostat_multi(con, incremental=False, prefixes=None) -> list[tuple]:
    """Sync every curated Eurostat multi-geo set (one API call per indicator),
    or only the sets whose series_id prefix is in `prefixes`."""
    from datetime import date as _date, timedelta as _td
    report = []
    for spec in EUROSTAT_BULK_SETS:
        # A geography that stopped publishing is dead after 400 days; an annual
        # value is dated to its year's first day and is already ~650 days old
        # when the next one arrives, so annual families get twice the room.
        cutoff = _date.today() - _td(days=800 if spec["frequency"] == "annual" else 400)
        if prefixes is not None and spec["prefix"] not in prefixes:
            continue
        prefix, label = spec["prefix"], spec["label"]
        start = _bulk_incremental_start(con, prefix) if incremental else None
        try:
            per_geo, geo_labels = eurostat.fetch_multi_geo(spec["code"], start=start)
        except Exception as e:  # noqa: BLE001
            # One set failing must never abort the others.
            report.append((label, "FAIL", error_detail(e, 60)))
            _pace("eurostat")
            continue

        cat_rows, n_geo = [], 0
        for geo in family_geos(spec):
            assert geo not in _NON_EU_GEOS, f"{geo} is outside Eurostat's reuse grant"
            df = per_geo.get(geo)
            if df is None or df.empty:
                continue                      # dataset doesn't publish this geo
            if df["date"].max() < cutoff:
                report.append((f"{prefix}{geo.lower()}", "skip", "dead/stale geo"))
                continue
            sid = f"{prefix}{geo.lower()}"
            if sid in ALIASES:
                continue                      # stored under its canonical id
            store.upsert_macro_series(con, sid, df)
            n_geo += 1
            cat_rows.append({
                "series_id": sid, "source": "eurostat",
                "source_code": f"{spec['code']}&geo={geo}",
                "name": spec["name_tmpl"].format(geo=geo_labels.get(geo, geo)),
                "frequency": spec["frequency"], "units": spec["units"],
                "notes": spec["notes"], "staleness_days": spec.get("staleness_days"),
                "license": licensing.ATTRIBUTION,
            })
        if cat_rows:
            store.upsert_macro_catalog(con, pd.DataFrame(cat_rows))
        report.append((label, "ok", f"{n_geo} geos"))
        _pace("eurostat")
    return report


def bulk_summary_specs() -> list[tuple[str, str, int]]:
    """(label, series_id prefix, staleness allowance in days) per bulk family.

    validate summarises these instead of printing one line per geo — 11 sets ×
    13 geos would bury the real warnings under 140 routine ones."""
    out = [("broad-Europe HICP", HICP_PREFIX, 90),
           ("World Bank commodities", WB_PREFIX, 90),
           ("CFTC positioning", COT_PREFIX, 30)]
    for s in EUROSTAT_BULK_SETS:
        out.append((s["label"], s["prefix"],
                    s.get("staleness_days") or (220 if s["frequency"] == "quarterly" else 90)))
    return out


# Pink Sheet series that need more than the shared licence note.
PINK_SHEET_NOTES = {
    "wb_crude_brent": ("Dated Brent, the physical North Sea cargo price, monthly average, as "
                       "the World Bank publishes it. The futures price news reports quote (ICE "
                       "Brent front month) is a different measure and can diverge: in "
                       "September-October 2026 it sat about $20 below Dated Brent. "
                       + worldbank.CCBY),
}


def sync_pink_sheet(con, incremental=False) -> list[tuple]:
    """World Bank Pink Sheet commodities → wb_* macro series (bulk, one download).
    Mirrors sync_hicp_europe: registers its catalog rows at runtime."""
    if not config.PROFILE.get("macro", {}).get("commodities", True):
        return [("worldbank", "skip", "commodities disabled in profile")]
    start = None
    if incremental:
        retired = sorted(RETIRED)
        last = con.execute(
            "SELECT min(hi) FROM (SELECT max(date) hi FROM macro_series "
            f"WHERE series_id LIKE ? AND series_id NOT IN ({', '.join('?' for _ in retired)}) "
            "GROUP BY series_id)", [WB_PREFIX + "%", *retired]
        ).fetchone()[0]
        if last:
            start = f"{last.year}-{last.month:02d}"
    try:
        per_slug, meta = worldbank.fetch_pink_sheet(start=start)
    except Exception as e:  # noqa: BLE001
        return [("worldbank_pink_sheet", "FAIL", error_detail(e, 70))]

    report, cat_rows = [], []
    for slug, df in sorted(per_slug.items()):
        if slug in RETIRED:
            continue
        n = store.upsert_macro_series(con, slug, df)
        m = meta.get(slug, {})
        cat_rows.append({
            "series_id": slug, "source": "worldbank",
            "source_code": f"pinksheet:{m.get('name', slug)}",
            "name": f"{m.get('name', slug)} (World Bank Pink Sheet)",
            "frequency": "monthly", "units": units.from_label(m.get("units", "")),
            "notes": PINK_SHEET_NOTES.get(slug, worldbank.CCBY),
            "license": licensing.ATTRIBUTION,
        })
        report.append((slug, "ok", f"{n} rows"))
    if cat_rows:
        store.upsert_macro_catalog(con, pd.DataFrame(cat_rows))
    return report


def sync_cot(con, incremental=False) -> list[tuple]:
    """CFTC Commitments of Traders → cot_* net non-commercial positioning.

    Bulk, like the Pink Sheet: one annual zip carries every market, so this
    downloads whole years rather than fetching per series.

    Incremental pulls the current year only, plus the previous one in Jan/Feb
    (the year-end reports are revised, and a fresh January file is thin). A
    series with nothing stored forces a full pull regardless, so adding a new
    market backfills itself on the next run without a manual deep backfill.
    """
    if not config.PROFILE.get("macro", {}).get("positioning", True):
        return [("cftc_cot", "skip", "positioning disabled in profile")]
    today = date.today()
    stored = {r[0] for r in con.execute(
        "SELECT DISTINCT series_id FROM macro_series WHERE series_id LIKE ?",
        [COT_PREFIX + "%"]).fetchall()}
    fresh_market = any(sid not in stored for sid in cftc.MARKETS)

    if incremental and not fresh_market:
        years = [today.year] if today.month > 2 else [today.year - 1, today.year]
    else:
        years = list(range(cftc.EARLIEST_YEAR, today.year + 1))

    try:
        per_series, missing = cftc.fetch_cot(years)
    except Exception as e:  # noqa: BLE001
        return [("cftc_cot", "FAIL", error_detail(e, 60))]

    report, cat_rows = [], []
    for sid, spec in cftc.MARKETS.items():
        df = per_series.get(sid)
        if df is None or df.empty:
            report.append((sid, "skip", "no rows in the fetched years"))
            continue
        n = store.upsert_macro_series(con, sid, df)
        cat_rows.append({
            "series_id": sid, "source": "cftc",
            "source_code": f"deacot:{spec['code']}",
            "name": spec["name"], "frequency": "weekly", "units": "contracts",
            "notes": ("net = non-commercial long minus short; Tuesday as-of date "
                      "released the following Friday. Legacy futures-only report; "
                      f"this contract's history starts {spec['start_year']}"),
            "staleness_days": 30, "license": licensing.PUBLIC_DOMAIN,
        })
        report.append((sid, "ok", f"{n} rows"))
    if cat_rows:
        store.upsert_macro_catalog(con, pd.DataFrame(cat_rows))
    if missing:
        # Expected in early January, before the first report of the new year.
        report.append(("cftc_cot", "skip", f"no archive for {', '.join(missing)}"))
    return report


def sync_macro(con, incremental=False, only=None, prime=True) -> list[tuple]:
    """Fetch every per-series catalog row from its publisher.

    Personal-use series are fetched like any other where they may be stored:
    this is a private local store, filled with the user's own keys for their
    own use. A missing key skips its series without a network call.

    A series whose catalog source differs from the one its stored history came
    from (`filled_from`) has been re-pointed: it is pulled in full and its
    history replaced, never topped up. If the new source fails or has no key,
    the old history stays and the swap is retried on the next run.

    A `cache = session` row is never fetched here: it may be held for a
    conversation (ingest/live.py) but not written. If it still has stored
    history — it was a store row once — that history is deleted.
    """
    report = []
    src_ph = ", ".join("?" for _ in BULK_SOURCES)
    pfx_clause = "".join(" AND series_id NOT LIKE ?" for _ in BULK_PREFIXES)
    cat = con.execute(
        f"SELECT series_id, source, source_code, filled_from, "
        f"coalesce(cache, 'store') AS cache FROM macro_catalog "
        f"WHERE source NOT IN ({src_ph}){pfx_clause}{_NOT_FX} ORDER BY series_id",
        list(BULK_SOURCES) + [p + "%" for p in BULK_PREFIXES]
    ).fetch_df()
    if only is not None:
        cat = cat[cat["series_id"].isin(only)]
    for sid in cat.loc[cat["cache"] == licensing.SESSION, "series_id"]:
        n = store.forget_macro_series(con, sid)
        if n:
            report.append((sid, "ok", f"session-only: {n} stored rows removed"))
    cat = cat[cat["cache"] != licensing.SESSION]
    # A caller looping one series at a time (the MCP server's background job)
    # primes the batching adapters once for the whole run and passes prime=False,
    # so BLS still answers every series from one request per window.
    if prime:
        prime_batches(cat)
    for _, row in cat.iterrows():
        sid = row["series_id"]
        origin = f"{row['source']}:{row['source_code']}"
        repointed = row["filled_from"] != origin
        start = (_incremental_start(con, "macro_series", "series_id", sid)
                 if incremental and not repointed else None)
        try:
            df = _fetch_macro(row, start)
            if repointed:
                if df.empty:
                    raise ValueError(f"{origin} returned no rows; old history kept")
                n = store.replace_macro_series(con, sid, df, origin)
                was = row["filled_from"]
                detail = f"{n} rows" + (f" (replaced {was})" if isinstance(was, str) else "")
            else:
                detail = f"{store.upsert_macro_series(con, sid, df)} rows"
            report.append((sid, "ok", detail))
            _record_next_release(con, sid, row)
        except MissingKey as e:
            report.append((sid, "skip", str(e)))
            continue  # no network call happened — don't pace
        except Exception as e:  # noqa: BLE001
            report.append((sid, "FAIL", error_detail(e, 200)))
        _pace(row["source"])
    return report


def _record_next_release(con, sid: str, row) -> None:
    """Keep the next release a publisher named (ingest/calendars.py): an ONS
    series in the response just read, a National Bank of Kazakhstan series on
    its statistics page, which costs one more request per page. Only series
    with a calendar row are asked. Never fails the series."""
    try:
        if row["source"] == "ons":
            calendars.record_publisher(con, sid, calendars.parse_ons_next(
                *(ons.next_release(row["source_code"]) or (None, None, "monthly"))))
        elif row["source"] == "nbk" and releases.calendar_key(sid):
            rule = releases.rule_for(sid)
            if rule is None:
                return
            calendars.record_publisher(con, sid, calendars.parse_nbk_next(
                nbk.next_release(row["source_code"]), rule["at"]), feed="nbk_next_release")
    except Exception:  # noqa: BLE001 — a date we could not keep is not a failed series
        pass


# Names for the curated Eurostat geographies, so a series is findable before its
# set has been downloaded; the download replaces them with Eurostat's own labels.
GEO_NAMES = {"EA": "Euro area", "EA20": "Euro area (20)", "EA21": "Euro area (21)",
             "EU27_2020": "European Union (27)",
             "DE": "Germany", "FR": "France", "IT": "Italy", "ES": "Spain",
             "NL": "Netherlands", "BE": "Belgium", "AT": "Austria", "PT": "Portugal",
             "IE": "Ireland", "EL": "Greece", "FI": "Finland"}


def static_catalog_rows() -> pd.DataFrame:
    """Catalog rows knowable without any download: the derived series and the
    curated Eurostat geographies. A fresh store registers them at once, so the
    first question can find `us_breakeven_10y` or `ltgov_yield_de` — the bulk
    stages that used to register them only run on a refresh. (World Bank, CFTC
    and broad-HICP rows are in bulk_catalog_rows.)"""
    from bedrok.ingest import derived
    rows = [derived.catalog_row(spec) for spec in derived.DERIVED]
    for spec in EUROSTAT_BULK_SETS:
        for geo in family_geos(spec):
            if f"{spec['prefix']}{geo.lower()}" in ALIASES:
                continue                      # answered under its canonical id
            rows.append({
                "series_id": f"{spec['prefix']}{geo.lower()}", "source": "eurostat",
                "source_code": f"{spec['code']}&geo={geo}",
                "name": spec["name_tmpl"].format(geo=GEO_NAMES.get(geo, geo)),
                "frequency": spec["frequency"], "units": spec["units"],
                "notes": spec["notes"], "staleness_days": spec.get("staleness_days"),
                "license": licensing.ATTRIBUTION, "cache": licensing.STORE,
            })
    return pd.DataFrame(rows + fx_catalog_rows())


def bulk_catalog_rows() -> pd.DataFrame:
    """The Pink Sheet, CFTC and broad-HICP rows as a refreshed store had them
    (seeds/bulk_catalog.csv), less anything retired since. Their units and
    geography labels exist only in the downloads, hence a seed, not code."""
    df = pd.read_csv(BULK_CATALOG_CSV, dtype=str)
    df["staleness_days"] = pd.to_numeric(df["staleness_days"]).astype("Int64")
    return df[~df["series_id"].isin(RETIRED | set(ALIASES))].reset_index(drop=True)


# Bulk families whose series ids only a download names: asking for an unknown
# id with one of these prefixes runs the stage that would register it.
BULK_STAGE_FOR_PREFIX = {WB_PREFIX: "pinksheet", COT_PREFIX: "cot", HICP_PREFIX: "hicp"}


def prime_batches(cat: pd.DataFrame) -> None:
    """Tell the batching adapters (bls, fedboard) which codes a run will fetch."""
    for source, fn in MACRO_PRIMERS.items():
        fn(cat.loc[cat["source"] == source, "source_code"].tolist())


def per_series_catalog(con, only=None) -> pd.DataFrame:
    """The store-cache, per-series catalog rows sync_macro would fetch."""
    src_ph = ", ".join("?" for _ in BULK_SOURCES)
    pfx_clause = "".join(" AND series_id NOT LIKE ?" for _ in BULK_PREFIXES)
    cat = con.execute(
        f"SELECT series_id, source, source_code FROM macro_catalog "
        f"WHERE source NOT IN ({src_ph}){pfx_clause}{_NOT_FX} "
        f"AND coalesce(cache, 'store') = 'store' ORDER BY series_id",
        list(BULK_SOURCES) + [p + "%" for p in BULK_PREFIXES]).fetch_df()
    return cat if only is None else cat[cat["series_id"].isin(only)]


def clear_download_caches() -> None:
    """Forget every whole-file download cached for a run. Harmless in a CLI
    process, essential in a long-lived one (the MCP server): an lru_cache never
    expires, so without this a refresh would re-read yesterday's file."""
    import importlib
    for name in ("bea", "fedboard", "fedfiles", "nbk", "bns"):
        mod = importlib.import_module(f"bedrok.ingest.adapters.{name}")
        for attr in ("_download", "_table"):
            fn = getattr(mod, attr, None)
            if fn is not None and hasattr(fn, "cache_clear"):
                fn.cache_clear()


# The six FX pairs answer as series (session D2, the owner, 2026-10-14):
# fx_rates stays the table the engines and the CLI read, and sync_fx mirrors
# each pair into macro_series under its lower-case id, with a catalog row, so
# every series tool answers `eurusd` with no FX branch of its own. The macro
# stage never fetches these rows (the _NOT_FX guard): sync_fx owns them.
# An inverse is credited to the publisher of the rate it inverts. A pair XXXYYY
# is YYY per 1 XXX (CLAUDE.md rule 5).
_FX = [
    # id, source, source_code, name, quote ccy per base, publisher's words
    ("eurusd", "ecb", "EXR/D.USD.EUR.SP00.A", "Euro in US dollars, EURUSD (ECB reference rate)",
     "USD per EUR", "the ECB's daily euro reference rate, set around 14:15 CET on TARGET days"),
    ("eurgbp", "ecb", "EXR/D.GBP.EUR.SP00.A", "Euro in pounds sterling, EURGBP (ECB reference rate)",
     "GBP per EUR", "the ECB's daily euro reference rate, set around 14:15 CET on TARGET days"),
    ("gbpusd", "fedboard", "H10:RXI$US_N.B.UK",
     "Pound sterling in US dollars, GBPUSD (Fed H.10 noon rate)",
     "USD per GBP", "the Fed's H.10 noon buying rate in New York, published weekly"),
    ("usdeur", "ecb", "1/EURUSD", "US dollar in euros, USDEUR (inverse of the ECB rate)",
     "EUR per USD", "the inverse of eurusd"),
    ("gbpeur", "ecb", "1/EURGBP", "Pound sterling in euros, GBPEUR (inverse of the ECB rate)",
     "EUR per GBP", "the inverse of eurgbp"),
    ("usdgbp", "fedboard", "1/GBPUSD",
     "US dollar in pounds sterling, USDGBP (inverse of the Fed H.10 rate)",
     "GBP per USD", "the inverse of gbpusd"),
    ("usdkzt", "nbk", "fx:5", "US dollar in tenge, USDKZT (National Bank of Kazakhstan official rate)",
     "KZT per USD", "the National Bank of Kazakhstan's official rate, set the day before the date it "
     "applies to, so the newest row is dated tomorrow; from 1999-10-19, and a managed rate until "
     "the tenge floated in August 2015"),
    ("eurkzt", "nbk", "fx:6", "Euro in tenge, EURKZT (National Bank of Kazakhstan official rate)",
     "KZT per EUR", "the National Bank of Kazakhstan's official rate, set the day before the date it "
     "applies to, so the newest row is dated tomorrow; from 1999-10-19"),
]
FX_IDS = tuple(r[0] for r in _FX)
FX_ALLOWANCE_DAYS = 14   # H.10 publishes weekly; see ops/validate.py
_NOT_FX = " AND series_id NOT IN (" + ", ".join(f"'{i}'" for i in FX_IDS) + ")"


def fx_catalog_rows() -> list[dict]:
    """The pairs' catalog rows, knowable without a download."""
    rows = []
    for sid, src, code, name, units, how in _FX:
        rows.append({
            "series_id": sid, "source": src, "source_code": code, "name": name,
            "frequency": "daily", "units": units,
            "notes": (f"{units}: {how}. "
                      + ("" if code.startswith("1/") or f"{sid[3:]}{sid[:3]}" not in FX_IDS
                         else f"Its inverse is {sid[3:]}{sid[:3]}. ")
                      + f"Stored in fx_rates as {sid.upper()} and refreshed by the fx stage."),
            "staleness_days": FX_ALLOWANCE_DAYS,
            "license": licensing.SOURCE_DEFAULT_LICENSE[src], "cache": licensing.STORE,
        })
    return rows


def _mirror_fx(con, pair: str) -> None:
    """Copy a pair's whole fx_rates history into macro_series (a few thousand
    rows: whole, so the two can never drift)."""
    df = con.execute("SELECT date, rate AS value FROM fx_rates WHERE pair = ? "
                     "ORDER BY date", [pair]).fetch_df()
    store.upsert_macro_series(con, pair.lower(), df)


def _derived_fx(con, expr: str) -> pd.DataFrame:
    """Compute a cross pair from already-stored pairs. expr: '1/EURUSD' or 'A/B'."""
    num, _, den = expr.partition("/")
    den_df = con.execute(
        "SELECT date, rate FROM fx_rates WHERE pair=? ORDER BY date", [den]).fetch_df()
    if num == "1":
        out = den_df.copy()
        out["value"] = 1.0 / out["rate"]
    else:
        num_df = con.execute(
            "SELECT date, rate FROM fx_rates WHERE pair=? ORDER BY date", [num]).fetch_df()
        out = num_df.merge(den_df, on="date", suffixes=("_n", "_d"))
        out["value"] = out["rate_n"] / out["rate_d"]
    return out[["date", "value"]]


def sync_fx(con, incremental=False) -> list[tuple]:
    """Sync every FX pair. A pair whose stored rows came from another source
    is pulled in full and replaced, as sync_macro does for series."""
    report = []
    pairs = config.fx_pairs()
    # Fetchable pairs first so 'derived' ones can be computed from them.
    for pair, src, code in sorted(pairs, key=lambda p: p[1] == "derived"):
        held = {r[0] for r in con.execute(
            "SELECT DISTINCT source FROM fx_rates WHERE pair = ?", [pair]).fetchall()}
        repointed = bool(held) and held != {src}
        start = (_incremental_start(con, "fx_rates", "pair", pair)
                 if incremental and not repointed else None)
        try:
            if src == "derived":
                df = _derived_fx(con, code)
            elif src in MACRO_FETCHERS:
                df = _with_retry(lambda: MACRO_FETCHERS[src](code, start))
            else:
                raise ValueError(f"unknown fx source {src}")
            if repointed:
                if df.empty:
                    raise ValueError(f"{src} returned no rows; old history kept")
                con.execute("DELETE FROM fx_rates WHERE pair = ?", [pair])
            n = store.upsert_fx(con, pair, df, src)
            _mirror_fx(con, pair)
            note = f" (replaced {', '.join(sorted(held))})" if repointed else ""
            report.append((pair, "ok", f"{n} rows ({src}){note}"))
        except Exception as e:  # noqa: BLE001
            report.append((pair, "FAIL", error_detail(e, 70)))
        _pace(src)
    return report


def print_report(title: str, rows: list[tuple]) -> int:
    fails = sum(1 for _, status, _ in rows if status == "FAIL")
    ok = sum(1 for _, status, _ in rows if status == "ok")
    print(f"\n== {title} ==")
    for name, status, detail in rows:
        mark = "!!" if status == "FAIL" else "  "
        print(f"{mark} {name:<24} {status:<5} {detail}")
    print(f"   {ok}/{len(rows)} ok" + (f", {fails} failed" if fails else ""))
    return fails


# ---- One run, one list of stages ---------------------------------------------

def _derived_stage(con, incremental=False):
    from bedrok.ingest import derived
    return derived.sync_derived(con, incremental=incremental)


# Every stage of a refresh, in order: the per-series fetch, then the bulk
# downloads, then the series computed from what they wrote, then FX. The CLI
# (`bedrok refresh`) and the MCP server's background job both read this, so the
# two cannot drift.
REFRESH_STAGES: list[tuple[str, str, callable]] = [
    ("macro", "Macro", sync_macro),
    ("hicp", "HICP Europe", sync_hicp_europe),
    ("eurostat", "Eurostat multi-geo", sync_eurostat_multi),
    ("pinksheet", "Commodities (Pink Sheet)", sync_pink_sheet),
    ("cot", "CFTC positioning", sync_cot),
    ("derived", "Derived series", _derived_stage),
    ("fx", "FX", sync_fx),
    ("calendar", "Release calendars", calendars.sync_calendars),
]


def run_stage(con, name: str) -> list[tuple]:
    """One refresh stage by name (see REFRESH_STAGES)."""
    fn = {n: f for n, _, f in REFRESH_STAGES}[name]
    clear_download_caches()
    return fn(con, incremental=True)


def refresh_series(con, series_id: str) -> tuple[str, list[tuple]]:
    """Refresh one stored series, whatever stage owns it: (stage, report rows).

    A per-series row is fetched on its own (incrementally, or in full when it
    has been re-pointed). A member of a bulk family has no request of its own,
    so its family's download runs — one Eurostat set, the Pink Sheet, the COT
    archive or the derived recomputation. Never prints.

    Raises KeyError for an id not in the catalog and ValueError for a
    session-only series, which may never be written."""
    row = con.execute("SELECT source, coalesce(cache, 'store') FROM macro_catalog "
                      "WHERE series_id = ?", [series_id]).fetchone()
    if row is None:
        raise KeyError(series_id)
    source, cache = row
    if cache == licensing.SESSION:
        raise ValueError(f"{series_id} is session-only: it is fetched live, never stored")
    clear_download_caches()
    if series_id in FX_IDS:
        return "fx", sync_fx(con, incremental=True)
    if source == "worldbank":
        return "pinksheet", sync_pink_sheet(con, incremental=True)
    if source == "cftc":
        return "cot", sync_cot(con, incremental=True)
    if source == "derived":
        # A derived series needs its inputs stored first; on a fresh store
        # they are not, so fetch each empty input before computing.
        from bedrok.ingest import derived
        spec = next((d for d in derived.DERIVED if d["series_id"] == series_id), None)
        rows = []
        for sid in (spec or {}).get("inputs", []):
            if con.execute("SELECT count(*) FROM macro_series WHERE series_id = ?",
                           [sid]).fetchone()[0] == 0:
                rows += refresh_series(con, sid)[1]
        return "derived", rows + _derived_stage(con)
    if series_id.startswith(HICP_PREFIX):
        return "hicp", sync_hicp_europe(con, incremental=True)
    for spec in EUROSTAT_BULK_SETS:
        if series_id.startswith(spec["prefix"]):
            return "eurostat", sync_eurostat_multi(con, incremental=True,
                                                   prefixes={spec["prefix"]})
    return "macro", sync_macro(con, incremental=True, only={series_id})
