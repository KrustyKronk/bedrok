"""Validation: coverage report + trust checks over the store.

  1. Row counts and date ranges per table, and per-series coverage.
  2. Licence check — every catalog row carries a reviewed terms class.
  3. Staleness report — every series and FX pair vs its expected lag.

Run:  bedrok validate
Exit code is nonzero if any WARN/finding fires. (`--market`, from the days of
the pack, is accepted and ignored.)
"""
from __future__ import annotations

import sys

import pandas as pd

from bedrok.core import config, licensing, store
from bedrok.ingest import pipeline
from bedrok.ops import freshness


def main() -> int:
    con = config.connect(read_only=True)
    problems = 0

    print("\n=== TABLE COVERAGE ===")
    for t in ("macro_catalog", "macro_series", "fx_rates"):
        try:
            if t == "macro_catalog":
                n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                print(f"  {t:<22} {n:>7} rows")
            else:
                n, lo, hi = store.table_range(con, t)
                print(f"  {t:<22} {n:>7} rows   {lo} → {hi}")
        except Exception as e:  # noqa: BLE001
            print(f"  {t:<22} ERROR {e}")
            problems += 1

    print("\n=== MACRO SERIES (first / last / rows) ===")
    dfm = con.execute(
        "SELECT series_id, min(date) lo, max(date) hi, count(*) n "
        "FROM macro_series GROUP BY series_id ORDER BY series_id"
    ).fetch_df()
    for _, r in dfm.iterrows():
        print(f"  {r['series_id']:<24} {str(r['lo'])} → {str(r['hi'])}  ({r['n']})")

    problems += license_check(con)
    problems += staleness_report(con)
    problems += adding_up_check(con)
    problems += identities_check(con)

    con.close()
    print(f"\n{'ALL CHECKS PASSED' if problems == 0 else f'{problems} item(s) need a look'}\n")
    return 1 if problems else 0


def license_check(con) -> int:
    """Every catalog row must carry a reviewed licence class.

      LICENSE-UNREVIEWED  a catalog row has a NULL or unrecognised `license`.
                          Almost always a new cat_rows builder that forgot to set
                          one — and an unreviewed series is exactly the one whose
                          terms nobody has read.

      SESSION-STORED      a `cache = session` row has observations in the
                          store. Its terms forbid keeping them; the next
                          `bedrok refresh` deletes them.

    Personal-use series holding rows are reported as info, never as a finding:
    the store is private and they are for the user's own use.
    """
    problems = 0
    print("\n=== LICENCE (publisher terms) ===")
    unreviewed = licensing.unreviewed_series(con)
    for sid, lic in unreviewed:
        problems += 1
        shown = lic if lic else "NULL"
        print(f"  LICENSE-UNREVIEWED  {sid:<24} license={shown}")

    stored_session = con.execute(
        "SELECT ms.series_id, count(*) FROM macro_series ms JOIN macro_catalog mc "
        "USING (series_id) WHERE mc.cache = ? GROUP BY ms.series_id "
        "ORDER BY ms.series_id", [licensing.SESSION]).fetchall()
    for sid, n in stored_session:
        problems += 1
        print(f"  SESSION-STORED      {sid:<24} {n} rows held; may only be fetched live")

    held = con.execute(
        "SELECT ms.series_id, count(*) FROM macro_series ms JOIN macro_catalog mc "
        "USING (series_id) WHERE mc.license = ? AND coalesce(mc.cache, 'store') = 'store' "
        "GROUP BY ms.series_id ORDER BY ms.series_id", [licensing.RESTRICTED]
    ).fetchall()
    for sid, n in held:
        print(f"  info  {sid:<24} {n} rows held locally (personal use, never passed on)")
    session = licensing.session_series(con)
    if session:
        print(f"  info  session-only, fetched live and never stored: {', '.join(session)}")

    by_class = con.execute(
        "SELECT coalesce(license, 'NULL') l, count(*) FROM macro_catalog "
        "GROUP BY l ORDER BY l").fetchall()
    print("  " + "  ".join(f"{l}={n}" for l, n in by_class))
    if not unreviewed:
        print("  all catalog rows carry a reviewed licence")
    return problems


# ---- Trust layer ------------------------------------------------------------

# The staleness rule lives in ops/freshness.py, shared with doctor and the MCP
# server; re-exported here for callers that know it by this name.
STALENESS_ALLOWANCE = freshness.STALENESS_ALLOWANCE


