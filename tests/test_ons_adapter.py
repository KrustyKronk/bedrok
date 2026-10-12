"""ONS timeseries JSON parsing (pure — no network)."""
import pandas as pd

from bedrok.ingest.adapters import ons


def _obs(date, value, year, month="", quarter=""):
    return {"date": date, "value": value, "label": date, "year": year,
            "month": month, "quarter": quarter, "sourceDataset": "MM23"}


# Shape captured from a live response for CDID D7G7 (dataset MM23).
SAMPLE = {
    "description": {"cdid": "D7G7", "title": "CPI ANNUAL RATE", "unit": "%"},
    "years": [_obs("1989", "5.2", "1989"), _obs("2025", "3.4", "2025")],
    "quarters": [_obs("1989 Q1", "5.0", "1989", quarter="Q1"),
                 _obs("2026 Q1", "3.1", "2026", quarter="Q1")],
    "months": [_obs("1989 JAN", "4.9", "1989", month="January"),
               _obs("2026 MAY", "2.8", "2026", month="May")],
}


def test_prefers_the_finest_grain_available():
    """A monthly series also publishes quarterly and annual roll-ups of itself;
    storing a roll-up would silently coarsen the series."""
    df = ons._parse(SAMPLE)
    assert list(df.columns) == ["date", "value"]
    assert len(df) == 2
    assert df["date"].tolist() == [pd.Timestamp("1989-01-01").date(),
                                   pd.Timestamp("2026-05-01").date()]
    assert df["value"].tolist() == [4.9, 2.8]     # strings coerced to floats


def test_falls_back_through_quarters_then_years():
    quarterly = {k: v for k, v in SAMPLE.items() if k != "months"}
    df = ons._parse(quarterly)
    assert df["date"].tolist() == [pd.Timestamp("1989-01-01").date(),
                                   pd.Timestamp("2026-01-01").date()]  # quarter START

    annual = {"years": SAMPLE["years"]}
    df = ons._parse(annual)
    assert df["date"].tolist() == [pd.Timestamp("1989-01-01").date(),
                                   pd.Timestamp("2025-01-01").date()]


def test_empty_and_malformed_observations_are_dropped_not_fatal():
    js = {"months": [_obs("2026 MAY", "2.8", "2026", month="May"),
                     _obs("2026 JUN", "", "2026", month="June"),        # no value
                     _obs("bad", "1.0", "", month="Nonsense"),          # no year
                     _obs("2026 JUL", "1.0", "2026", month="Smarch")]}  # bad month
    df = ons._parse(js)
    assert len(df) == 1
    assert df["value"].iloc[0] == 2.8


def test_no_observations_returns_the_empty_contract():
    df = ons._parse({"description": {"cdid": "X"}})
    assert df.empty
    assert list(df.columns) == ["date", "value"]


def test_quarter_labels_map_to_quarter_start_months():
    js = {"quarters": [_obs(f"2026 Q{q}", "1.0", "2026", quarter=f"Q{q}")
                       for q in (1, 2, 3, 4)]}
    df = ons._parse(js)
    assert [d.month for d in df["date"]] == [1, 4, 7, 10]
