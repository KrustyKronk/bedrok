"""Warnings: documented cases where an official number should not be taken at
face value, one row each in bedrok/seeds/warnings.csv.

A warning is about a country and a concept over a period: Argentina's CPI from
2007 to 2015, the US household survey in October 2025, Ireland's GDP from 2015.
Most concern series Bedrok does not carry, and they are kept anyway, because
"Bedrok does not have it, and here is why to be careful elsewhere" is an
answer too. The `series` column links a warning to the Bedrok series it
touches, when there are any.

How loud a warning is follows its severity:

  disqualifying, material  ride on every answer that quotes numbers from the
                           period they cover (get_series, percentile)
  caveat                   shown when a series is described or searched for

Every row states what happened and what it did to the data, with a primary
source. None characterises anyone's motives.

Pure data, in core: nothing here imports a layer above.
"""
from __future__ import annotations

import csv
import functools
import importlib.resources
import re
from datetime import date
from pathlib import Path

from bedrok.core import geography

WARNINGS_CSV = Path(str(importlib.resources.files("bedrok") / "seeds" / "warnings.csv"))

COLUMNS = ["warning_id", "country", "iso2", "severity", "concept", "official_series",
           "period_start", "period_end", "what_happened", "effect_on_data", "cross_check",
           "source_name", "source_url", "series"]
SEVERITIES = ("disqualifying", "material", "caveat")
LOUD = frozenset({"disqualifying", "material"})


def period_bounds(row: dict) -> tuple[date, date | None]:
    """'2007' or '2007-01' → the first day; an end of '2015' or '2015-12' → the
    last day it covers; a blank end → None (still open). A blank start means
    the case reaches back as far as the records do (Turkmenistan's national
    accounts) and counts from the earliest date."""
    def parse(text: str, end: bool) -> date | None:
        text = (text or "").strip()
        if not text:
            return None
        m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?", text)
        if not m:
            raise ValueError(f"{row.get('warning_id')}: period {text!r} is not YYYY or YYYY-MM")
        year, month = int(m.group(1)), int(m.group(2) or (12 if end else 1))
        if not end:
            return date(year, month, 1)
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        return date.fromordinal(nxt.toordinal() - 1)
    return parse(row["period_start"], False) or date.min, parse(row["period_end"], True)


@functools.lru_cache(maxsize=1)
def load() -> tuple[dict, ...]:
    with open(WARNINGS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["series_ids"] = tuple(r["series"].split())
    return tuple(rows)


def public(row: dict) -> dict:
    """The fields an answer carries."""
    keys = ["warning_id", "country", "severity", "official_series", "period_start",
            "period_end", "what_happened", "effect_on_data", "cross_check",
            "source_name", "source_url"]
    return {k: row[k] or None for k in keys}


def _overlaps(row: dict, start: date | None, end: date | None) -> bool:
    w0, w1 = period_bounds(row)
    return (end is None or w0 <= end) and (start is None or w1 is None or w1 >= start)


def for_series(series_id: str, start: date | None = None, end: date | None = None,
               loud_only: bool = False) -> list[dict]:
    """Warnings linked to a series. With `start`/`end`, only those whose period
    overlaps that window; with `loud_only`, only disqualifying and material ones."""
    out = []
    for r in load():
        if series_id not in r["series_ids"]:
            continue
        if loud_only and r["severity"] not in LOUD:
            continue
        if (start or end) and not _overlaps(r, start, end):
            continue
        out.append(public(r))
    return out


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


# Names people type for the countries Bedrok itself covers come from
# core/geography.py; these are the extra ones for countries only the warnings
# cover. ISO codes are not used for matching: "in", "it" and "no" are words
# before they are India, Italy and Norway.
_EXTRA = {"china": {"prc"}, "russia": {"russian"}, "turkey": {"turkish", "turkiye"},
          "argentina": {"argentine", "argentinian"}, "india": {"indian"}}


def _place(country: str, iso2: str = "") -> set[str]:
    toks = _tokens(country) - {"and", "of", "the", "republic", "generally"}
    toks |= geography.words_for_iso2(iso2)
    by_name = geography.NAMES.get(country.lower().strip())
    if by_name:
        toks |= geography.words_for_iso2(geography.ISO2.get(by_name, ""))
    for name, extra in _EXTRA.items():
        if name in country.lower():
            toks |= extra
    return toks


def search(text: str, limit: int = 5) -> list[dict]:
    """Warnings whose country and concept match the words asked for. A place
    word must match: "inflation" alone is not a question about Argentina."""
    words = _tokens(text)
    if not words:
        return []
    scored = []
    for r in load():
        place = _place(r["country"], r.get("iso2", ""))
        about = _tokens(r["concept"]) | _tokens(r["official_series"])
        hits_place = words & place
        if not hits_place:
            continue
        scored.append((3 * len(hits_place) + len(words & about), r["warning_id"], r))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [public(r) for _, _, r in scored[:limit]]
