"""The chart recipe: what every Bedrok chart says, whoever draws it.

Bedrok does not draw. The assistant in the host does, with whatever chart tool
that host has, so two charts drawn in two places used to look like two
products. What Bedrok can fix is the content: the same title rules, the same
subtitle carrying the units, the same axes, colours, labels, source line and
footnotes, every time. The prose version is bedrok/seeds/charting.md
(bedrok://charting); this module is the arithmetic of it.

The house decisions (2026-10-11): two vertical scales when units differ; units
in the subtitle and on the vertical axis, never in the title; a chart by
default, a table only when asked; short answers.

Pure data, in core: nothing here imports a layer above.
"""
from __future__ import annotations

import math
from datetime import date

# Okabe–Ito, without its yellow (unreadable on white): seven hues that stay
# distinct for the common forms of colour blindness. Assigned in the order the
# series were asked for, so a series keeps its colour across charts.
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#56B4E9", "#CC79A7", "#7F7F7F"]

# The house style, repeated on every recipe so a host that never read
# bedrok://charting still draws it the same way.
LAYOUT = {
    "container": "a card with a border",
    "title": "top, bold; never carries units",
    "subtitle": "under the title; carries the units and how the data is measured",
    "legend": "below the chart; clicking a series hides or shows it",
    "source": "one line under the legend",
    "footnotes": "under the source line, smaller",
    "toggle": "a switch between the chart and a table of the same numbers",
    "y_axis": "labelled with the units; left scale solid lines, right scale dashed",
    "x_axis": "the labels given, every `tick_every`-th one shown",
    "gaps": "leave empty: never draw a line across a null",
    "type": "lines; bars only when every series has a single period",
}

_HOW_WORDS = {
    ("weekly", "last"): "weekly closes", ("weekly", "mean"): "weekly averages",
    ("weekly", "sum"): "weekly totals", ("weekly", "first"): "first value of each week",
    ("monthly", "mean"): "monthly averages", ("monthly", "last"): "month-end values",
    ("monthly", "sum"): "monthly totals", ("monthly", "first"): "first value of each month",
    ("quarterly", "mean"): "quarterly averages", ("quarterly", "last"): "quarter-end values",
    ("quarterly", "sum"): "quarterly totals", ("quarterly", "first"): "first value of each quarter",
    ("annual", "mean"): "annual averages", ("annual", "last"): "year-end values",
    ("annual", "sum"): "annual totals", ("annual", "first"): "first value of each year",
}
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MAX_TICKS = 10


def colour(i: int) -> str:
    return PALETTE[i % len(PALETTE)]


def title_suggestion(names: list[str]) -> str:
    """One series: its name. Two: both. More: the first and a count. The
    assistant may always replace it; it never carries units."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{names[0]} and {len(names) - 1} others"


def axes(series: list[tuple[str, str]]) -> dict:
    """[(id, units)] → which series go on the left scale and which on the right.

    One unit: one scale. Two: the first-listed series' units on the left, the
    other on the right. Three or more cannot be read on two scales: the first
    two groups are placed and `problem` says what to do instead."""
    groups: list[str] = []
    for _, u in series:
        if u not in groups:
            groups.append(u)
    left = [i for i, u in series if u == groups[0]] if groups else []
    right = [i for i, u in series if len(groups) > 1 and u == groups[1]]
    out = {"left": {"units": groups[0] if groups else None, "series": left},
           "right": {"units": groups[1], "series": right} if len(groups) > 1 else None,
           "problem": None}
    if len(groups) > 2:
        dropped = [i for i, u in series if u in groups[2:]]
        out["problem"] = (f"{len(groups)} different units cannot share two scales; "
                          f"{', '.join(dropped)} {'is' if len(dropped) == 1 else 'are'} "
                          "not placed and not drawn. Draw two charts, or ask for "
                          "transform='index_to' so every series is an index.")
    return out


def _span_label(d: date, frequency: str) -> str:
    if frequency == "quarterly":
        return f"Q{(d.month - 1) // 3 + 1} {d.year}"
    if frequency == "annual":
        return str(d.year)
    return f"{_MONTHS[d.month - 1]} {d.year}"


def subtitle(placed: dict, frequency: str, how: str | None, first: date | None,
             last: date | None) -> str:
    """Units first (per scale when there are two), then how the data is
    measured, then the span: "Percent; weekly closes; Oct 2023 to Oct 2026"."""
    def cap(u: str | None) -> str:
        return (u[:1].upper() + u[1:]) if u else ""
    if placed.get("right"):
        units = f"Left: {placed['left']['units']}. Right: {placed['right']['units']}"
    else:
        units = cap(placed["left"]["units"])
    parts = [units]
    parts.append(_HOW_WORDS.get((frequency, how or ""), frequency))
    if first and last:
        parts.append(f"{_span_label(first, frequency)} to {_span_label(last, frequency)}")
    return "; ".join(p for p in parts if p)


def x_labels(dates: list[date], frequency: str) -> dict:
    """Labels by frequency, and how many to skip so about ten show."""
    def one(d: date) -> str:
        if frequency == "weekly":
            return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"
        if frequency == "monthly":
            return f"{_MONTHS[d.month - 1]} {str(d.year)[2:]}"
        if frequency == "quarterly":
            return f"Q{(d.month - 1) // 3 + 1} {str(d.year)[2:]}"
        return str(d.year)
    n = len(dates)
    return {"labels": [one(d) for d in dates],
            "tick_every": max(1, math.ceil(n / MAX_TICKS))}


def source_line(publishers: list[tuple[str, bool, bool]]) -> str:
    """[(publisher name, personal use?, fetched live?)] → one line, each
    publisher once, in order, with its condition when it has one."""
    seen, parts = set(), []
    for name, personal, live in publishers:
        if name in seen:
            continue
        seen.add(name)
        cond = [c for c, on in (("personal use only", personal), ("fetched live, not stored", live)) if on]
        parts.append(name + (f" ({', '.join(cond)})" if cond else ""))
    return "Source: " + "; ".join(parts) + "." if parts else ""


def footnotes(entries: list[dict], table: dict, frequency: str) -> list[str]:
    """Everything the chart cannot say by itself, one short line each: the
    loud warnings for the period shown, periods not over or missing a part,
    a cut, and series drawn as steps."""
    notes: list[str] = []
    names = {e["series_id"]: e.get("name", e["series_id"]) for e in entries}
    for e in entries:
        for w in e.get("warnings") or []:
            period = w.get("period_start") or ""
            if w.get("period_end"):
                period += f" to {w['period_end']}"
            # The whole effect: warning texts are kept short in the seed, and a
            # cut at the first ". " breaks on abbreviations ("incl.").
            line = f"{names[e['series_id']]}, {period}: {(w.get('effect_on_data') or '').strip()}"
            if line not in notes:
                notes.append(line)
    for sid, stepped in (table.get("stepped") or {}).items():
        if stepped:
            notes.append(f"{names.get(sid, sid)} is published less often than {frequency}; "
                         "it is drawn as a step, each value held until the next.")
    for sid, partial in (table.get("partial_last") or {}).items():
        if partial:
            notes.append(f"{names.get(sid, sid)}: the latest period is not over yet.")
    for sid, short in (table.get("incomplete") or {}).items():
        notes.append(f"{names.get(sid, sid)}: {', '.join(short)} hold only part of their period "
                     "(a month or quarter not published).")
    if table.get("cut"):
        notes.append(table["cut"])
    return notes
