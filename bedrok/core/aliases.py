"""One id per concept: the ids that point at another.

The bulk families name a series by family and geography (`hicp_yoy_es`); the
seed names the most-asked ones by geography first (`es_hicp_yoy`), the
convention every instruction teaches. Where both exist they are the same
number from the same dataset, so the family id becomes an alias: it still
answers, with `alias_of` naming the canonical id, but it is neither stored nor
listed (session 11, after the feedback session found the duplicates).

Pure data, in core: nothing here imports a layer above.
"""
from __future__ import annotations

ALIASES: dict[str, str] = {
    "hicp_yoy_ea": "ea_hicp_yoy",
    "hicp_yoy_es": "es_hicp_yoy",
    "hicp_yoy_de": "de_hicp_yoy",
    "hicp_yoy_fr": "fr_hicp_yoy",
    "gdp_qoq_ea20": "ea_gdp_qoq",
    "gdp_qoq_es": "es_gdp_qoq",
}


def canonical(series_id: str) -> str:
    return ALIASES.get(series_id, series_id)
