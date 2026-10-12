"""Bedrok as an MCP server: the store and its publishers, for an AI assistant.

  bedrok serve            stdio MCP server (Claude Desktop, Claude Code, any MCP host)

Seven tools, each answering a question the model would otherwise guess at:

  list_sources     which publishers are wired, whether their keys are set, their terms
  search_series    find a series id in the curated catalog
  describe_series  everything about one series: units, publisher, licence, caveats, freshness
  get_series       observations, optionally transformed (yoy, 3m annualised, rebased)
  percentile       where a value sits in its own history — with the sample, always
  query            read-only SQL over the store
  refresh          bring stored series up to date (named ones now, or all in the background)

How it behaves, and why:
  * Fetch on demand. A stored series that is stale or empty is re-fetched from
    its publisher before it is answered, so the store fills itself as it is
    used. A failed attempt is not retried for six hours, so a publisher that is
    simply late does not cost a download on every question.
  * Session-only series (FRED's) are fetched live for the call and never
    written — the publisher's terms forbid keeping them.
  * Every answer carries its freshness, licence, attribution line and caveats,
    because a caller who has to ask separately will not ask.
  * No connection is held between calls. DuckDB allows one writer or many
    readers, never both, so holding one would block a `bedrok refresh` in a
    terminal (and the reverse). A lock serialises access within this process;
    another process holding the store is reported as "busy", not as a crash.
  * Claude Desktop cuts a tool call off at about a minute, so a full refresh
    runs as a background job, writing one series at a time so reads can
    interleave, and `refresh()` reports its progress.
  * Nothing is written to stdout except the protocol: logs go to stderr, which
    Claude Desktop keeps in ~/Library/Logs/Claude/mcp-server-bedrok.log.
"""
from __future__ import annotations

import logging
import re
import sys
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from typing import Annotated, Any, Literal

import duckdb
import pandas as pd
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from bedrok.core import aliases, charting, config, dates, licensing, sources, store, warnings
from bedrok.core import geography as geo
from bedrok.engines import pctile
from bedrok.ingest import derived, live, pipeline, transforms
from bedrok.ops import docs, freshness

log = logging.getLogger("bedrok.mcp")

INSTRUCTIONS = """\
Bedrok is a local store of public macroeconomic data (central banks, statistics
offices, international organisations), each series fetched from its publisher.

Start with search_series to find an id, describe_series to read what it is,
then get_series or percentile. describe_series lists `related` series (the
parts of a yield, the inputs of a spread); search by concept ("term
premium", "breakeven", "real yield") before concluding Bedrok lacks a
series. Every answer carries freshness, the licence and
an attribution line: cite the source when you quote a number, and read the
caveats — they say when a series is dated in the future, short, revised or
not comparable across a break. When an answer carries `warnings`, the official
number itself is in doubt for that period (a suspended release, a missing
month, a restatement): quote the number only together with its warning.
search_series returns warnings too, even for countries Bedrok does not carry.
To chart anything, use chart_series: it lines series of any frequency up on
one calendar and returns a recipe (title, subtitle with units, one scale or
two, colours, labels, source line, footnotes). Draw it as written, in a card
with the legend below and the source under it; never line raw rows up by
hand (bedrok://charting has the house style). A chart whenever there is more
than one period; a table only when asked.

Keep answers short. A request to show or plot something gets the chart and at
most three short lines: how recent the data is, the footnotes that matter, and
anything the chart cannot show. Warnings are always said. No analysis unless
asked; when asked, give it fully. When you quote a level, say where it sits in
its own history with percentile, unless the reader only asked for a chart.

Rules: quote a percentile only with its sample (start, end, n). Bedrok
measures and contextualises; it never gives investment advice or buy/sell
timing signals. Currency and units are in the catalog, never inferred.
Session-only series (FRED's) are fetched live and are not visible to query().
"""

def _version() -> str:
    try:
        from importlib.metadata import version
        return version("bedrok")
    except Exception:  # noqa: BLE001 — running from a checkout without install
        return "0"


server = MCPServer(name="bedrok", instructions=INSTRUCTIONS, version=_version())

MAX_ROWS = 1000
LOCK_WAIT_S = 20
RETRY_AFTER_S = 6 * 3600
BUSY = ("The Bedrok store is busy: another process (probably `bedrok refresh` in a "
        "terminal) is writing to it. Try again in a minute.")

_lock = threading.Lock()
_last_attempt: dict[str, float] = {}
_job: dict[str, Any] = {"status": "idle"}
_job_thread: threading.Thread | None = None

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
READ_FETCHING = ToolAnnotations(read_only_hint=True, open_world_hint=True)


# ---- plumbing ----------------------------------------------------------------

@contextmanager
def _store(write: bool = False, wait: float | None = LOCK_WAIT_S):
    """A short connection to the store, under the in-process lock."""
    got = _lock.acquire() if wait is None else _lock.acquire(timeout=wait)
    if not got:
        raise ToolError("Bedrok is writing a refresh right now; try again in a moment.")
    try:
        try:
            con = config.connect(read_only=not write)
        except SystemExit as e:
            raise ToolError(sources.redact(str(e))) from None
        except duckdb.Error as e:
            if "lock" in str(e).lower():
                raise ToolError(BUSY) from None
            raise
        try:
            yield con
        finally:
            if write:
                store.close_checkpointed(con)
            else:
                con.close()
    finally:
        _lock.release()


