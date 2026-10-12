"""Transforms work by date, not by row count."""
from datetime import date

import pandas as pd
import pytest

from bedrok.ingest import transforms


def _monthly(values, start="2024-01-01"):
    return pd.DataFrame({"date": pd.date_range(start, periods=len(values), freq="MS").date,
                         "value": values})


def test_yoy_on_a_monthly_index():
    df = _monthly([100.0 + i for i in range(25)])
    out = transforms.yoy(df)
    assert str(out["date"].iloc[0]) == "2025-01-01"
    assert out["value"].iloc[0] == pytest.approx((112 / 100 - 1) * 100)


def test_yoy_is_not_fooled_by_a_missing_month():
    df = _monthly([100.0 + i for i in range(25)]).drop(index=5)   # 2024-06 missing
    out = transforms.yoy(df).set_index("date")["value"]
    assert out[pd.Timestamp("2025-07-01").date()] == pytest.approx((118 / 106 - 1) * 100)
    assert pd.Timestamp("2025-06-01").date() not in out.index   # no stand-in a month away


def test_yoy_on_daily_data_uses_the_last_trading_day_before():
    dates = pd.bdate_range("2024-01-01", "2025-01-10")
    df = pd.DataFrame({"date": dates.date, "value": range(len(dates))})
    out = transforms.yoy(df).set_index("date")
    # 2025-01-04 is a Saturday a year on, so 2025-01-06 compares with 2024-01-05
    assert pd.Timestamp("2025-01-06").date() in out.index


def test_three_month_annualised_matches_the_derived_formula():
    df = _monthly([100.0, 101.0, 102.0, 103.0])
    out = transforms.mom3_ann(df)
    assert out["value"].tolist() == pytest.approx([((103 / 100) ** 4 - 1) * 100])


def test_quarterly_three_month_annualised_is_one_quarter():
    df = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=3, freq="QS").date,
                       "value": [100.0, 102.0, 103.0]})
    out = transforms.mom3_ann(df)
    assert out["value"].tolist() == pytest.approx([((1.02) ** 4 - 1) * 100,
                                                   ((103 / 102) ** 4 - 1) * 100])


def test_index_to_rebases_to_100():
    out = transforms.index_to(_monthly([50.0, 100.0, 150.0]), "2024-02-15")
    assert out["value"].tolist() == [50.0, 100.0, 150.0]
    with pytest.raises(ValueError):
        transforms.index_to(_monthly([1.0]), "2020-01-01")


def test_apply_rejects_an_unknown_transform():
    with pytest.raises(ValueError, match="unknown transform"):
        transforms.apply(_monthly([1.0]), "log")


# ---- frequencies (session 8) -------------------------------------------------


def _daily(start, end, skip=()):
    days = [d for d in pd.bdate_range(start, end) if d.strftime("%Y-%m-%d") not in skip]
    return pd.DataFrame({"date": [d.date() for d in days], "value": [float(i) for i in range(len(days))]})


def test_weekly_takes_the_friday_close_and_a_holiday_falls_back_to_thursday():
    df = _daily("2025-06-30", "2025-07-11", skip={"2025-07-04"})
    out, notes = transforms.to_frequency(df, "daily", "weekly", today=date(2026, 1, 1))
    assert [str(d) for d in out["date"]] == ["2025-07-04", "2025-07-11"]   # dated by Friday
    assert out["value"].tolist() == [3.0, 8.0]          # 3 July's close stands in for the 4th
    assert notes["how"] == "last" and notes["partial_last"] is False


def test_longer_periods_average_by_default_and_honour_sum():
    df = _monthly([1.0, 2.0, 3.0, 10.0, 20.0, 30.0], start="2025-01-01")
    mean, notes = transforms.to_frequency(df, "monthly", "quarterly", today=date(2026, 1, 1))
    assert mean["value"].tolist() == [2.0, 20.0] and notes["how"] == "mean"
    total, _ = transforms.to_frequency(df, "monthly", "quarterly", how="sum", today=date(2026, 1, 1))
    assert total["value"].tolist() == [6.0, 60.0]


