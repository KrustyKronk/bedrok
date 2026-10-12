"""Inflation + rate differentials + EURUSD era-correlations (the hedge-tilt toolkit).

  .venv/bin/python src/engines/rates_infl.py [--csv /tmp/panel.csv]

Outputs: latest YoY inflation (US CPI/core CPI/core PCE, EA HICP), policy and 10y
differentials, the carry proxy (us_3m − ecb_estr), and EURUSD↔differential
correlations computed PER ERA (they are regime-dependent — never pool).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from bedrok.core import config  # noqa: E402

ERAS = ["2010-01-01", "2015-01-01", "2022-01-01", "2024-01-01"]


def _series(con, sid):
    df = con.execute(
        "SELECT date, value FROM macro_series WHERE series_id = ? ORDER BY date",
        [sid]).fetch_df()
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["value"]


def main() -> int:
    if config.BASE_CCY != "EUR":
        raise SystemExit(
            "rates_infl is an EUR-investor module (US-vs-euro-area differentials, "
            "hedge-cost carry, EURUSD era-correlations). Generalisation is on the "
            "roadmap; for now it requires base.currency = 'EUR'.")
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    con = config.connect(read_only=True)
    us_cpi = _series(con, "us_cpi"); us_core = _series(con, "us_core_cpi")
    us_pce = _series(con, "us_core_pce"); ea_hicp = _series(con, "ea_hicp_yoy")
    ff = _series(con, "us_fed_funds"); dfr = _series(con, "ecb_deposit_rate")
    us3m = _series(con, "us_3m"); estr = _series(con, "ecb_estr")
    us10 = _series(con, "us_10y"); ea10 = _series(con, "ea_10y_yield")
    fx = con.execute(
        "SELECT date, rate FROM fx_rates WHERE pair='EURUSD' ORDER BY date").fetch_df()
    con.close()
    fx["date"] = pd.to_datetime(fx["date"]); eurusd = fx.set_index("date")["rate"]

    yoy = lambda s: (s / s.shift(12) - 1.0) * 100.0
    print("\nLatest inflation (YoY):")
    for name, s in [("US CPI", yoy(us_cpi)), ("US core CPI", yoy(us_core)),
                    ("US core PCE", yoy(us_pce)), ("EA HICP", ea_hicp)]:
        v = s.dropna()
        print(f"  {name:<12} {v.iloc[-1]:+5.2f}%  ({v.index[-1].date()})")

    # Monthly panel for differentials/correlations.
    m = pd.DataFrame({
        "policy_diff": (ff.resample("ME").last().ffill()
                        - dfr.resample("ME").last().ffill()),
        "carry": (us3m.resample("ME").last().ffill()
                  - estr.resample("ME").last().ffill()),
        "y10_diff": (us10.resample("ME").last().ffill()
                     - ea10.resample("ME").last().ffill()),
        "eurusd": eurusd.resample("ME").last().ffill(),
    }).dropna(subset=["eurusd"])

    latest = m.dropna(subset=["policy_diff", "y10_diff"]).iloc[-1]
    carry_now = m["carry"].dropna().iloc[-1]
    print(f"\nDifferentials now: policy {latest['policy_diff']:+.2f}pts, "
          f"carry (3m−€STR, ≈ hedge cost) {carry_now:+.2f}pts, "
          f"10y {latest['y10_diff']:+.2f}pts, EURUSD {latest['eurusd']:.4f}")

    print("\nEURUSD level correlation with differentials, BY ERA (regime-dependent):")
    for era in ERAS:
        w = m.loc[era:]
        if len(w) < 12:
            continue
        c_pol = w["eurusd"].corr(w["policy_diff"])
        c_10 = w["eurusd"].corr(w["y10_diff"])
        print(f"  since {era[:4]}: vs policy {c_pol:+.2f}, vs 10y {c_10:+.2f}  ({len(w)} months)")

    if args.csv:
        m.to_csv(args.csv, index_label="date")
        print(f"\nMonthly panel → {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