def _iso(v):
    """A JSON-safe value: dates as ISO strings, NaN/NaT/None as None."""
    if v is None or (not isinstance(v, (str, list, dict, tuple)) and pd.isna(v)):
        return None
    if isinstance(v, (date, datetime, pd.Timestamp)):
        return v.isoformat()[:10] if not isinstance(v, datetime) or v.time() == datetime.min.time() \
            else v.isoformat()
    if isinstance(v, float) and v != v:
        return None
    return v


def _catalog_row(con, series_id: str) -> dict:
    """The catalog row for an id; an alias (core/aliases.py) answers with its
    canonical row, marked `alias_of`."""
    canon = aliases.canonical(series_id)
    df = con.execute("SELECT * FROM macro_catalog WHERE series_id = ?", [canon]).fetch_df()
    if df.empty:
        raise ToolError(f"{series_id!r} is not in the catalog. Use search_series to find an id.")
    row = {k: _iso(v) for k, v in df.iloc[0].to_dict().items()}
    if canon != series_id:
        row["alias_of"] = canon
    return row


def _alias(row: dict) -> dict:
    """{"alias_of": id} for an answer asked by an alias, else nothing."""
    return {"alias_of": row["alias_of"]} if row.get("alias_of") else {}


def _find(series_id: str) -> dict:
    """The catalog row for an id. A World Bank, CFTC or broad-HICP id is named
    only by its family's download; on a store that has not run it yet, run it
    once and look again."""
    try:
        with _store() as con:
            return _catalog_row(con, series_id)
    except ToolError:
        stage = next((st for pfx, st in pipeline.BULK_STAGE_FOR_PREFIX.items()
                      if series_id.startswith(pfx)), None)
        if stage is None or time.time() - _last_attempt.get(f"stage:{stage}", 0) < RETRY_AFTER_S:
            raise
        _last_attempt[f"stage:{stage}"] = time.time()
        with _store(write=True) as con:
            pipeline.run_stage(con, stage)
        with _store() as con:
            return _catalog_row(con, series_id)


def _derived_inputs(series_id: str) -> list[str]:
    spec = next((d for d in derived.DERIVED if d["series_id"] == series_id), None)
    return list(spec["inputs"]) if spec else []


def _terms(row: dict, con=None) -> dict:
    src = row["source"]
    pub = sources.load().get(src, {})
    attribution = licensing.ATTRIBUTIONS.get(src, "")
    extra = licensing.SERIES_ATTRIBUTIONS.get(row["series_id"])
    if src == "derived":
        # A computed series is credited to whoever published its inputs.
        inputs = _derived_inputs(row["series_id"])
        if con is not None and inputs:
            ph = ", ".join("?" for _ in inputs)
            srcs = [r[0] for r in con.execute(
                f"SELECT DISTINCT source FROM macro_catalog WHERE series_id IN ({ph}) "
                "ORDER BY source", inputs).fetchall()]
            credits = " ".join(licensing.ATTRIBUTIONS.get(s, "") for s in srcs if s != "derived")
            attribution = (attribution + " " + credits).strip()
    return {
        "publisher": pub.get("name", src),
        "license": row.get("license"),
        "attribution": (attribution + (" " + extra if extra else "")).strip(),
        "terms_url": pub.get("terms_url") or None,
        "caveats": row.get("notes") or "",
    }


def _terms_for(row: dict) -> dict:
    with _store() as con:
        return _terms(row, con)


def _ensure_fresh(series_id: str) -> str:
    """Re-fetch a stored series that is stale or empty. Returns what happened."""
    with _store() as con:
        f = freshness.assess(con, series_id)
    if f is None or f["status"] not in ("stale", "empty"):
        return "store"
    if time.time() - _last_attempt.get(series_id, 0) < RETRY_AFTER_S:
        return "store (stale; a refresh was tried recently and found nothing newer)"
    _last_attempt[series_id] = time.time()
    try:
        with _store(write=True) as con:
            stage, rows = pipeline.refresh_series(con, series_id)
    except ToolError:
        return "store (stale; the store was busy, so it was not refreshed)"
    fails = [d for _, s, d in rows if s == "FAIL"]
    if fails:
        return f"store (stale; refresh failed: {sources.redact(fails[0])[:120]})"
    return f"refreshed now from the publisher ({stage})"


def _key_help(series_id: str, source: str) -> str:
    """How to unlock a series whose publisher needs a key the user has not set."""
    pub = sources.load().get(source, {})
    return (f"{series_id} comes from {pub.get('name', source)}, which needs a free API key "
            f"({pub.get('key_env')}). Get one at {pub.get('key_url')} and add it in Bedrok's "
            "settings (Claude Desktop: Settings → Extensions → Bedrok) or to the .env file "
            "in Bedrok's home folder, then ask again. Everything else works without keys.")


