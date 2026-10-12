"""EIA adapter: the pure parser honours the [date, value] contract."""
import datetime as dt

from bedrok.ingest.adapters import eia


def test_parse_sorts_dedups_drops_nan():
    data = [
        {"period": "2024-01-03", "value": 72.5},
        {"period": "2024-01-02", "value": 70.1},
        {"period": "2024-01-02", "value": 70.1},   # duplicate date
        {"period": "2024-01-05", "value": None},    # missing → dropped
        {"period": "2024-01-04", "value": "71.0"},  # string number → coerced
    ]
    df = eia._parse(data)
    assert list(df.columns) == ["date", "value"]
    # ascending, deduped, NaN row gone
    assert [str(d) for d in df["date"]] == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert isinstance(df["date"].iloc[0], dt.date)
    assert df["value"].tolist() == [70.1, 72.5, 71.0]


def test_parse_empty_returns_columns():
    df = eia._parse([])
    assert list(df.columns) == ["date", "value"]
    assert df.empty
