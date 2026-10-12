"""Dates a person types: one reading for every tool that takes one.

`start`, `end` and `since` used to be read three ways (pandas, pandas again,
DuckDB's cast), so "2010" worked as a start and crashed as a `since`, and a
malformed date crashed a whole call with no message (the 2026-10-12
self-test). One function now accepts YYYY, YYYY-MM and YYYY-MM-DD, deliberately
nothing looser, and says so when it refuses.

Pure, in core: nothing here imports a layer above.
"""
from __future__ import annotations

import re
from datetime import date

FORMS = "YYYY, YYYY-MM or YYYY-MM-DD"


def parse(text: str | None, *, name: str = "date", end: bool = False) -> date | None:
    """`text` as a date; None stays None. A year or a month is its first day,
    or its last when `end` is set ("2025" as an end keeps December)."""
    if text is None or not str(text).strip():
        return None
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", str(text).strip())
    if not m:
        raise ValueError(f"{name} {text!r} is not a date; use {FORMS}")
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else (12 if end else 1)
    try:
        if m.group(3):
            return date(year, month, int(m.group(3)))
        if not end:
            return date(year, month, 1)
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        return date.fromordinal(nxt.toordinal() - 1)
    except ValueError:
        raise ValueError(f"{name} {text!r} is not a date; use {FORMS}") from None