def _load(series_id: str) -> tuple[pd.DataFrame, str, dict]:
    """(observations, how they were obtained, catalog row) for any catalog series."""
    row = _find(series_id)
    series_id = row["series_id"]          # an alias reads its canonical series
    if row.get("cache") == licensing.SESSION:
        try:
            with _store() as con:
                df = live.fetch(con, series_id)
        except live.MissingKey:
            raise ToolError(_key_help(series_id, row["source"])) from None
        except ToolError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"live fetch of {series_id} failed: "
                            f"{pipeline.error_detail(e, 160)}") from None
        return df, "fetched live for this call; not stored (the publisher's terms forbid it)", row
    how = _ensure_fresh(series_id)
    with _store() as con:
        df = con.execute("SELECT date, value FROM macro_series WHERE series_id = ? "
                         "ORDER BY date", [series_id]).fetch_df()
    src = row["source"]
    if df.empty and sources.load().get(src, {}).get("key_required") == "yes" \
            and not sources.key(src):
        raise ToolError(_key_help(series_id, src))
    if df.empty:
        raise ToolError(f"{series_id} has no observations: {how}. Its publisher may not "
                        "publish this geography or period; describe_series has the details.")
    return df, how, row


def _freshness(series_id: str) -> dict:
    with _store() as con:
        f = freshness.assess(con, series_id) or {}
    return {k: _iso(v) for k, v in f.items()}


# ---- tools -------------------------------------------------------------------

@server.tool(annotations=READ)
def list_sources() -> dict[str, Any]:
    """Every publisher Bedrok is wired to: how it is reached, whether it needs an
    API key and whether that key is set (never the key itself), its licence
    class, whether its numbers may be stored or are fetched live only
    (cache = session), its terms page, and how many catalog series it has with
    the freshest date held. Use it to answer "what can Bedrok see?" and to
    explain a missing series (no key, session-only, not wired)."""
    with _store() as con:
        counts = dict(con.execute(
            "SELECT source, count(*) FROM macro_catalog GROUP BY source").fetchall())
        # The freshest observation dated today or earlier; forecasts and survey
        # horizons dated later are reported apart, as `future_to`.
        fresh, ahead = {}, {}
        for src, past, top in con.execute(
                "SELECT mc.source, max(ms.date) FILTER (WHERE ms.date <= current_date), "
                "max(ms.date) FROM macro_series ms JOIN macro_catalog mc "
                "USING (series_id) GROUP BY mc.source").fetchall():
            fresh[src] = past
            if top is not None and (past is None or top > past):
                ahead[src] = top
    out = []
    for src, r in sources.load().items():
        out.append({
            "source": src, "name": r["name"], "protocol": r["protocol"],
            "key_env": r["key_env"] or None, "key_required": r["key_required"],
            "key_set": bool(sources.key(src)) if r["key_env"] else None,
            "license": r["license"], "cache": r["cache"], "terms_url": r["terms_url"] or None,
            "series": counts.get(src, 0), "freshest": _iso(fresh.get(src)),
            **({"future_to": _iso(ahead[src])} if src in ahead else {}),
        })
    return {"sources": out}


# What people ask for → the words series are named by. Small on purpose: only
# where the everyday word never appears in the names (session 11: "spain
# inflation" found a bond spread before Spain's CPI).
_SYNONYMS = {
    "inflation": {"cpi", "hicp", "prices", "price"},
    "prices": {"cpi", "hicp"},
    "growth": {"gdp"},
    "jobless": {"unemployment"},
    "jobs": {"payrolls", "employment", "unemployment"},
}


