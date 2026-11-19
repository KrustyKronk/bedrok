"""Rewrite seeds/eurostat_geos.csv from what Eurostat publishes today.

For every curated family (pipeline.EUROSTAT_BULK_SETS), one download without a
geo filter, kept to the countries inside Eurostat's reuse grant
(pipeline.REUSE_GEOS) whose series is still current by the same rule the sync
uses to call a geography dead. The aggregates (euro area, EU) are not in the
seed: each family names its own euro-area codes.

Run it when Eurostat adds or drops a country, then review the diff:

    ./.venv/bin/python scripts/eurostat_geos.py
"""
from __future__ import annotations

import csv
import time

from bedrok.ingest import eurostat_geos, pipeline
from bedrok.ingest.adapters import eurostat

AGGREGATES = {"EA", "EA19", "EA20", "EA21", "EU", "EU27_2020", "EEA"}


def main() -> int:
    rows, failed = [], []
    for spec in pipeline.EUROSTAT_BULK_SETS:
        cutoff = pipeline.dead_geo_cutoff(spec)
        try:
            per_geo, _ = eurostat.fetch_multi_geo(spec["code"], start="2015-01")
        except Exception as e:  # noqa: BLE001
            failed.append(f"{spec['prefix']}: {pipeline.error_detail(e, 80)}")
            continue
        geos = sorted(g for g, df in per_geo.items()
                      if g in pipeline.REUSE_GEOS and g not in AGGREGATES
                      and not df.empty and df["date"].max() >= cutoff)
        rows += [{"prefix": spec["prefix"], "geo": g} for g in geos]
        print(f"{spec['prefix']:<22} {len(geos)} countries")
        time.sleep(1)
    if failed:
        # A partial seed would silently drop every country of the failed
        # families, so nothing is written.
        print("not written; failed:\n  " + "\n  ".join(failed))
        return 1
    with eurostat_geos.SEED.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["prefix", "geo"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {eurostat_geos.SEED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
