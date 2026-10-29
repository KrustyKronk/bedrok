"""The National Bank of Kazakhstan adapter (session K1). No network: every
payload is the shape of a live response captured on 2026-10-28, cut short."""
import json
from datetime import date

import pytest

from bedrok.ingest.adapters import _util, nbk

# /records, the reserves page: early rows carry an arbitrary day inside the
# month and published_at can precede reporting_date; recent rows are stamped at
# the month's end; the National Fund's column is null before 2001.
RESERVES = [
    {"id": 2, "gross_international_reserve_volume_million_dollar": 711,
     "national_fund_republic_of_kazakhstan_volume_million_dollar": None,
     "published_at": "1993-12-04 00:00:00", "reporting_date": "1993-12-13 00:00:00"},
    {"id": 3, "gross_international_reserve_volume_million_dollar": 701,
     "national_fund_republic_of_kazakhstan_volume_million_dollar": None,
     "published_at": "1994-01-11 00:00:00", "reporting_date": "1994-01-09 00:00:00"},
    {"id": 404, "gross_international_reserve_volume_million_dollar": 70214,
     "national_fund_republic_of_kazakhstan_volume_million_dollar": 67174,
     "published_at": "2026-09-30 08:00:00", "reporting_date": "2026-08-31 00:00:00"},
    {"id": 407, "gross_international_reserve_volume_million_dollar": 66410,
     "national_fund_republic_of_kazakhstan_volume_million_dollar": 66653,
     "published_at": "2026-10-09 09:55:00", "reporting_date": "2026-09-30 08:00:00"},
]
# The money page stamps its month's end at 22:00, which must stay in that month.
MONEY = [{"m_3": 8232, "reporting_date": "1994-01-08 00:00:00"},
         {"m_3": 56246156, "reporting_date": "2026-09-30 22:00:00"}]

# The open-data API, form 299: REER comes twice a month, with and without oil trade.
FORM_299 = {"locale": "en", "formId": 299, "page": 0, "pageSize": 10000, "totalRows": 3,
            "columns": [{"key": "report_date"}, {"key": "amount"}],
            "rows": [
                {"report_date": "2016-12-01", "amount": 100.0, "type": "Index",
                 "category": "Real effective exchange rate", "subcategory": "Including oil trade"},
                {"report_date": "2016-12-01", "amount": 100.0, "type": "Index",
                 "category": "Real effective exchange rate", "subcategory": "Excluding oil trade"},
                {"report_date": "2026-08-01", "amount": 132.39323, "type": "Index",
                 "category": "Real effective exchange rate", "subcategory": "Including oil trade"},
            ]}

BASE_RATE = [
    {"effectiveDate": "2026-10-27", "rate": 15.75, "corridorLower": 14.75, "corridorUpper": 16.75},
    {"effectiveDate": "2015-09-02", "rate": 12.0, "corridorLower": 7.0, "corridorUpper": 17.0},
]

# The rates report: a header row in Russian, then date, quantity, rate.
FX_HTML = """<table><tr><th></th><th>Числовое значение</th><th>EURO</th></tr>
<tr><td>2026-10-28</td><td>1</td><td>507.89</td></tr>
<tr><td>2026-10-29</td><td>1</td><td>508.42</td></tr></table>"""
FX_HTML_QTY = """<table><tr><td>2026-10-29</td><td>100</td><td>5 610,00</td></tr></table>"""

PAGE_HTML = """<div class="stat-date"><span>Published on: 09.10.2026</span>
<span>Next: 30.10.2026</span></div>"""

RES = "records:international-reserve-and-asset/mezhdunarodnye-rezervy-i-aktivy-nacionalnogo-fonda-rk#"


def test_records_are_dated_by_the_month_of_their_reporting_date():
    df = nbk.parse_records(RESERVES, "gross_international_reserve_volume_million_dollar")
    assert df["date"].tolist() == [date(1993, 12, 1), date(1994, 1, 1),
                                   date(2026, 8, 1), date(2026, 9, 1)]
    assert df["value"].tolist() == [711, 701, 70214, 66410]
    late = nbk.parse_records(MONEY, "m_3")
    assert late["date"].iloc[-1] == date(2026, 9, 1)


