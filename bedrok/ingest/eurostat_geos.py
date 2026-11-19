"""Which countries each curated Eurostat family is registered and fetched for.

The list is a seed (seeds/eurostat_geos.csv), one row per family and country,
written by scripts/eurostat_geos.py from what Eurostat actually publishes:
every country inside the reuse grant (pipeline.REUSE_GEOS) whose series in that
family is still current. A seed rather than a loop over each download because
a fresh store must be able to find `gdp_yoy_pl` before its first refresh, and
because registering a country a dataset does not fill leaves an empty catalog
row that search finds and describe reports empty.

Its own module so that derived.py (net trade per country) can read it without
importing pipeline, which imports derived.
"""
from __future__ import annotations

import csv

from bedrok.core import config

SEED = config.SEEDS_DIR / "eurostat_geos.csv"


def load() -> dict[str, list[str]]:
    """Family prefix → its countries (Eurostat geo codes), in seed order."""
    out: dict[str, list[str]] = {}
    with SEED.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.setdefault(row["prefix"], []).append(row["geo"])
    return out


COUNTRIES = load()