@server.tool(annotations=READ)
def search_series(
    text: Annotated[str, Field(description="words to look for, e.g. 'euro area inflation', "
                                           "'japan policy rate', 'credit gap'")],
    source: Annotated[str | None, Field(description="limit to one publisher, e.g. 'ecb', 'bis'")] = None,
    frequency: Annotated[Literal["daily", "weekly", "monthly", "quarterly", "annual"] | None,
                         Field(description="limit to one frequency")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 25,
    offset: Annotated[int, Field(ge=0, description="skip this many matches, to page")] = 0,
) -> dict[str, Any]:
    """Find series in the curated catalog by words in their id, name, units,
    notes or publisher. A place in the text ("spain", "euro area", "us",
    "japanese") narrows the search to that geography rather than being matched
    as a word. Ids follow a pattern: a geography prefix (us_, ea_, uk_, jp_,
    de_, …), then the measure — e.g. us_10y, ea_hicp_yoy, jp_policy_rate; the
    bulk families end with the geography instead (hicp_core_es, gdp_qoq_de).
    Empty text lists everything (with source/frequency filters); to walk the
    whole catalog or get counts, use list_series. Returns the best matches with
    their frequency, units, whether they are stored or fetched live, and their
    last stored date, plus `warnings`: documented cases where that place's
    official numbers should not be taken at face value, whether or not Bedrok
    carries the series."""
    places, words = geo.in_text(text)
    with _store() as con:
        cat = con.execute(
            "SELECT mc.series_id, mc.source, mc.name, mc.frequency, mc.units, mc.notes, "
            "coalesce(mc.cache, 'store') AS cache, "
            "max(ms.date) FILTER (WHERE ms.date <= current_date) AS last_date "
            "FROM macro_catalog mc LEFT JOIN macro_series ms USING (series_id) "
            "GROUP BY ALL").fetch_df()
    if source:
        cat = cat[cat["source"] == source]
    if frequency:
        cat = cat[cat["frequency"] == frequency]
    if places and not cat.empty:
        cat = cat[[geo.of_series(sid, src) in places
                   for sid, src in zip(cat["series_id"], cat["source"])]]
    pubs = {s: r["name"].lower() for s, r in sources.load().items()}

    def toks(text) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", str(text).lower()))

    def score(r) -> int:
        # r["name"], never r.name: on a pandas row .name is the index label.
        sid = str(r["series_id"]).lower()
        sid_t, name_t = set(sid.split("_")), toks(r["name"])
        rest_t = toks(" ".join(str(r[c]) for c in ("units", "notes", "source"))
                      + " " + pubs.get(r["source"], ""))
        name = " ".join(re.findall(r"[a-z0-9]+", str(r["name"]).lower()))

        def one(w) -> int:
            n = 4 * (w in sid_t) + 3 * (w in name_t) + (w in rest_t)
            # Part of an id token (10y in 10y2y); never two letters, or "us"
            # would match every usdcad and banana_us.
            n += (len(w) >= 3 and w in sid and w not in sid_t)
            if w not in name_t and len(w) >= 4:      # german → germany, inflat → inflation
                n += 2 * any(t.startswith(w) for t in name_t)
            return n

        total = 0
        for w in words:
            # "inflation" finds the series named CPI or HICP that measure it.
            total += max(one(c) for c in {w} | _SYNONYMS.get(w, set()))
        # Adjacent words found together in the name ("high yield", "policy rate")
        # outrank the same words scattered across a different series.
        total += sum(3 for a, b in zip(words, words[1:]) if f"{a} {b}" in name)
        return total + (10 if "_".join(words) == sid else 0)

    cat = cat.assign(score=cat.apply(score, axis=1)) if not cat.empty else cat
    hits = cat[cat["score"] > 0].sort_values(["score", "series_id"], ascending=[False, True]) \
        if words else cat.sort_values("series_id")
    found = warnings.search(text)
    if not places and found:
        # The text names a place Bedrok does not cover (Argentina, Turkey):
        # any match would be another country's series. Only the warning answers.
        hits = hits.iloc[0:0]
    page = hits.iloc[offset:offset + limit]
    rows = [{"series_id": r.series_id, "name": r.name, "source": r.source,
             "frequency": r.frequency, "units": r.units, "cache": r.cache,
             "last_date": _iso(r.last_date)} for r in page.itertuples()]
    return {"matches": rows, "total_matches": int(len(hits)),
            "next_offset": offset + limit if offset + limit < len(hits) else None,
            # Documented cases where an official number is in doubt, matched by
            # place: they answer even when Bedrok carries nothing for that country.
            "warnings": found}


@server.tool(annotations=READ)
def list_series(
    source: Annotated[str | None, Field(description="one publisher, e.g. 'eurostat', 'bls'")] = None,
    geography: Annotated[str | None, Field(description="a place: 'es', 'spain', 'euro area', 'us'")] = None,
    frequency: Annotated[Literal["daily", "weekly", "monthly", "quarterly", "annual", "event"] | None,
                         Field(description="one frequency")] = None,
    prefix: Annotated[str | None, Field(description="ids starting with this, e.g. 'hicp_core_'")] = None,
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=500)] = 100,
    summary: Annotated[bool, Field(description="counts by source, geography and frequency "
                                               "instead of rows")] = False,
) -> dict[str, Any]:
    """Walk the whole catalog, or summarise it. With summary=true: how many
    series each publisher, geography and frequency has, in one call: the
    answer to "what does Bedrok have?". Otherwise the matching series in id
    order, `limit` at a time; `next_offset` pages on until it is null. For
    finding a series by what it measures, search_series is better."""
    with _store() as con:
        cat = con.execute(
            "SELECT mc.series_id, mc.source, mc.name, mc.frequency, mc.units, "
            "coalesce(mc.cache, 'store') AS cache, "
            "max(ms.date) FILTER (WHERE ms.date <= current_date) AS last_date "
            "FROM macro_catalog mc LEFT JOIN macro_series ms USING (series_id) "
            "GROUP BY ALL ORDER BY mc.series_id").fetch_df()
    cat["geography"] = [geo.of_series(sid, src) for sid, src in zip(cat["series_id"], cat["source"])]
    if source:
        cat = cat[cat["source"] == source]
    if geography:
        places, _ = geo.in_text(geography)
        code = next(iter(places), geography.lower())
        cat = cat[cat["geography"] == code]
    if frequency:
        cat = cat[cat["frequency"] == frequency]
    if prefix:
        cat = cat[cat["series_id"].str.startswith(prefix)]
    if summary:
        def counts(col):
            vc = cat[col].fillna("none").value_counts()
            return {str(k): int(v) for k, v in vc.items()}
        return {"total": int(len(cat)), "by_source": counts("source"),
                "by_geography": counts("geography"), "by_frequency": counts("frequency")}
    page = cat.iloc[offset:offset + limit]
    return {
        "series": [{"series_id": r.series_id, "name": r.name, "source": r.source,
                    "frequency": r.frequency, "units": r.units, "cache": r.cache,
                    "last_date": _iso(r.last_date)} for r in page.itertuples()],
        "total": int(len(cat)),
        "next_offset": offset + limit if offset + limit < len(cat) else None,
    }


