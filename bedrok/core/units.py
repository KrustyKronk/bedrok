"""The units vocabulary: how a catalog row says what its numbers are.

`units` is the authority for a series' currency and scale (CLAUDE.md rule 5),
so it is written out rather than abbreviated: "USD per barrel", never "$/bbl".
An index names its base ("index 2020=100"), because two indices of the same
thing on different bases are not comparable and nothing else says which is
which. A test checks every seed row and every code-built row against this.

Pure data, in core: nothing here imports a layer above.
"""
from __future__ import annotations

import re

CURRENCIES = r"(USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|SEK|NOK|DKK|KZT)"
SCALES = r"(thousands|millions|billions)"

GRAMMAR = [
    r"percent",
    r"percent annualised",
    r"percent of GDP",
    r"percent change on (previous|a year earlier) (month|quarter)",
    r"percentage points( of GDP)?",
    r"net percent",
    r"balance",                                      # survey balance: % positive minus % negative
    r"index [A-Za-z0-9-]+=100",                      # index 2020=100, index Sep-2025=100
    r"index long-term average=100",
    r"index points",
    r"index 0=average",
    r"index 0-1",                                    # bounded composite (the ECB's CISS)
    r"diffusion index",
    r"standard deviations from (mean|trend)",
    r"binary 0 or 1",
    r"contracts",
    rf"{CURRENCIES} per {CURRENCIES}",               # an exchange rate
    rf"{CURRENCIES} per [a-z][A-Za-z ]*",            # a price: USD per million Btu
    r"US cents per [a-z][A-Za-z ]*",
    rf"{SCALES} {CURRENCIES}(, [A-Za-z0-9 ,]+)?",    # millions CAD, chained 2017, SAAR
    rf"{SCALES} of [a-z][a-z ]*(, [A-Za-z0-9 ,]+)?", # thousands of persons
    r"number of [a-z][a-z ]*",
]
_FULL = re.compile("|".join(f"(?:{g})" for g in GRAMMAR))


def valid(units: str) -> bool:
    return bool(_FULL.fullmatch(str(units).strip()))


# Which units can share one vertical scale. Two percent series got two scales
# because "percent" and "percent change on previous quarter" are different
# strings (the 2026-10-12 self-test); what matters is the dimension. Each
# index base is its own dimension: two indices on different bases are not
# comparable, which is why the base is in the units at all.
_RATE = re.compile(r"percent( annualised|, annualised| change on .*)?|percentage points|net percent|balance")
_MONEY = re.compile(rf"{SCALES} {CURRENCIES}.*")
_SD = re.compile(r"standard deviations from (mean|trend)|index 0=average")


def dimension(units: str) -> str:
    """The scale a unit belongs to: 'rate' for every percentage-like unit,
    'share of GDP', 'money:USD' (millions and billions alike), 'index:<base>',
    'sd', or the unit itself when nothing else applies."""
    u = str(units).strip()
    if _RATE.fullmatch(u):
        return "rate"
    if u in ("percent of GDP", "percentage points of GDP"):
        return "share of GDP"
    m = _MONEY.fullmatch(u)
    if m:
        return f"money:{m.group(2)}"
    m = re.fullmatch(r"index ([A-Za-z0-9-]+)=100", u)
    if m:
        return f"index:{m.group(1)}"
    if _SD.fullmatch(u):
        return "sd"
    return u


def is_money_level(units: str) -> bool:
    """A level in money terms (millions USD, billions GBP): it grows with the
    economy, so its percentile mostly says how recent it is."""
    return bool(_MONEY.fullmatch(str(units).strip()))


# The World Bank Pink Sheet's own unit labels, written out.
_PER = {"mt": "metric ton", "kg": "kilogram", "bbl": "barrel", "troy oz": "troy ounce",
        "mmbtu": "million Btu", "cubic meter": "cubic meter", "dmtu": "dry metric ton unit",
        "sheet": "sheet"}


def from_label(label: str) -> str:
    """'($/mt)' → 'USD per metric ton'; '(cents/sheet)' → 'US cents per sheet'.
    Anything not recognised is returned stripped of its brackets, and the
    vocabulary test then names it."""
    text = str(label).strip().strip("()").strip()
    m = re.fullmatch(r"(\$|cents|¢)\s*/\s*(.+)", text)
    if not m:
        return text
    unit = _PER.get(m.group(2).strip().lower(), m.group(2).strip().lower())
    return ("USD" if m.group(1) == "$" else "US cents") + f" per {unit}"
