"""Historical percentile of a series value — with the sample size, always.

  bedrok pctile us_10y
  bedrok pctile us_hy_oas --value 3.5
  bedrok pctile us_2y --since 2000-01-01

Runs against the unified `all_series` view, so macro series and FX pairs both
resolve. A session-only series (one the store may not hold, such as us_hy_oas)
is fetched live with your own key, ranked in memory and never written. The
whole point: it prints sample_start / sample_end / n_obs alongside the rank, and
shouts when the sample is short — a "92nd percentile" off 3 years of post-2023,
recession-free data (the ICE OAS clamp) must never masquerade as deep history.
"""
from __future__ import annotations

import argparse
import sys


def _fmt(v) -> str:
    return "n/a" if v is None else str(v)


def compute(con, series_id: str, value: float | None = None,
            since: str | None = None, frame=None) -> dict | None:
    """Rank `value` (default: latest obs) within a series' history via all_series,
    or within `frame` (a [date, value] DataFrame held in memory) when given.

    Returns a dict with value/value_date/is_latest/pctile/n/d0/d1/span_years, or
    None when the series has no data. Pure over the connection so it unit-tests
    without a profile."""
    source = "all_series"
    if frame is not None:
        live = frame[["date", "value"]].assign(series_id=series_id)
        con.register("_live_series", live)
        source = "_live_series"
    latest = con.execute(
        f"SELECT date, value FROM {source} WHERE series_id = ? "
        "AND value IS NOT NULL ORDER BY date DESC LIMIT 1", [series_id]
    ).fetchone()
    if latest is None:
        return None
    latest_date, latest_val = latest
    is_latest = value is None
    val = float(latest_val) if is_latest else float(value)

    n, d0, d1, pctile = con.execute(
        "SELECT count(*) AS n, min(date) AS d0, max(date) AS d1, "
        "100.0 * avg(CASE WHEN value <= ? THEN 1 ELSE 0 END) AS pctile "
        f"FROM {source} WHERE series_id = ? AND value IS NOT NULL "
        "AND date >= coalesce(?, DATE '1900-01-01')",
        [val, series_id, since]
    ).fetchone()
    return {"value": val, "value_date": latest_date, "is_latest": is_latest,
            "pctile": pctile, "n": n, "d0": d0, "d1": d1,
            "span_years": (d1 - d0).days / 365.25 if d0 and d1 else 0.0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("series_id", help="e.g. us_10y, us_hy_oas, EURUSD")
    ap.add_argument("--value", type=float, default=None,
                    help="rank this value (default: the latest observation)")
    ap.add_argument("--since", default=None,
                    help="restrict the sample to dates >= this (YYYY-MM-DD)")
    args = ap.parse_args()

    from bedrok.core import config
    from bedrok.ingest import live
    con = config.connect(read_only=True)
    frame = None
    try:
        if live.cache_of(con, args.series_id) == "session":
            try:
                frame = live.fetch(con, args.series_id)
            except live.MissingKey as e:
                print(f"{args.series_id} is never stored and is fetched live, "
                      f"which needs a key: {e}", file=sys.stderr)
                return 2
        r = compute(con, args.series_id, args.value, args.since, frame=frame)
    finally:
        con.close()
    if r is None:
        print(f"no data for series {args.series_id!r} (check the id with "
              f"`bedrok schema` or the cheat-sheet in the skill)", file=sys.stderr)
        return 2

    what = "latest" if r["is_latest"] else "given value"
    tail = "" if not r["is_latest"] else f", {r['value_date']}"
    print(f"\n{args.series_id}: {r['value']:g} ({what}{tail})")
    print(f"  percentile:   {r['pctile']:.1f}  "
          f"({r['pctile']:.0f}% of the sample is at or below it)")
    print(f"  sample:       {_fmt(r['d0'])} → {_fmt(r['d1'])}  ({r['n']:,} obs)")
    if frame is not None:
        print("  fetched live for this command and not stored — the publisher's "
              "terms do not allow keeping it")
    if r["span_years"] < 5:
        print(f"  ⚠ short sample — only {r['span_years']:.1f}y of history; this rank "
              "is not a deep-history percentile (e.g. HY/IG OAS clamp to ~3y).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
