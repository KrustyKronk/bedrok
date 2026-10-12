"""The session-3 US agency adapters honour the [date, value] contract, on
payloads shaped like the live responses of 2026-10-09. No network."""
import datetime as dt

import pandas as pd
import pytest

from bedrok.ingest.adapters import bea, bls, census, nyfed

D = dt.date


# ---- BLS ------------------------------------------------------------------
def test_bls_parse_keeps_months_and_quarters_and_drops_gaps():
    df = bls._parse([
        {"year": "2026", "period": "M08", "value": "334.131"},
        {"year": "2026", "period": "M13", "value": "330.0"},   # annual average
        {"year": "2025", "period": "M10", "value": "-"},       # never collected
        {"year": "2025", "period": "M09", "value": "324.8"},
        {"year": "2026", "period": "Q02", "value": "177.178"},
    ])
    assert list(df.columns) == ["date", "value"]
    assert df["date"].tolist() == [D(2025, 9, 1), D(2026, 4, 1), D(2026, 8, 1)]


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _bls_payload(codes, y0, y1):
    return {"status": "REQUEST_SUCCEEDED", "Results": {"series": [
        {"seriesID": c, "data": [{"year": str(y), "period": "M01", "value": "1"}
                                  for y in range(y0, y1 + 1)]} for c in codes]}}


