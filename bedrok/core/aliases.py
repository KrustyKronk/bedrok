"""One id per concept: the ids that point at another.

The bulk families name a series by family and geography (`hicp_yoy_es`); the
seed names the most-asked ones by geography first (`es_hicp_yoy`), the
convention every instruction teaches. Where both exist they are the same
number from the same dataset, so the family id becomes an alias: it still
answers, with `alias_of` naming the canonical id, but it is neither stored nor
listed (session 11, after the feedback session found the duplicates).

An id can also point at the same family under a newer euro-area code: the
surveys' EA20 stopped at 2025-12 when the euro area became EA21, and EA21
carries the whole history, so the EA20 ids answer as EA21 (2026-10-12).

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
    "esi_ea20": "esi_ea21",
    "ind_conf_ea20": "ind_conf_ea21",
    "cons_conf_ea20": "cons_conf_ea21",
    # The UK is not in Eurostat's national accounts; its own series answer
    # the family ids a cross-country question naturally tries (session C).
    "gdp_yoy_uk": "uk_gdp_yoy",
    "gdp_annual_uk": "uk_gdp_annual",
    # The FX pairs are spelled in capitals in fx_rates, the all_series view and
    # the CLI; as series they are lower case (session D2). Both answer.
    **{p: p.lower() for p in ("EURUSD", "EURGBP", "GBPUSD", "USDEUR", "GBPEUR", "USDGBP")},
}


def canonical(series_id: str) -> str:
    return ALIASES.get(series_id, series_id)
