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

CURRENCIES = r"(USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|SEK|NOK|DKK)"
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
