"""Transforms on a [date, value] frame — arithmetic anyone would agree with.

Each works by DATE, not by row count, so the same call is right for daily,
weekly, monthly, quarterly and annual series and is not fooled by a missing
observation: "a year ago" is the observation dated one year earlier (or the
nearest one before it, within a tolerance), never "twelve rows up".

  yoy        percent change on the same date a year earlier
  mom3_ann   change over three months, annualised: ((x / x_-3m) ** 4 - 1) * 100
  index_to   rebased so the value at (or just before) a date is 100

And the calendar a chart needs (session 8): `to_frequency` puts one series on
a weekly, monthly, quarterly or annual calendar, and `align` puts several on
one shared calendar. Resampling comes BEFORE a transform, so a quarterly
average of CPI and then `yoy` is the quarterly-average annual rate, the way
statistics offices quote it.

Pure: pandas in, pandas out, nothing read or written.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

TRANSFORMS = ("level", "yoy", "mom3_ann", "index_to")
# How far before the target date a stand-in observation may sit: a weekend for
# daily series, a week for weekly ones. Monthly and later land exactly.
_TOLERANCE = pd.Timedelta(days=10)


def _frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["date", "value"]].copy()
    out["date"] = pd.to_datetime(out["date"])
    return out.dropna().sort_values("date").reset_index(drop=True)


def _back(df: pd.DataFrame, offset: pd.DateOffset) -> pd.Series:
    """For each row, the value at date − offset (the nearest earlier one)."""
    left = df[["date"]].assign(target=df["date"] - offset).sort_values("target")
    right = df.rename(columns={"date": "target", "value": "prior"})
    m = pd.merge_asof(left, right, on="target", direction="backward",
                      tolerance=_TOLERANCE)
    return m.set_index(left.index)["prior"].reindex(df.index)


def _out(df: pd.DataFrame, values: pd.Series) -> pd.DataFrame:
    res = pd.DataFrame({"date": df["date"].dt.date, "value": values})
    return res.dropna().reset_index(drop=True)


def yoy(df: pd.DataFrame) -> pd.DataFrame:
    df = _frame(df)
    return _out(df, (df["value"] / _back(df, pd.DateOffset(years=1)) - 1) * 100)


def mom3_ann(df: pd.DataFrame) -> pd.DataFrame:
    df = _frame(df)
    return _out(df, ((df["value"] / _back(df, pd.DateOffset(months=3))) ** 4 - 1) * 100)


def index_to(df: pd.DataFrame, when) -> pd.DataFrame:
    df = _frame(df)
    base = df[df["date"] <= pd.Timestamp(when)]
    if base.empty:
        raise ValueError(f"no observation on or before {when} to index to")
    return _out(df, df["value"] / base["value"].iloc[-1] * 100)


def apply(df: pd.DataFrame, transform: str = "level", index_date=None) -> pd.DataFrame:
    if transform == "level":
        return _out(_frame(df), _frame(df)["value"])
    if transform == "yoy":
        return yoy(df)
    if transform == "mom3_ann":
        return mom3_ann(df)
    if transform == "index_to":
        if index_date is None:
            raise ValueError("index_to needs index_date")
        return index_to(df, index_date)
    raise ValueError(f"unknown transform {transform!r}; one of {TRANSFORMS}")


# ---- frequencies -----------------------------------------------------------
#
# A week is dated by its Friday, the week-end markets quote; months, quarters
# and years by their first day, the store's own convention, so a resampled
# monthly series lines up with a stored monthly one.

FREQUENCIES = ("daily", "weekly", "monthly", "quarterly", "annual")
HOWS = ("last", "mean", "sum", "first")
# "daily" has no resample rule: a daily calendar is the union of the days the
# series have (never a weekend slot, which the house rule "never join across
# a null" would turn into a break every week).
_RULE = {"weekly": "W-FRI", "monthly": "MS", "quarterly": "QS", "annual": "YS"}
# How fast a catalog frequency is; `event` (dated at each change, like the ECB's
# rates) counts as daily, and is carried forward rather than averaged.
_RANK = {"daily": 0, "event": 0, "weekly": 1, "monthly": 2, "quarterly": 3, "annual": 4}
# How many of a slower native period make up a target one, where that is fixed
# (it is not for daily data: holidays vary).
_PARTS = {("monthly", "quarterly"): 3, ("monthly", "annual"): 12, ("quarterly", "annual"): 4}


def default_how(frequency: str) -> str:
    """The house rule (2026-10-11): a week takes its last value, the way markets
    quote weekly data; longer periods take their average, the way statistics
    offices do."""
    return "last" if frequency == "weekly" else "mean"


def _period_end(start: pd.Timestamp, frequency: str) -> pd.Timestamp:
    if frequency in ("weekly", "daily"):
        return start      # a week is labelled by its last day already
    step = {"monthly": pd.DateOffset(months=1), "quarterly": pd.DateOffset(months=3),
            "annual": pd.DateOffset(years=1)}[frequency]
    return start + step - pd.Timedelta(days=1)


def _floor(ts: pd.Series, frequency: str) -> pd.Series:
    """Each date → the label of the period that contains it."""
    if frequency == "daily":
        return ts.dt.normalize()
    if frequency == "weekly":
        return ts + pd.to_timedelta((4 - ts.dt.weekday) % 7, unit="D")
    if frequency == "monthly":
        return ts.dt.to_period("M").dt.start_time
    if frequency == "quarterly":
        return ts.dt.to_period("Q").dt.start_time
    return ts.dt.to_period("Y").dt.start_time


def to_frequency(df: pd.DataFrame, native: str | None, frequency: str,
                 how: str | None = None, today: date | None = None) -> tuple[pd.DataFrame, dict]:
    """One series on `frequency`: ([date, value], notes).

    Faster than the target: aggregated with `how` (default_how otherwise), one
    value per period from the observations inside it, so a holiday Friday uses
    the last trading day of its week. A period with nothing in it stays empty.
    Same speed: unchanged. Slower than the target: unchanged and noted
    `stepped`, for `align` to repeat across the faster calendar; nothing is
    ever interpolated.

    notes: how, stepped, partial_last (the last period has not ended yet),
    partial (when it has not: how many native observations it holds and
    their first and last dates, so "the quarter is six trading days old" can
    be said), incomplete (periods holding fewer native observations than they
    should, e.g. a quarter with a missing month)."""
    if frequency not in FREQUENCIES:
        raise ValueError(f"unknown frequency {frequency!r}; one of {FREQUENCIES}")
    how = how or default_how(frequency)
    if how not in HOWS:
        raise ValueError(f"unknown how {how!r}; one of {HOWS}")
    native = (native or "daily").lower()
    notes = {"how": how, "stepped": False, "partial_last": False, "incomplete": []}
    d = _frame(df)
    if d.empty:
        return _out(d, d["value"]), notes
    rank_native, rank_target = _RANK.get(native, 0), _RANK[frequency]
    if rank_native > rank_target:
        notes.update(stepped=True, how=None)
        return _out(d, d["value"]), notes
    if frequency == "daily" and native == "event":
        # A change-dated rate on a daily calendar: one row per day it was in
        # force, to the day of the last change; align carries it on from there.
        notes["how"] = None
        days = pd.date_range(d["date"].min(), d["date"].max(), freq="D")
        held = d.set_index("date")["value"].reindex(days).ffill()
        notes["carried"] = _carried(d)
        return _out(pd.DataFrame({"date": days}), held.values), notes
    if rank_native == rank_target:
        # One observation per period already; only the label moves, so a
        # Tuesday-dated weekly series (CFTC) lands on the shared Friday.
        notes["how"] = None
        labelled = d.assign(date=_floor(d["date"], frequency))
        labelled = labelled.drop_duplicates(subset="date", keep="last")
        return _out(labelled, labelled["value"]), notes

    s = d.set_index("date")["value"]
    r = s.resample(_RULE[frequency])
    counts = r.count()
    today_ts = pd.Timestamp(today or date.today())
    if native == "event":
        # A change-dated rate holds until it changes, and is still in force
        # today: carry it to the period holding today, not only to its last
        # change (the self-test's ECB line stopped in September).
        last = r.last()
        upto = _floor(pd.Series([today_ts]), frequency).iloc[0]
        values = last.reindex(pd.date_range(last.index[0], max(last.index[-1], upto),
                                            freq=_RULE[frequency])).ffill()
        notes["carried"] = _carried(d)
    elif how == "sum":
        values = r.sum(min_count=1)
    else:
        values = getattr(r, how)()
    values = values.where(counts > 0) if native != "event" else values
    out = pd.DataFrame({"date": values.index, "value": values.values})
    if out.empty:
        return _out(out, out["value"]), notes

    # A rate in force is not a partial period: its last value is the value.
    notes["partial_last"] = native != "event" and \
        bool(_period_end(out["date"].iloc[-1], frequency) >= today_ts)
    n_last = int(counts.iloc[-1])
    if notes["partial_last"] and n_last:
        # The bins are in date order, so the last period's observations are
        # the series' last n_last ones.
        inside = s.sort_index().index[-n_last:]
        notes["partial"] = {"n": n_last, "first": inside[0].date().isoformat(),
                            "last": inside[-1].date().isoformat()}
    elif frequency == "weekly" and native != "event" and n_last:
        # A week that has ended but whose last observation is older than its
        # Thursday rests on partial data: the EIA publishes Tuesday's price
        # on Friday's date, the Fed Board's files run a day behind. Said in a
        # footnote, where the self-test's session had to work it out.
        last_obs = s.sort_index().index[-1]
        if (out["date"].iloc[-1] - last_obs).days > 1:
            inside = s.sort_index().index[-n_last:]
            notes["ended_early"] = {"n": n_last, "first": inside[0].date().isoformat(),
                                    "last": last_obs.date().isoformat()}
    parts = _PARTS.get((native, frequency))
    if parts:
        short = counts[(counts > 0) & (counts < parts)]
        notes["incomplete"] = [ts.date().isoformat() for ts in short.index]
    res = pd.DataFrame({"date": out["date"].dt.date, "value": out["value"]})
    return res.reset_index(drop=True), notes


def _carried(d: pd.DataFrame) -> dict:
    """What a chart footnote says about a change-dated rate: its last change."""
    return {"last_change": d["date"].iloc[-1].date().isoformat(),
            "value": float(d["value"].iloc[-1])}


def align(frames: dict[str, tuple[pd.DataFrame, str, bool]], frequency: str,
          start=None, end=None) -> tuple[list[date], dict[str, list[float | None]]]:
    """Several series, each already on `frequency` (or stepped), on one shared
    calendar: (dates, {id: values or None}).

    frames: {id: (df, native frequency, stepped)}. The calendar runs over the
    periods the non-stepped series cover (all of them when every series is
    stepped), every period present, empty ones None. A stepped series repeats
    the value of its own period across the faster slots inside it, and stops at
    its last period: weeks after the latest monthly print stay None."""
    if frequency not in FREQUENCIES:
        raise ValueError(f"unknown frequency {frequency!r}; one of {FREQUENCIES}")
    prepared = {k: (_frame(df), native, stepped) for k, (df, native, stepped) in frames.items()}
    spans = [d["date"] for d, _, stepped in prepared.values() if not stepped and not d.empty]
    if not spans:
        spans = [_floor(d["date"], frequency) for d, _, _ in prepared.values() if not d.empty]
    if not spans:
        return [], {k: [] for k in frames}
    lo = min(x.min() for x in spans)
    hi = max(x.max() for x in spans)
    if start is not None:
        lo = max(lo, _floor(pd.Series([pd.Timestamp(start)]), frequency).iloc[0])
    if end is not None:
        hi = min(hi, pd.Timestamp(end))
    if lo > hi:
        calendar = pd.DatetimeIndex([])
    elif frequency == "daily":
        # The days the series have, so a weekend is no slot rather than a gap.
        days = sorted({ts for x in spans for ts in x if lo <= ts <= hi})
        calendar = pd.DatetimeIndex(days)
    else:
        calendar = pd.date_range(lo, hi, freq=_RULE[frequency])
    cal = pd.Series(calendar)
    columns: dict[str, list[float | None]] = {}
    for k, (d, native, stepped) in prepared.items():
        if d.empty:
            columns[k] = [None] * len(calendar)
            continue
        if stepped:
            own = native if native in FREQUENCIES else "monthly"
            key = _floor(cal, own)
            lookup = dict(zip(_floor(d["date"], own), d["value"]))
            vals = [lookup.get(x) for x in key]
        elif native == "event":
            # In force until it changes: the last value on or before each slot.
            s = d.set_index("date")["value"].sort_index()
            vals = [s[:x].iloc[-1] if len(s[:x]) else None for x in cal]
        else:
            lookup = dict(zip(d["date"], d["value"]))
            vals = [lookup.get(x) for x in cal]
        columns[k] = [None if v is None or pd.isna(v) else float(v) for v in vals]
    return [ts.date() for ts in calendar], columns