def test_bls_batches_every_primed_series_into_one_request_per_window(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        return _Resp(_bls_payload(json["seriesid"], int(json["startyear"]),
                                  int(json["endyear"])))
    monkeypatch.setattr(bls.requests, "post", fake_post)
    bls.prime(["CUSR0000SA0", "LNS14000000", "CES0000000001"])
    start = f"{dt.date.today().year - 15}-01-01"
    a = bls.fetch("CUSR0000SA0", "", start=start)
    b = bls.fetch("LNS14000000", "", start=start)
    assert len(calls) == 2                          # two keyless 10-year windows, shared
    assert all(len(c["seriesid"]) == 3 for c in calls)
    assert "registrationkey" not in calls[0]
    assert len(a) == len(b) == 16


def test_bls_with_a_key_uses_twenty_year_windows(monkeypatch):
    calls = []
    monkeypatch.setattr(bls.requests, "post", lambda url, json, timeout: (
        calls.append(json) or _Resp(_bls_payload(json["seriesid"],
                                                 int(json["startyear"]), int(json["endyear"])))))
    bls.prime(["CUSR0000SA0"])
    bls.fetch("CUSR0000SA0", "k", start=f"{dt.date.today().year - 15}-01-01")
    assert len(calls) == 1 and calls[0]["registrationkey"] == "k"


def test_bls_threshold_is_an_error_not_an_empty_series(monkeypatch):
    monkeypatch.setattr(bls.requests, "post", lambda *a, **k: _Resp(
        {"status": "REQUEST_NOT_PROCESSED", "message": ["daily threshold"]}))
    bls.prime([])
    with pytest.raises(RuntimeError, match="REQUEST_NOT_PROCESSED"):
        bls.fetch("CUSR0000SA0", "", start="2026-01-01")


# ---- BEA ------------------------------------------------------------------
def test_bea_parse_picks_the_line_and_scales_to_billions():
    rows = [
        {"LineNumber": "1", "TimePeriod": "2026Q2", "UNIT_MULT": "6", "DataValue": "24,012,345"},
        {"LineNumber": "1", "TimePeriod": "2026Q1", "UNIT_MULT": "6", "DataValue": "23,900,000"},
        {"LineNumber": "2", "TimePeriod": "2026Q2", "UNIT_MULT": "6", "DataValue": "1"},
    ]
    df = bea._parse(rows, "1", "billions")
    assert df["date"].tolist() == [D(2026, 1, 1), D(2026, 4, 1)]
    assert df["value"].tolist() == pytest.approx([23900.0, 24012.345])


def test_bea_parse_monthly_index_is_unscaled():
    df = bea._parse([{"LineNumber": "25", "TimePeriod": "2026M08", "UNIT_MULT": "0",
                      "DataValue": "127.5"}], "25")
    assert df.values.tolist() == [[D(2026, 8, 1), 127.5]]


# ---- Census ---------------------------------------------------------------
def test_census_parse_filters_to_the_seasonally_adjusted_cell_and_drops_echoed_columns():
    payload = [
        ["cell_value", "data_type_code", "category_code", "seasonally_adj", "time_slot_id",
         "time", "category_code"],
        ["725,000", "SM", "44X72", "yes", "0", "2026-08", "44X72"],
        ["700000", "SM", "44X72", "no", "0", "2026-08", "44X72"],
        ["710000", "SM", "44X72", "yes", "0", "2026-07", "44X72"],
        ["5", "SM", "441", "yes", "0", "2026-08", "441"],
    ]
    df = census._parse(payload, "44X72", "SM")
    assert df.values.tolist() == [[D(2026, 7, 1), 710000.0], [D(2026, 8, 1), 725000.0]]


def test_census_parse_empty():
    assert census._parse([], "44X72", "SM").empty


# ---- NY Fed ---------------------------------------------------------------
def test_nyfed_rrp_sums_treasury_tranches_per_day_in_billions():
    payload = {"repo": {"operations": [
        {"operationType": "Reverse Repo", "term": "Overnight", "operationDate": "2026-10-08",
         "details": [{"securityType": "Treasury", "amtAccepted": 335000000}]},
        {"operationType": "Reverse Repo", "term": "Overnight", "operationDate": "2026-10-08",
         "details": [{"securityType": "Treasury", "amtAccepted": 1000000000},
                     {"securityType": "Mortgage-Backed", "amtAccepted": 9e9}]},
        {"operationType": "Reverse Repo", "term": "Term", "operationDate": "2026-10-08",
         "details": [{"securityType": "Treasury", "amtAccepted": 5e9}]},
    ]}}
    df = nyfed._parse_rrp(payload)
    assert df.values.tolist() == [[D(2026, 10, 8), pytest.approx(1.335)]]


def test_nyfed_sofr():
    df = nyfed._parse_sofr({"refRates": [
        {"effectiveDate": "2026-10-08", "type": "SOFR", "percentRate": 3.87},
        {"effectiveDate": "2026-10-07", "type": "SOFR", "percentRate": 3.88}]})
    assert df["value"].tolist() == [3.88, 3.87]


def test_nyfed_empire_dates_move_to_month_start():
    text = ("surveyDate,GACDISA,NOCDISA\n2026-08-31,20.6,17.3\n"
            "2026-09-30,7.6,2.0\n2001-07-31,ND,1\n")
    df = nyfed._parse_esms(text, "GACDISA")
    assert df.values.tolist() == [[D(2026, 8, 1), 20.6], [D(2026, 9, 1), 7.6]]


def test_nyfed_effr_carries_the_target_range_and_no_upper_bound_before_2008():
    """Session 10: the Fed's decisions come from the daily target, not a hand-kept list."""
    from bedrok.ingest.adapters import nyfed
    payload = {"refRates": [
        {"effectiveDate": "2008-12-15", "type": "EFFR", "percentRate": 0.18, "targetRateFrom": 1.0},
        {"effectiveDate": "2008-12-16", "type": "EFFR", "percentRate": 0.17,
         "targetRateFrom": 0.0, "targetRateTo": 0.25},
        {"effectiveDate": "2026-09-17", "type": "EFFR", "percentRate": 3.88,
         "targetRateFrom": 3.75, "targetRateTo": 4.0},
        {"effectiveDate": "2026-09-17", "type": "OBFR", "percentRate": 3.9}]}
    lower = nyfed._parse_effr(payload, "effr_target_lower")
    upper = nyfed._parse_effr(payload, "effr_target_upper")
    effr = nyfed._parse_effr(payload, "effr")
    assert lower["value"].tolist() == [1.0, 0.0, 3.75]      # the single target, then the floor
    assert [str(d) for d in upper["date"]] == ["2008-12-16", "2026-09-17"]
    assert effr["value"].tolist() == [0.18, 0.17, 3.88]     # other rate types ignored