def test_records_drop_nulls_and_refuse_an_unknown_column_or_a_doubled_month():
    nf = nbk.parse_records(RESERVES, "national_fund_republic_of_kazakhstan_volume_million_dollar")
    assert nf["date"].tolist() == [date(2026, 8, 1), date(2026, 9, 1)]
    with pytest.raises(ValueError, match="no column"):
        nbk.parse_records(RESERVES, "m_3")
    doubled = RESERVES + [{**RESERVES[-1], "reporting_date": "2026-09-15 00:00:00"}]
    with pytest.raises(ValueError, match="two rows for 2026-09"):
        nbk.parse_records(doubled, "gross_international_reserve_volume_million_dollar")


def test_the_api_needs_every_varying_column_pinned_and_an_unpaged_answer():
    df = nbk.parse_api(FORM_299, {"category": "Real effective exchange rate",
                                  "subcategory": "Including oil trade"})
    assert df["value"].tolist() == [100.0, 132.39323]
    with pytest.raises(_util.UnpinnedKey, match="subcategory"):
        nbk.parse_api(FORM_299, {"category": "Real effective exchange rate"})
    with pytest.raises(ValueError, match="paged"):
        nbk.parse_api({**FORM_299, "totalRows": 3042}, {"category": "x"})


def test_the_base_rate_is_one_row_per_decision_in_date_order():
    df = nbk.parse_base_rate(BASE_RATE)
    assert df["date"].tolist() == [date(2015, 9, 2), date(2026, 10, 27)]
    assert df["value"].tolist() == [12.0, 15.75]


def test_fx_rates_are_per_one_unit_of_the_currency():
    df = nbk.parse_fx(FX_HTML)
    assert df["date"].tolist() == [date(2026, 10, 28), date(2026, 10, 29)]
    assert df["value"].iloc[-1] == 508.42
    assert nbk.parse_fx(FX_HTML_QTY)["value"].iloc[0] == pytest.approx(56.10)
    with pytest.raises(ValueError, match="no rates"):
        nbk.parse_fx("<html>maintenance</html>")


def test_the_next_date_is_read_from_the_statistics_page():
    assert nbk.parse_next(PAGE_HTML) == date(2026, 10, 30)
    assert nbk.parse_next("<p>no schedule</p>") is None


class _Resp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def test_requests_one_page_per_run_and_filter_by_start(monkeypatch):
    seen = []

    def fake_get(url, headers=None, timeout=None):
        seen.append(url)
        if url.endswith("/records"):
            return _Resp(RESERVES)
        if "report" in url:
            return _Resp(FX_HTML)
        return _Resp(PAGE_HTML)
    monkeypatch.setattr(nbk.requests, "get", fake_get)
    nbk._download.cache_clear()
    gross = nbk.fetch(RES + "gross_international_reserve_volume_million_dollar", start="2026-01-01")
    nbk.fetch(RES + "national_fund_republic_of_kazakhstan_volume_million_dollar")
    assert gross["value"].tolist() == [70214, 66410]
    assert sum(u.endswith("/records") for u in seen) == 1, "the reserves page is fetched once"
    assert seen[0] == ("https://nationalbank.kz/en/international-reserve-and-asset/"
                       "mezhdunarodnye-rezervy-i-aktivy-nacionalnogo-fonda-rk/records")
    nbk.fetch("fx:6", start="2026-10-20")
    assert "beginDate=20.10.2026" in seen[-1] and seen[-1].endswith("rates[]=6")
    assert nbk.next_release(RES + "gross_international_reserve_volume_million_dollar") == date(2026, 10, 30)
    assert nbk.next_release("base-rate") is None
    with pytest.raises(ValueError, match="unknown source_code"):
        nbk.fetch("xml:5")
    nbk._download.cache_clear()