@server.tool(annotations=READ)
def describe_series(series_id: str) -> dict[str, Any]:
    """Everything about one series before you use it: name, publisher and the
    publisher's own code, units and frequency, the licence and the attribution
    line to carry, caveats (read them), first and last observation, whether it
    is fresh, stale, empty, or session-only (fetched live, never stored), and
    `related`: series computed from it, its own inputs, and series its caveats
    name — the decompositions and companions worth checking next. `warnings`
    lists every documented case, of any severity, where the official number
    behind this series is in doubt for some period."""
    with _store() as con:
        row = _catalog_row(con, series_id)
        canon = row["series_id"]
        terms = _terms(row, con)
        related = _related(con, canon, row)
    return {
        "series_id": series_id, **_alias(row), "name": row["name"], "source": row["source"],
        "source_code": row["source_code"], "frequency": row["frequency"],
        "units": row["units"], **terms, "related": related,
        "warnings": warnings.for_series(canon),
        "freshness": _freshness(canon),
    }


def _related(con, series_id: str, row: dict) -> list[dict]:
    """Series computed from this one, this one's inputs, and ids its notes name."""
    ids = [d["series_id"] for d in derived.DERIVED if series_id in d["inputs"]]
    ids += _derived_inputs(series_id)
    ids += re.findall(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b", row.get("notes") or "")
    ids = [i for i in dict.fromkeys(ids) if i != series_id]
    if not ids:
        return []
    ph = ", ".join("?" for _ in ids)
    found = dict(con.execute(
        f"SELECT series_id, name FROM macro_catalog WHERE series_id IN ({ph})", ids).fetchall())
    return [{"series_id": i, "name": found[i]} for i in ids if i in found]


@server.tool(annotations=READ_FETCHING)
def get_series(
    series_ids: Annotated[list[str], Field(min_length=1, max_length=5,
                                           description="one to five catalog ids")],
    start: Annotated[str | None, Field(description="first date: YYYY, YYYY-MM or YYYY-MM-DD")] = None,
    end: Annotated[str | None, Field(description="last date: YYYY, YYYY-MM or YYYY-MM-DD")] = None,
    transform: Literal["level", "yoy", "mom3_ann", "index_to"] = "level",
    index_date: Annotated[str | None, Field(description="for index_to: the date set to 100")] = None,
    max_rows: Annotated[int, Field(ge=1, le=MAX_ROWS)] = 300,
    frequency: Annotated[Literal["native", "weekly", "monthly", "quarterly", "annual"],
                         Field(description="put every series on this calendar; "
                                           "native = as published")] = "native",
    how: Annotated[Literal["last", "mean", "sum", "first"] | None,
                   Field(description="how a faster series becomes one value per period; "
                                     "default: last for weekly, mean otherwise")] = None,
) -> dict[str, Any]:
    """Observations for up to five series, newest last. transform: level (as
    published), yoy (% change on a year earlier, by date), mom3_ann (3-month
    change annualised), index_to (rebased to 100 at index_date). A stored series
    that is stale or empty is re-fetched from its publisher first; a
    session-only series is fetched live and not stored — `fetched` says which.

    To chart series of different frequencies together, set `frequency`: Bedrok
    puts them all on one calendar and returns it once, as `table` (dates, and a
    column per series). Weeks are dated by their Friday and take the week's
    last value (a holiday Friday uses the day before); months, quarters and
    years are dated by their first day and take the average. A slower series
    on a faster calendar (the monthly Fed rate beside weekly spreads) repeats
    its value across the period and is marked `stepped`; nothing is ever
    interpolated, and a period with no data stays null. `partial_last` says
    the latest period has not ended; `incomplete` names periods missing a part
    (a quarter with a month never published). Do this rather than lining raw
    rows up yourself. To draw a chart, use chart_series instead: same data,
    plus the recipe.

    When more rows match than max_rows, the most recent are kept and `cut`
    says how many of the oldest went; narrow with start, or use a frequency."""
    start, end = _span(start, end)
    if frequency != "native":
        return _get_aligned(series_ids, start, end, transform, index_date, max_rows,
                            frequency, how)
    out = []
    for sid in series_ids:
        try:
            df, fetched, row = _load(sid)
            df = transforms.apply(df, transform, index_date)
        except (ToolError, ValueError) as e:
            out.append({"series_id": sid, "error": str(e)})
            continue
        if start:
            df = df[df["date"] >= start]
        if end:
            df = df[df["date"] <= end]
        total = len(df)
        df = df.tail(max_rows)
        out.append({
            "series_id": sid, **_alias(row), "name": row["name"],
            "units": _units(row, transform, index_date),
            "transform": transform, "frequency": row["frequency"], "fetched": fetched,
            "observations": [{"date": _iso(d), "value": _iso(float(v))}
                             for d, v in zip(df["date"], df["value"])],
            "total_rows": total, "truncated": total > len(df),
            **_cut(total, len(df), df["date"].min() if len(df) else None),
            **_terms_for(row),
            # Only the loud ones, and only if their period touches the rows sent.
            "warnings": warnings.for_series(row["series_id"], df["date"].min(), df["date"].max(),
                                            loud_only=True) if not df.empty else [],
            "freshness": _freshness(row["series_id"]) if row.get("cache") != licensing.SESSION
            else {"status": "session"},
        })
    return {"series": out}


def _span(start: str | None, end: str | None) -> tuple:
    """start and end as dates, read once for every tool, or a message the
    assistant can act on: a malformed date, or a start after the end, used to
    crash the call or come back empty without a word."""
    try:
        s, e = dates.parse(start, name="start"), dates.parse(end, name="end", end=True)
    except ValueError as err:
        raise ToolError(str(err)) from None
    if s and e and s > e:
        raise ToolError(f"start {s} is after end {e}, so nothing lies between them; "
                        "swap them.")
    return s, e


def _units(row: dict, transform: str, index_date) -> str:
    return row["units"] if transform == "level" else \
        {"yoy": "percent change on a year earlier", "mom3_ann": "percent, annualised",
         "index_to": f"index {index_date}=100"}[transform]


def _cut(total: int, kept: int, first_kept) -> dict:
    """Say which end a truncation removed, and how to get the rest."""
    if total <= kept:
        return {}
    return {"cut": f"the oldest {total - kept} of {total} rows were cut; the answer starts "
                   f"at {_iso(first_kept)}. Narrow with start, or ask for a frequency "
                   "(weekly, monthly) to cover the whole span."}


def _get_aligned(series_ids, start, end, transform, index_date, max_rows,
                 frequency, how) -> dict[str, Any]:
    """get_series with `frequency`: every series on one shared calendar."""
    out, frames, notes = [], {}, {}
    for sid in series_ids:
        try:
            df, fetched, row = _load(sid)
            df, note = transforms.to_frequency(df, row.get("frequency"), frequency, how)
            df = transforms.apply(df, transform, index_date)
        except (ToolError, ValueError) as e:
            out.append({"series_id": sid, "error": str(e)})
            continue
        frames[sid] = (df, row.get("frequency") or "daily", note["stepped"])
        notes[sid] = note
        out.append({
            "series_id": sid, **_alias(row), "name": row["name"],
            "units": _units(row, transform, index_date),
            "transform": transform, "frequency": row["frequency"], "fetched": fetched,
            **_terms_for(row),
            "freshness": _freshness(row["series_id"]) if row.get("cache") != licensing.SESSION
            else {"status": "session"},
        })
    if not frames:
        return {"series": out}
    dates, columns = transforms.align(frames, frequency, start=start, end=end)
    total = len(dates)
    keep = max(total - max_rows, 0)
    dates = dates[keep:]
    columns = {k: v[keep:] for k, v in columns.items()}
    for entry in out:
        sid = entry["series_id"]
        if sid not in columns:
            continue
        have = [d for d, v in zip(dates, columns[sid]) if v is not None]
        entry["warnings"] = warnings.for_series(entry.get("alias_of") or sid, have[0], have[-1],
                                                loud_only=True) \
            if have else []
    table = {
        "frequency": frequency,
        # Only when something was actually aggregated: a monthly series charted
        # monthly was not averaged, and saying so would be false.
        "how": next((n["how"] for n in notes.values() if n["how"]), None),
        "dates": [_iso(d) for d in dates],
        "columns": {k: [_iso(x) for x in v] for k, v in columns.items()},
        "stepped": {k: n["stepped"] for k, n in notes.items()},
        "partial_last": {k: n["partial_last"] for k, n in notes.items()},
        "incomplete": {k: n["incomplete"] for k, n in notes.items() if n["incomplete"]},
        "total_rows": total, "truncated": total > len(dates),
        **_cut(total, len(dates), dates[0] if dates else None),
    }
    return {"series": out, "table": table}


@server.tool(annotations=READ_FETCHING)
def chart_series(
    series_ids: Annotated[list[str], Field(min_length=1, max_length=5,
                                           description="one to five catalog ids, main series first")],
    start: Annotated[str | None, Field(description="first date: YYYY, YYYY-MM or YYYY-MM-DD")] = None,
    end: Annotated[str | None, Field(description="last date: YYYY, YYYY-MM or YYYY-MM-DD")] = None,
    transform: Literal["level", "yoy", "mom3_ann", "index_to"] = "level",
    index_date: Annotated[str | None, Field(description="for index_to: the date set to 100")] = None,
    frequency: Annotated[Literal["auto", "weekly", "monthly", "quarterly", "annual"],
                         Field(description="the chart's calendar; auto = weekly for daily data, "
                                           "otherwise the fastest series' own")] = "auto",
    how: Annotated[Literal["last", "mean", "sum", "first"] | None,
                   Field(description="how a faster series becomes one value per period")] = None,
    title: Annotated[str | None, Field(description="your title; Bedrok suggests one if empty")] = None,
) -> dict[str, Any]:
    """Use this for every chart: when someone asks to show, plot, chart or
    compare series over time. It returns the data on one calendar (`table`)
    and a `chart` recipe to draw it exactly as written, so every Bedrok chart
    looks the same wherever it is drawn:

    title (never with units) and subtitle (units, how measured, span); one
    vertical scale, or two when units differ: `axes.left` solid, `axes.right`
    dashed, each labelled with its units; a colour per series in the order
    given; x labels with every `tick_every`-th shown; legend below the chart
    with series that can be hidden; `source` as one line under the legend;
    `footnotes` under it; a chart/table toggle; nulls left as breaks, never
    joined. `layout` repeats these rules.

    After the chart, say at most three short lines: the as-of date, the
    footnotes that matter, anything the chart cannot show. No analysis unless
    asked. If `axes.problem` is set, follow it rather than adding a third
    scale."""
    start, end = _span(start, end)
    if frequency == "auto":
        frequency = _auto_frequency(series_ids)
    got = _get_aligned(series_ids, start, end, transform, index_date, MAX_ROWS, frequency, how)
    table = got.get("table")
    if not table:
        return got
    entries = [e for e in got["series"] if "error" not in e]
    placed = charting.axes([(e["series_id"], e["units"]) for e in entries])
    right = set((placed.get("right") or {}).get("series", []))
    dates = [pd.Timestamp(d).date() for d in table["dates"]]
    have = [d for i, d in enumerate(dates)
            if any(table["columns"][e["series_id"]][i] is not None for e in entries)]
    got["chart"] = {
        "title": title or charting.title_suggestion([e["name"] for e in entries]),
        "subtitle": charting.subtitle(placed, frequency, table["how"],
                                      have[0] if have else None, have[-1] if have else None),
        "axes": placed,
        "series": [{"id": e["series_id"], "label": e["name"], "units": e["units"],
                    "axis": "right" if e["series_id"] in right else "left",
                    "dashed": e["series_id"] in right, "colour": charting.colour(i),
                    "stepped": table["stepped"].get(e["series_id"], False)}
                   for i, e in enumerate(entries)],
        "x": charting.x_labels(dates, frequency),
        "source": charting.source_line(
            [(e.get("publisher") or e["series_id"], e.get("license") == licensing.RESTRICTED,
              str(e.get("fetched", "")).startswith("fetched live")) for e in entries]),
        "footnotes": charting.footnotes(entries, table, frequency),
        "layout": charting.LAYOUT,
    }
    return got


def _auto_frequency(series_ids: list[str]) -> str:
    """The calendar a chart gets when none is asked for: weekly for anything
    daily (a daily line over years is noise and a lot of rows), otherwise the
    fastest series' own frequency, so nothing slower is averaged away."""
    order = ["weekly", "monthly", "quarterly", "annual"]
    best = "annual"
    for sid in series_ids:
        try:
            f = (_find(sid).get("frequency") or "daily").lower()
        except ToolError:
            continue
        f = "weekly" if f in ("daily", "event", "weekly") else f
        if f in order and order.index(f) < order.index(best):
            best = f
    return best


@server.tool(annotations=READ_FETCHING)
def percentile(
    series_id: str,
    value: Annotated[float | None, Field(description="rank this value instead of the latest")] = None,
    since: Annotated[str | None, Field(description="restrict the sample to dates on or after this: YYYY, YYYY-MM or YYYY-MM-DD")] = None,
) -> dict[str, Any]:
    """Where a value (default: the latest observation) sits in the series' own
    history: the share of the sample at or below it. Always quote it WITH the
    sample — start, end and number of observations — and heed `short_sample`:
    a rank over a few years is not a deep-history percentile."""
    try:
        since_d = dates.parse(since, name="since")
    except ValueError as err:
        raise ToolError(str(err)) from None
    df, how, row = _load(series_id)
    con = duckdb.connect(":memory:")
    try:
        r = pctile.compute(con, series_id, value, since_d, frame=df)
    finally:
        con.close()
    if r is None:
        raise ToolError(f"{series_id} has no observations to rank.")
    if not r["n"]:
        raise ToolError(f"{series_id} has no observations on or after {since_d}; "
                        f"its last is {_iso(r['value_date'])}.")
    out = {k: _iso(v) for k, v in r.items()}
    out.update({
        "series_id": series_id, "name": row["name"], "units": row["units"], "fetched": how,
        "sample": {"start": _iso(r["d0"]), "end": _iso(r["d1"]), "n": r["n"],
                   "years": round(r["span_years"], 1)},
        "short_sample": r["span_years"] < 5,
        **_terms_for(row),
        **_alias(row),
        "warnings": warnings.for_series(row["series_id"], pd.Timestamp(r["d0"]).date(),
                                        pd.Timestamp(r["d1"]).date(), loud_only=True),
    })
    if value is not None:
        # A supplied value has no date of its own; the latest observation's
        # date beside it would read as that day's close.
        out["value_date"] = None
    return out


_SQL_START = ("select", "with", "describe", "show", "summarize", "pragma show", "from")


@server.tool(annotations=READ)
def query(
    sql: Annotated[str, Field(description="one read-only SQL statement")],
    max_rows: Annotated[int, Field(ge=1, le=MAX_ROWS)] = 200,
) -> dict[str, Any]:
    """Read-only SQL (DuckDB dialect) over the store — for joins, aggregates and
    anything the other tools do not cover. Tables: macro_catalog (series_id,
    source, source_code, name, frequency, units, notes, staleness_days, license,
    cache), macro_series (series_id, date, value), fx_rates (pair, date, rate,
    source); view all_series (kind, series_id, date, value). One statement;
    files, URLs and extensions are disabled. Session-only series (cache =
    'session') are never in the store, so SQL cannot see them."""
    stmt = sql.strip().rstrip(";").strip()
    if ";" in stmt:
        raise ToolError("one statement per call, please.")
    if not stmt.lower().startswith(_SQL_START):
        raise ToolError("only read-only statements (SELECT, WITH, DESCRIBE, SHOW, SUMMARIZE).")
    with _store() as con:
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        try:
            cur = con.execute(stmt)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1)
        except duckdb.Error as e:
            raise ToolError(f"SQL error: {sources.redact(str(e))[:400]}") from None
    return {"columns": cols, "rows": [[_iso(v) for v in r] for r in rows[:max_rows]],
            "truncated": len(rows) > max_rows}


