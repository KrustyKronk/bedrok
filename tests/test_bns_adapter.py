"""The Bureau of National Statistics of Kazakhstan adapter, Taldau (session K2).
No network: every payload is the shape of a live response captured on
2026-10-29, cut short."""
from datetime import date

import pytest

from bedrok.ingest.adapters import bns

# GetIndexTreeData, monthly CPI against the same month a year earlier: values
# are relatives (109.3 is a 9.3% rise) in keys yMMYYYY; "x" is suppressed.
CPI_YOY = [{"id": "741880", "rownum": 1, "text": "REPUBLIC OF KAZAKHSTAN", "leaf": "false",
            "measureName": "", "y012011": "108.1", "y082026": "109.8", "y092026": "109.3",
            "y102026": "x", "y112026": ""}]

# Quarterly GDP to date (period 9): the key names the quarter's last month.
GDP_YTD = [{"id": "741880", "text": "REPUBLIC OF KAZAKHSTAN", "leaf": "false",
            "y032025": "30467333700000", "y062025": "61371821900000",
            "y092025": "99724898500000", "y122025": "159608552900000",
            "y032026": "33102358400000", "y122024": "136693318300000"}]

CPI = "703076?period=4&measure=1&dics=67,848,2753&terms=741880,2695732,4772381"


def test_monthly_keys_date_the_month_and_change_subtracts_one_hundred():
    df = bns.parse(CPI_YOY, "4", as_="change")
    assert df["date"].tolist() == [date(2011, 1, 1), date(2026, 8, 1), date(2026, 9, 1)]
    assert df["value"].tolist() == [8.1, 9.8, 9.3]


def test_quarterly_keys_date_the_quarter_start():
    df = bns.parse(GDP_YTD, "9")
    assert df["date"].tolist()[:2] == [date(2024, 10, 1), date(2025, 1, 1)]
    assert df["date"].iloc[-1] == date(2026, 1, 1)


def test_year_to_date_levels_become_discrete_quarters():
    df = bns.parse(GDP_YTD, "9", ytd="decumulate", scale=1e-9).set_index("date")["value"]
    assert df[date(2025, 1, 1)] == pytest.approx(30467.3337)
    assert df[date(2025, 4, 1)] == pytest.approx(61371.8219 - 30467.3337)
    assert df[date(2025, 10, 1)] == pytest.approx(159608.5529 - 99724.8985)
    assert sum(df[d] for d in df.index if d.year == 2025) == pytest.approx(159608.5529)
    # 2024-Q4 has no 2024-Q3 before it in the answer: dropped, never guessed
    assert date(2024, 10, 1) not in df.index
    assert df[date(2026, 1, 1)] == pytest.approx(33102.3584)


def test_a_missing_country_row_or_an_empty_answer_is_an_error(monkeypatch):
    with pytest.raises(ValueError, match="Republic of Kazakhstan"):
        bns.parse([{"id": "268020", "y092026": "100.8"}], "4")

    class Empty:
        text = "  "

        def raise_for_status(self):
            pass
    monkeypatch.setattr(bns.requests, "get", lambda *a, **k: Empty())
    bns._download.cache_clear()
    with pytest.raises(ValueError, match="measure id"):
        bns.fetch(CPI)
    with pytest.raises(ValueError, match="needs measure="):
        bns.fetch("703076?period=4&terms=741880&dics=67")
    bns._download.cache_clear()


def test_the_request_pins_the_country_and_start_filters(monkeypatch):
    seen = {}

    class R:
        text = "[...]"

        def raise_for_status(self):
            pass

        def json(self):
            return CPI_YOY

    def fake_get(url, timeout=None, params=None):
        seen.update(url=url, params=params)
        return R()
    monkeypatch.setattr(bns.requests, "get", fake_get)
    bns._download.cache_clear()
    df = bns.fetch(CPI + "&as=change", start="2026-01-01")
    assert df["value"].tolist() == [9.8, 9.3]
    assert seen["url"] == "https://taldau.stat.gov.kz/en/Api/GetIndexTreeData"
    assert seen["params"]["p_term_id"] == "741880" and seen["params"]["p_index_id"] == 703076
    assert seen["params"]["p_terms"] == "741880,2695732,4772381"
    assert seen["params"]["p_measure_id"] == "1" and seen["params"]["p_period_id"] == "4"
    bns._download.cache_clear()
