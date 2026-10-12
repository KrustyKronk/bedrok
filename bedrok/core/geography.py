"""Places: which geography a series is about, and which words name one.

One table, used by search (a place in the question filters the catalog rather
than being scored like any other word), by the warnings search, and by the
test that every name leads with its geography.

Series ids carry their geography in one of two places: first, by convention
(`es_hicp_yoy`, `us_10y`), or last, for the families the bulk stages build
(`hicp_core_es`, `gdp_qoq_ea20`, `oecd_cli_deu`). Commodity and positioning
series have no geography of their own: "us" in `wb_banana_us` is where the
banana price is quoted, not a country to search by.

Pure data, in core: nothing here imports a layer above.
"""
from __future__ import annotations

import re

# id code → how names begin (the names test) and a readable label.
CODES = {
    "us": "US", "ea": "Euro area", "eu": "EU", "uk": "UK", "jp": "Japan",
    "cn": "China", "ca": "Canada", "au": "Australia", "ch": "Switzerland",
    "de": "Germany", "fr": "France", "it": "Italy", "es": "Spain",
    "nl": "Netherlands", "be": "Belgium", "at": "Austria", "pt": "Portugal",
    "ie": "Ireland", "el": "Greece", "fi": "Finland", "se": "Sweden",
    "no": "Norway", "dk": "Denmark",
}

# Words a person types for a place → its code. Two-letter country codes that
# are also common words (it, no, de, es, at, be, ie) are deliberately absent:
# "it" in a question is a pronoun before it is Italy.
NAMES = {
    "us": "us", "usa": "us", "america": "us", "american": "us", "united states": "us",
    "uk": "uk", "britain": "uk", "british": "uk", "united kingdom": "uk", "england": "uk",
    "euro area": "ea", "eurozone": "ea", "euro-area": "ea", "ea": "ea",
    "eu": "eu", "european union": "eu",
    "japan": "jp", "japanese": "jp", "china": "cn", "chinese": "cn",
    "canada": "ca", "canadian": "ca", "australia": "au", "australian": "au",
    "switzerland": "ch", "swiss": "ch", "germany": "de", "german": "de",
    "france": "fr", "french": "fr", "italy": "it", "italian": "it",
    "spain": "es", "spanish": "es", "netherlands": "nl", "dutch": "nl",
    "belgium": "be", "belgian": "be", "austria": "at", "austrian": "at",
    "portugal": "pt", "portuguese": "pt", "ireland": "ie", "irish": "ie",
    "greece": "el", "greek": "el", "finland": "fi", "finnish": "fi",
    "sweden": "se", "swedish": "se", "norway": "no", "norwegian": "no",
    "denmark": "dk", "danish": "dk",
}

# Where an id suffix spells a place differently: OECD's ISO-3 codes and
# Eurostat's euro-area and EU aggregates.
_SUFFIX = {"usa": "us", "deu": "de", "fra": "fr", "ita": "it", "esp": "es", "gbr": "uk",
           "jpn": "jp", "chn": "cn", "ea20": "ea", "ea21": "ea", "eu27": "eu", "cn": "cn"}

# ISO 3166 alpha-2 where it differs from the id code (the warnings carry ISO).
ISO2 = {**{c: c.upper() for c in CODES}, "uk": "GB", "el": "GR", "ea": "", "eu": ""}

# Sources whose series are not about a country.
_PLACELESS_SOURCES = {"worldbank", "cftc"}


def of_series(series_id: str, source: str | None = None) -> str | None:
    """The geography code a series is about, or None."""
    if source in _PLACELESS_SOURCES:
        return None
    sid = series_id.lower()
    first = sid.split("_", 1)[0]
    if first in CODES:
        return first
    sid = re.sub(r"_eu27_2020$", "_eu27", sid)
    last = sid.rsplit("_", 1)[-1]
    if last in _SUFFIX:
        return _SUFFIX[last]
    return last if last in CODES and "_" in sid else None


def in_text(text: str) -> tuple[set[str], list[str]]:
    """(codes of the places named, the other words), two-word names first."""
    words = re.findall(r"[a-z0-9-]+", text.lower())
    codes, rest, i = set(), [], 0
    while i < len(words):
        pair = " ".join(words[i:i + 2])
        if i + 1 < len(words) and pair in NAMES:
            codes.add(NAMES[pair])
            i += 2
            continue
        if words[i] in NAMES:
            codes.add(NAMES[words[i]])
        else:
            rest.append(words[i])
        i += 1
    return codes, rest


def words_for_iso2(iso2: str) -> set[str]:
    """Every single word in NAMES that names the country with this ISO code."""
    codes = {c for c, iso in ISO2.items() if iso and iso == (iso2 or "").upper()}
    return {w for w, c in NAMES.items() if c in codes and " " not in w}