def _run_job() -> None:
    """The full refresh, one write at a time so reads can interleave."""
    try:
        pipeline.clear_download_caches()
        with _store(write=True, wait=None) as con:
            store.init_db(con)
            pipeline.load_catalog(con)
            cat = pipeline.per_series_catalog(con)
        pipeline.prime_batches(cat)
        stages = pipeline.REFRESH_STAGES[1:]
        _job.update(total=len(cat) + len(stages))
        for sid in cat["series_id"]:
            _job["current"] = sid
            with _store(write=True, wait=None) as con:
                _record(pipeline.sync_macro(con, incremental=True, only={sid}, prime=False))
        for name, title, fn in stages:
            _job["current"] = title
            with _store(write=True, wait=None) as con:
                _record(fn(con, incremental=True))
        _job.update(status="done", current=None)
    except Exception as e:  # noqa: BLE001 — the job must end in a readable state
        log.exception("refresh job failed")
        _job.update(status="failed", current=None,
                    error=sources.redact(f"{type(e).__name__}: {e}")[:300])
    finally:
        _job["finished_at"] = datetime.now().isoformat(timespec="seconds")


def _record(rows: list[tuple]) -> None:
    _job["done"] = _job.get("done", 0) + 1
    for name, status, detail in rows:
        if status == "FAIL":
            _job["failures"].append({"item": name, "detail": sources.redact(detail)})
        elif status == "ok":
            _job["updated"] += 1


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                         idempotent_hint=True, open_world_hint=True))
def refresh(
    series_ids: Annotated[list[str] | None,
                          Field(max_length=10, description="refresh just these, now")] = None,
) -> dict[str, Any]:
    """Bring stored series up to date from their publishers. With series_ids
    (up to ten), they are refreshed now and the result returned. Without, a
    full refresh of every publisher starts in the background and this returns
    at once; call refresh() again to see its progress. Other tools keep working
    while it runs. Session-only series are never stored, so never refreshed."""
    global _job_thread
    if series_ids:
        results = []
        for sid in series_ids:
            try:
                with _store(write=True) as con:
                    stage, rows = pipeline.refresh_series(con, sid)
                fails = [sources.redact(d) for _, s, d in rows if s == "FAIL"]
                results.append({"series_id": sid, "stage": stage,
                                "status": "failed" if fails else "ok", "detail": fails[:3]})
            except KeyError:
                results.append({"series_id": sid, "status": "unknown id"})
            except ValueError as e:
                results.append({"series_id": sid, "status": "not stored", "detail": [str(e)]})
            except ToolError as e:
                results.append({"series_id": sid, "status": "busy", "detail": [str(e)]})
        return {"mode": "series", "results": results}
    if _job_thread is not None and _job_thread.is_alive():
        return {"mode": "background", **_job}
    if _job.get("status") in ("done", "failed") and _job.get("acknowledged") is False:
        _job["acknowledged"] = True
        return {"mode": "background", "note": "last run finished; call again to start a new one",
                **_job}
    _job.clear()
    _job.update(status="running", started_at=datetime.now().isoformat(timespec="seconds"),
                done=0, total=None, updated=0, failures=[], current=None,
                acknowledged=False)
    _job_thread = threading.Thread(target=_run_job, name="bedrok-refresh", daemon=True)
    _job_thread.start()
    return {"mode": "background", "note": "started; call refresh() again for progress", **_job}


