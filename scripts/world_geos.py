"""Rewrite seeds/world_geos.csv from what the IMF, BIS and OECD publish today.

For every world family (bedrok/ingest/world.py), one request with the country
left open, kept to the countries Bedrok places (geography.py) whose series is
still current by the rule the sync uses to call a geography dead, less those
the family skips: `skip_codes`, and any country for which Bedrok already holds
one of the family's `skip_held` ids (the same measure from a closer source).

Run it when a publisher adds or drops a country, then review the diff:

    ./.venv/bin/python scripts/world_geos.py
"""
from __future__ import annotations

import csv
import time
from datetime import date

import pandas as pd

from bedrok.core import sources
from bedrok.core.aliases import ALIASES
from bedrok.ingest import pipeline, world
from bedrok.ingest.adapters import sdmx


def held_ids() -> set[str]:
    """Every id Bedrok carries other than the world families' own."""
    seed = set(pd.read_csv(pipeline.CATALOG_CSV, dtype=str)["series_id"])
    static = set(pipeline.static_catalog_rows()["series_id"])
    bulk = set(pipeline.bulk_catalog_rows()["series_id"])
    own = {sid for sid in static if sid.startswith(world.PREFIXES)}
    return (seed | static | bulk | set(ALIASES)) - own


def main() -> int:
    held, rows, failed = held_ids(), [], []
    publishers = sources.load()
    for spec in world.WORLD_SETS:
        cutoff = pipeline.dead_geo_cutoff(spec)
        start = "2015" if spec["frequency"] == "annual" else "2015-01"
        try:
            per_area = sdmx.fetch_by(publishers[spec["source"]], spec["code"].format(""),
                                     spec["by"], start=start)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{spec['prefix']}: {pipeline.error_detail(e, 80)}")
            continue
        kept, skipped = [], []
        for area, df in sorted(per_area.items()):
            cc = world.to_code(spec, area)
            if cc is None:
                continue
            past = df[df["date"] <= date.today()]
            if past.empty or past["date"].max() < cutoff:
                continue
            if cc in spec.get("skip_codes", ()) or any(
                    t.format(cc=cc) in held for t in spec.get("skip_held", ())):
                skipped.append(cc)
                continue
            kept.append(area)
        rows += [{"prefix": spec["prefix"], "area": a} for a in kept]
        print(f"{spec['prefix']:<24} {len(kept):>3} countries"
              + (f"; already held: {' '.join(skipped)}" if skipped else ""))
        time.sleep(1.5)
    if failed:
        # A partial seed would silently drop every country of the failed
        # families, so nothing is written.
        print("not written; failed:\n  " + "\n  ".join(failed))
        return 1
    with world.SEED.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["prefix", "area"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {world.SEED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
