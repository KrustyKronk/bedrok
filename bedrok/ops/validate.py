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
        if age > 14:
            problems += 1
            print(f"  WARN  {pair:<24} last rate {hi} ({age}d old, allowance 14d)")

    if problems == 0:
        print("  all series fresh within allowance")
    return problems


if __name__ == "__main__":
    sys.exit(main())
