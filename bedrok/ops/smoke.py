"""Smoke test: one live call per publisher. PASS/FAIL gate.

Run:  bedrok smoke [--keyless]
Exits 0 only if every publisher returns usable data. The checks come from the
source list (bedrok/seeds/sources.csv): each row's `smoke_series` is fetched
through the same path a refresh uses, over the last year. So adding a publisher
adds its check, and a publisher that moved a file or changed an endpoint shows
up here rather than as a silent gap in the store. A source whose key is missing
is SKIP, not FAIL; `--keyless` leaves out the sources that require a key (the
weekly GitHub run has none); a source whose key is optional, like the BLS,
still runs.

A 200 with old data is the quieter failure: a publisher that moved a series can
leave the old one answering, frozen. So a PASS whose latest observation is older
than the series' staleness allowance (plus a fortnight's grace) prints WARN. A
WARN does not fail the run; it is a prompt to look.

Checks are paced by the source list's `pacing_s`, the way a refresh is, so a
run from a shared runner address stays polite.

Bulk stages have no per-series path, so World Bank, CFTC and Eurostat's
multi-geo download keep checks of their own.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import pandas as pd

from bedrok.core import sources
from bedrok.ingest import pipeline
from bedrok.ingest.adapters import cftc, eurostat, fedboard, worldbank
from bedrok.ops import freshness

GRACE_DAYS = 14


def _last(df):
    if df is None or df.empty:
        raise RuntimeError("empty result")
    row = df.dropna(subset=["value"]).iloc[-1]
    return row["date"], float(row["value"])


def checks(keyless: bool = False) -> list[tuple[str, str, int | None, callable]]:
    """(source, description, allowance in days or None, fetch) per check."""
    catalog = pd.read_csv(pipeline.CATALOG_CSV, dtype=str).set_index("series_id")
    start = (date.today() - timedelta(days=400)).isoformat()
    out = []
    for source, row in sources.load().items():
        sid = row["smoke_series"]
        if not sid or (keyless and row["key_required"] == "yes"):
            continue
        entry = catalog.loc[sid]
        allow = freshness.allowance(entry.get("frequency"), entry.get("staleness_days"), sid)
        out.append((source, sid, allow, lambda s=source, c=entry["source_code"]:
                    pipeline._with_retry(lambda: pipeline.MACRO_FETCHERS[s](c, start))))
    # The Fed Board is one source but eight release files, and the Board is
    # retiring its download program around them: one HEAD per file the catalog
    # reads, beyond the one the smoke series downloads, so a file that moves
    # shows here the week it moves.
    fed = catalog[catalog["source"] == "fedboard"]
    smoke_rel = str(catalog.loc[sources.load()["fedboard"]["smoke_series"], "source_code"]
                    ).split(":")[0] if "fedboard" in sources.load() else None
    for rel in sorted({str(c).split(":")[0] for c in fed["source_code"]} - {smoke_rel}):
        out.append(("fedboard", f"{rel} release zip", None,
                    lambda r=rel: fedboard.release_status(r)))
    out += [
        ("worldbank", "Pink Sheet (Brent)", None,
         lambda: worldbank.fetch_pink_sheet()[0]["wb_crude_brent"]),
        ("eurostat", "multi-geo 10y yields (DE)", None,
         lambda: eurostat.fetch_multi_geo("irt_lt_mcby_m?int_rt=MCBY")[0]["DE"]),
        ("cftc", "COT gold positioning", None,
         lambda: cftc.fetch_cot([date.today().year])[0]["cot_gold_net_noncomm"]),
    ]
    return out


def _age(when) -> int:
    return (date.today() - pd.Timestamp(when).date()).days


def main() -> int:
    keyless = "--keyless" in sys.argv[1:]
    print(f"\n{'SOURCE':<12} {'CHECK':<28} {'RESULT':<8} LATEST")
    print("-" * 76)
    failures = skips = warns = 0
    todo = checks(keyless)
    for i, (source, desc, allow, fn) in enumerate(todo):
        if i:
            pipeline._pace(source)
        try:
            when, val = _last(fn())
            stale = allow is not None and _age(when) > allow + GRACE_DAYS
            warns += stale
            result = "WARN" if stale else "PASS"
            note = f"  (older than {allow}d)" if stale else ""
            print(f"{source:<12} {desc:<28} {result:<8} {when}  {val:,.4f}{note}")
        except pipeline.MissingKey as e:
            skips += 1
            print(f"{source:<12} {desc:<28} {'SKIP':<8} {e}")
        except Exception as e:  # noqa: BLE001 - we want every source reported
            failures += 1
            msg = sources.redact(f"{type(e).__name__}: {e}")
            print(f"{source:<12} {desc:<28} {'FAIL':<8} {msg[:160]}")

    print("-" * 76)
    total = len(todo)
    print(f"{total - failures - skips}/{total} checks passed"
          + (f", {warns} with stale data" if warns else "")
          + (f", {skips} skipped for a missing key" if skips else "")
          + (", keyed sources left out" if keyless else "") + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