def test_an_empty_month_stays_empty_and_a_short_quarter_is_named():
    """The October 2025 CPI month was never collected: it must not be filled."""
    df = _monthly([1.0] * 12, start="2025-01-01").drop(index=9)   # 2025-10 missing
    monthly, _ = transforms.to_frequency(_daily("2025-09-01", "2025-11-28"), "daily", "monthly",
                                         today=date(2026, 1, 1))
    assert len(monthly) == 3
    q, notes = transforms.to_frequency(df, "monthly", "quarterly", today=date(2026, 1, 1))
    assert notes["incomplete"] == ["2025-10-01"]
    dates, cols = transforms.align({"cpi": (df, "monthly", False)}, "monthly")
    assert cols["cpi"][dates.index(date(2025, 10, 1))] is None


def test_an_unfinished_period_is_marked_partial():
    df = _daily("2026-09-01", "2026-09-10")
    _, notes = transforms.to_frequency(df, "daily", "monthly", today=date(2026, 9, 10))
    assert notes["partial_last"] is True


def test_an_unfinished_period_says_how_much_of_it_there_is():
    """The self-test: a Q4 point built on six trading days read as a move."""
    df = _daily("2026-07-01", "2026-10-08")
    _, notes = transforms.to_frequency(df, "daily", "quarterly", today=date(2026, 10, 12))
    held = notes["partial"]
    assert held["first"] == "2026-10-01" and held["last"] == "2026-10-08"
    assert held["n"] == len(_daily("2026-10-01", "2026-10-08"))


def test_a_slower_series_is_stepped_and_stops_after_its_last_period():
    weekly, _ = transforms.to_frequency(_daily("2026-08-03", "2026-10-09"), "daily", "weekly",
                                        today=date(2027, 1, 1))
    fed = _monthly([3.63, 3.75], start="2026-08-01")
    stepped, notes = transforms.to_frequency(fed, "monthly", "weekly")
    assert notes["stepped"] is True
    dates, cols = transforms.align({"oas": (weekly, "daily", False),
                                    "fed": (stepped, "monthly", True)}, "weekly")
    by = dict(zip(dates, cols["fed"]))
    assert by[date(2026, 8, 28)] == 3.63 and by[date(2026, 9, 25)] == 3.75
    assert by[date(2026, 10, 2)] is None                 # October not published: no invention
    assert None not in cols["oas"]


def test_a_change_dated_rate_is_carried_forward():
    ecb = pd.DataFrame({"date": [date(2024, 6, 12), date(2024, 9, 18)], "value": [3.75, 3.5]})
    out, _ = transforms.to_frequency(ecb, "event", "monthly", today=date(2025, 1, 1))
    assert out["value"].tolist() == [3.75, 3.75, 3.75, 3.5]   # Jun, Jul, Aug held; Sep changed


def test_resampling_comes_before_yoy():
    df = _monthly([100.0 + i for i in range(24)], start="2024-01-01")
    q, _ = transforms.to_frequency(df, "monthly", "quarterly", today=date(2026, 1, 1))
    out = transforms.yoy(q)
    assert out["value"].iloc[0] == pytest.approx((113 / 101 - 1) * 100)   # Q1 avg on Q1 avg


def test_unknown_frequency_or_how_is_refused():
    with pytest.raises(ValueError):
        transforms.to_frequency(_monthly([1.0]), "monthly", "fortnightly")
    with pytest.raises(ValueError):
        transforms.to_frequency(_monthly([1.0]), "daily", "monthly", how="median")


def test_a_weekly_series_dated_on_tuesday_lands_on_the_friday():
    cot = pd.DataFrame({"date": [date(2026, 9, 29), date(2026, 10, 6)], "value": [1.0, 2.0]})
    out, notes = transforms.to_frequency(cot, "weekly", "weekly")
    assert [str(d) for d in out["date"]] == ["2026-10-02", "2026-10-09"]
    assert notes["stepped"] is False