# ---- resources -------------------------------------------------------------------

@server.resource("bedrok://sources", name="sources", mime_type="text/markdown",
                 description="Every publisher Bedrok is wired to: series, key, licence, terms.")
def sources_page() -> str:
    return docs.sources_md()


@server.resource("bedrok://warnings", name="warnings", mime_type="text/markdown",
                 description="Documented cases where an official number should not be "
                             "taken at face value, by country, with sources.")
def warnings_page() -> str:
    return docs.warnings_md()


@server.resource("bedrok://gaps", name="gaps", mime_type="text/markdown",
                 description="What no open source provides, so Bedrok cannot either.")
def gaps_page() -> str:
    return docs.gaps_md()


@server.resource("bedrok://charting", name="charting", mime_type="text/markdown",
                 description="How every Bedrok chart looks: card, title, subtitle with units, "
                             "scales, legend, source line, footnotes, and the short answer around it.")
def charting_page() -> str:
    return docs.charting_md()


@server.resource("bedrok://licenses", name="licenses", mime_type="text/markdown",
                 description="The licence notices: what each class asks, attribution lines, "
                             "series with conditions beyond attribution.")
def licenses_page() -> str:
    return docs.licenses_md()


# ---- entry point ---------------------------------------------------------------

def _prepare_store() -> None:
    """Load the repo's catalog into the store once at start, so a new or
    re-pointed series is known before the first question. Skipped quietly when
    another process holds the store."""
    try:
        with _store(write=True) as con:
            store.init_db(con)
            pipeline.load_catalog(con)
    except ToolError as e:
        log.warning("catalog not reloaded at start: %s", e)
    except Exception:  # noqa: BLE001
        log.exception("catalog not reloaded at start")


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s bedrok-mcp %(levelname)s %(message)s")
    log.info("profile %s, store %s", config.PROFILE_DIR, config.DB_PATH)
    _prepare_store()
    server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