def staleness_report(con) -> int:
    problems = 0
    print("\n=== STALENESS (max date vs expected publication lag) ===")
    today = pd.Timestamp.now().date()
    # Per-series allowance = explicit catalog override, else the frequency default,
    # else 45d. Bulk families (per-geo Eurostat sets, World Bank commodities, COT)
    # are summarised below instead: ~190 per-geo lines would bury the real signal.
    summaries = pipeline.bulk_summary_specs()
    bulk_clause = " ".join(
        f"AND mc.series_id NOT LIKE '{prefix}%'" for _, prefix, _ in summaries)
    # The FX pairs' catalog rows mirror fx_rates, reported once, below.
    bulk_clause += pipeline._NOT_FX.replace("series_id", "mc.series_id")
    rows = con.execute(
        # Forecast observations (IMF WEO to 2031) never count as fresh data.
        "SELECT mc.series_id, mc.frequency, mc.staleness_days, "
        "max(ms.date) FILTER (WHERE ms.date <= current_date) hi "
        "FROM macro_catalog mc JOIN macro_series ms USING (series_id) "
        f"WHERE coalesce(mc.cache, 'store') = 'store' {bulk_clause} "
        "GROUP BY mc.series_id, mc.frequency, mc.staleness_days ORDER BY mc.series_id"
    ).fetchall()
    for sid, freq, sd, hi in rows:
        allow = freshness.allowance(freq, sd, sid)
        age = (today - hi).days
        if age > allow:
            problems += 1
            print(f"  WARN  {sid:<24} last {hi} ({age}d old, allowance {allow}d)")
    # Bulk families: per-geo members legitimately lag at different speeds
    # (candidate/EFTA countries publish late by design), so flag only when the
    # FRESHEST member has gone stale — that means the whole bulk ingest broke,
    # which is the failure actually worth waking up for.
    for label, prefix, allow in summaries:
        row = con.execute(
            "SELECT count(distinct series_id), max(mx) FROM "
            "(SELECT series_id, max(date) mx FROM macro_series "
            "WHERE series_id LIKE ? GROUP BY series_id)", [prefix + "%"]
        ).fetchone()
        if not row or not row[0]:
            continue
        n, freshest = row
        age = (today - freshest).days
        stale = age > allow
        problems += 1 if stale else 0
        print(f"  {'WARN' if stale else 'info':<4}  {label:<26} {n} series, "
              f"freshest {freshest} ({age}d, allowance {allow}d)")
    # FX allowance is 14d, not 7d: the H.10 GBPUSD rate (Fed Board release
    # file) publishes weekly, so a 7d bar fired on every single run. A warning that is always
    # on is a warning nobody reads — this keeps a clean store at "ok" and
    # reserves the WARN for genuinely broken FX.
    for pair, hi in con.execute(
            "SELECT pair, max(date) FROM fx_rates GROUP BY pair").fetchall():
        age = (today - hi).days
        if age > pipeline.FX_ALLOWANCE_DAYS:
            problems += 1
            print(f"  WARN  {pair:<24} last rate {hi} ({age}d old, "
                  f"allowance {pipeline.FX_ALLOWANCE_DAYS}d)")

    if problems == 0:
        print("  all series fresh within allowance")
    return problems


if __name__ == "__main__":
    sys.exit(main())


# The six parts of GDP growth by expenditure (session C), and how far their sum
# may sit from the total before it is worth a look. Eurostat's annual parts add
# up within 0.06 points; its quarterly ones are each seasonally adjusted, so
# they miss by more (Greece up to 1.3 points in 2011) and are reported as info.
_PARTS = ("hh", "gov", "gfcf", "inv", "exp", "imp")
_TOLERANCE = 0.1


