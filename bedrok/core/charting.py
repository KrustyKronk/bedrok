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

from bedrok.core import units as units_vocab

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
    "type": "lines; bars only when every series has a single period; stacked bars "
            "when `form` is stacked_bars (contributions to a total), positive parts "
            "stacked up from zero and negative ones down, with the `total` drawn as a "
            "dot or a line on the same scale",
}


def is_contribution(entry: dict) -> bool:
    """A part of a total measured in percentage points: GDP growth's parts by
    expenditure, from Eurostat or BEA. Known by units and name, so any later
    family named the same way is drawn the same way."""
    return entry.get("units") == "percentage points" and \
        "contribution" in str(entry.get("name", "")).lower()


def adding_up(parts: list[list], total: list, dates: list) -> str | None:
    """A footnote on whether the parts drawn add up to the total over the
    dates where all of them have a value: they do within 0.05 points when all
    are drawn and the publisher's parts are additive; otherwise say by how
    much they miss, and the likeliest reasons."""
    gaps = [(abs(sum(p[i] for p in parts) - total[i]), d) for i, d in enumerate(dates)
            if total[i] is not None and all(p[i] is not None for p in parts)]
    if not gaps:
        return None
    worst, when = max(gaps)
    if worst <= 0.05:
        return "The parts drawn add up to the total within 0.05 points in every period shown."
    return (f"The parts drawn differ from the total by up to {worst:.2f} points ({when}): "
            "a part is not drawn, or the publisher adjusts each part on its own.")

_HOW_WORDS = {
    ("daily", ""): "daily values", ("daily", "last"): "daily values",
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

    One dimension (percent, percentage points and the other rate-like units
    are one): one scale, labelled with the first series' units. Two: the
    first-listed series' dimension on the left, the other on the right. Three
    or more cannot be read on two scales: the first two are placed and
    `problem` says what to do instead."""
    dims = [(i, units_vocab.dimension(u), u) for i, u in series]
    groups: list[str] = []
    label: dict[str, str] = {}
    for _, d, u in dims:
        if d not in groups:
            groups.append(d)
            label[d] = u
    left = [i for i, d, _ in dims if d == groups[0]] if groups else []
    right = [i for i, d, _ in dims if len(groups) > 1 and d == groups[1]]
    out = {"left": {"units": label[groups[0]] if groups else None, "series": left},
           "right": {"units": label[groups[1]], "series": right} if len(groups) > 1 else None,
           "problem": None}
    if len(groups) > 2:
        dropped = [i for i, d, _ in dims if d in groups[2:]]
        out["problem"] = (f"{len(groups)} different units cannot share two scales; "
                          f"{', '.join(dropped)} {'is' if len(dropped) == 1 else 'are'} "
                          "not placed and not drawn. Draw two charts, or ask for "
                          "transform='index_to' so every series is an index.")
    return out


def _span_label(d: date, frequency: str) -> str:
    if frequency == "daily":
        return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"
    if frequency == "quarterly":
        return f"Q{(d.month - 1) // 3 + 1} {d.year}"
    if frequency == "annual":
        return str(d.year)
    return f"{_MONTHS[d.month - 1]} {d.year}"


def index_base_label(when: date, frequency: str | None) -> str:
    """The base period of a rebased series as the units name it, in the
    vocabulary's "index <base>=100" form: the period actually used on the
    chart's calendar, not the date typed ("index 2020Q1=100" when a quarterly
    chart was rebased to 2020-02-15)."""
    if frequency == "quarterly":
        base = f"{when.year}Q{(when.month - 1) // 3 + 1}"
    elif frequency == "annual":
        base = str(when.year)
    elif frequency == "monthly":
        base = f"{_MONTHS[when.month - 1]}-{when.year}"
    else:
        base = when.isoformat()
    return f"index {base}=100"


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
    years = {d.year for d in dates}

    def one(d: date) -> str:
        if frequency == "daily":
            return f"{d.day} {_MONTHS[d.month - 1]}" + (f" {d.year}" if len(years) > 1 else "")
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


def source_line(publishers: list[tuple]) -> str:
    """[(publisher name, personal use?, fetched live?[, outside?])] → one line,
    each publisher once, in order, with its condition when it has one. An
    outside publisher is one the assistant fetched itself; the line says so,
    because the reader must know which part did not come through Bedrok."""
    seen, parts = set(), []
    for name, personal, live, *rest in publishers:
        outside = bool(rest and rest[0])
        if name in seen:
            continue
        seen.add(name)
        cond = [c for c, on in (("personal use only", personal), ("fetched live, not stored", live),
                                ("fetched by Claude, not via Bedrok", outside)) if on]
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
    for e in entries:
        if e.get("outside"):
            notes.append(f"{e.get('name', e['series_id'])}: supplied by the assistant, not from "
                         f"Bedrok ({e.get('publisher') or 'source not given'}).")
    for sid, held in (table.get("carried") or {}).items():
        d = date.fromisoformat(held["last_change"])
        notes.append(f"{names.get(sid, sid)} changes only at decisions; its last change "
                     f"({d.day} {_MONTHS[d.month - 1]} {d.year}, {held['value']:g}) is held to "
                     "the end of the chart.")
    for sid, stepped in (table.get("stepped") or {}).items():
        if stepped:
            notes.append(f"{names.get(sid, sid)} is published less often than {frequency}; "
                         "it is drawn as a step, each value held until the next.")
    for sid, partial in (table.get("partial_last") or {}).items():
        if partial:
            held = (table.get("partial") or {}).get(sid)
            how_much = ""
            if held:
                # How much of the period the point stands on: six trading days
                # of a quarter read as a move unless this is said (self-test).
                a, b = date.fromisoformat(held["first"]), date.fromisoformat(held["last"])
                span = (f"{a.day} to {b.day} {_MONTHS[b.month - 1]} {b.year}" if (a.year, a.month)
                        == (b.year, b.month) else
                        f"{a.day} {_MONTHS[a.month - 1]} to {b.day} {_MONTHS[b.month - 1]} {b.year}")
                obs = "observation" if held["n"] == 1 else "observations"
                how_much = f" (it holds {held['n']} {obs}, {span})" if a != b else \
                    f" (it holds 1 observation, {b.day} {_MONTHS[b.month - 1]} {b.year})"
            notes.append(f"{names.get(sid, sid)}: the latest period is not over yet{how_much}.")
    for sid, short in (table.get("incomplete") or {}).items():
        notes.append(f"{names.get(sid, sid)}: {', '.join(short)} hold only part of their period "
                     "(a month or quarter not published).")
    if table.get("cut"):
        notes.append(table["cut"])
    return notes