def adding_up(con) -> list[tuple[str, str, int, float, str]]:
    """(basis, geography, periods compared, worst gap, its date) for every
    geography whose six stored parts and total are both held."""
    parts = "|".join(_PARTS)
    rows = con.execute(f"""
        WITH p AS (
            SELECT regexp_extract(series_id, '^gdp_contrib_(q_)?({parts})_(.+)$', 1) AS q,
                   regexp_extract(series_id, '^gdp_contrib_(q_)?({parts})_(.+)$', 3) AS geo,
                   date, value
            FROM macro_series WHERE regexp_matches(series_id, '^gdp_contrib_(q_)?({parts})_')),
        s AS (SELECT q, geo, date, sum(value) AS total, count(*) AS n FROM p GROUP BY ALL HAVING n = 6),
        g AS (SELECT s.q, s.geo, s.date, abs(s.total - t.value) AS gap FROM s JOIN macro_series t
                ON t.date = s.date
               AND t.series_id = CASE WHEN s.q = '' THEN 'gdp_annual_' ELSE 'gdp_yoy_' END || s.geo)
        SELECT q, geo, count(*) AS n, max(gap) AS worst, arg_max(date, gap) AS at
        FROM g GROUP BY q, geo ORDER BY q, geo""").fetchall()
    return [("quarterly" if q else "annual", geo, n, worst, str(at)) for q, geo, n, worst, at in rows]


def adding_up_check(con) -> int:
    """The parts of GDP growth add up to the total. An annual miss beyond the
    tolerance is a finding (Eurostat's annual parts are additive); a quarterly
    one is reported as info, because each quarterly part is adjusted alone."""
    rows = adding_up(con)
    if not rows:
        return 0
    print("\n=== GDP PARTS ADD UP (six contributions against the total) ===")
    problems = 0
    for basis, geo, n, worst, at in rows:
        if worst <= _TOLERANCE:
            continue
        loud = basis == "annual"
        problems += loud
        print(f"  {'WARN' if loud else 'info':<4}  {basis:<9} {geo:<10} worst gap {worst:.2f} "
              f"points at {at} ({n} periods)")
    ok = sum(1 for *_, worst, _ in rows if worst <= _TOLERANCE)
    print(f"  {ok} of {len(rows)} geography-and-basis pairs add up within {_TOLERANCE} points")
    return problems


# Published identities: a yield is its parts. The Bank of England's 10-year
# nominal zero-coupon yield is its real yield plus its breakeven to 0.0001 on
# every day from 1985 but two, where the Bank's own data are wrong for a day
# and are kept as published (session D2, 2026-10-14): 1996-09-27, real and
# breakeven both 0; 2022-11-28, the real curve ~1.4 points high. At 5 years
# the Bank fits the curves apart and the parts miss by up to 0.07 points in
# 2007-13 and 0.42 in 2003: reported, never a finding.
# (total, parts, tolerance, a miss is a finding, days known to be wrong)
IDENTITIES = [
    ("uk_gilt_10y", ("uk_real_10y", "uk_breakeven_10y"), 0.01, True,
     ("1996-09-27", "2022-11-28")),
    ("uk_gilt_5y", ("uk_real_5y", "uk_breakeven_5y"), 0.1, False, ("1996-09-27", "2022-11-28")),
]


def identities(con) -> list[tuple[str, int, int, float, str, bool, float]]:
    """(total, days compared, days beyond tolerance, worst gap, its date,
    loud, tolerance) for each identity whose total and parts are stored.
    Known-bad days are left out of the count and the worst."""
    out = []
    for total, parts, tol, loud, known in IDENTITIES:
        joins = " ".join(f"JOIN macro_series p{i} ON p{i}.date = t.date "
                         f"AND p{i}.series_id = '{pid}'" for i, pid in enumerate(parts))
        gap = "abs(" + " - ".join(["t.value"] + [f"p{i}.value" for i in range(len(parts))]) + ")"
        skip = "".join(f" AND t.date <> DATE '{d}'" for d in known)
        n, off, worst, at = con.execute(
            f"SELECT count(*), count(*) FILTER (WHERE {gap} > ?), max({gap}), "
            f"arg_max(t.date, {gap}) FROM macro_series t {joins} "
            f"WHERE t.series_id = ?{skip}", [tol, total]).fetchone()
        if n:
            out.append((total, n, off, worst, str(at), loud, tol))
    return out


def identities_check(con) -> int:
    rows = identities(con)
    if not rows:
        return 0
    print("\n=== IDENTITIES (a yield against its parts) ===")
    problems = 0
    for total, n, off, worst, at, loud, tol in rows:
        bad = off > 0 and loud
        problems += bad
        tag = "WARN" if bad else ("info" if off else "ok")
        print(f"  {tag:<4}  {total:<14} {off} of {n} days beyond {tol} points, "
              f"worst {worst:.4f} at {at}")
    return problems
