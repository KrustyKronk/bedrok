"""The generic SDMX client, on response shapes seen live on 2026-10-10. No network."""
import io

import pandas as pd
import pytest

from bedrok.ingest.adapters import sdmx
from bedrok.ingest.adapters._util import parse_period

D = lambda s: pd.Timestamp(s).date()  # noqa: E731


# ---- SDMX-CSV 1.0 (OECD, BIS, Istat, ABS) — the cases the OECD adapter pinned

OECD = ("DATAFLOW,REF_AREA,FREQ,MEASURE,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n")


def _oecd(period, value, area="USA"):
    return f"OECD.SDD.STES:DSD_STES@DF_CLI(4.1),{area},M,LI,{period},{value},A\n"


def test_period_and_value_come_out_of_the_wide_csv():
    df = sdmx.parse_csv(OECD + _oecd("2026-01", "100.5") + _oecd("2026-02", "100.8"))
    assert list(df.columns) == ["date", "value"]
    assert df.values.tolist() == [[D("2026-01-01"), 100.5], [D("2026-02-01"), 100.8]]


def test_newest_first_rows_are_sorted():
    df = sdmx.parse_csv(OECD + _oecd("2026-06", "3") + _oecd("2026-01", "1") + _oecd("2026-03", "2"))
    assert df["value"].tolist() == [1.0, 2.0, 3.0]


def test_missing_and_unparseable_observations_are_dropped():
    df = sdmx.parse_csv(OECD + _oecd("2026-01", "100.5") + _oecd("2026-02", "")
                        + _oecd("nonsense", "5.0"))
    assert len(df) == 1


def test_unexpected_shape_returns_the_empty_contract():
    df = sdmx.parse_csv("SOMETHING,ELSE\n1,2\n")
    assert df.empty and list(df.columns) == ["date", "value"]


def test_an_unpinned_key_is_an_error_not_interleaved_data():
    """Three CLI definitions once came back stacked in one OECD answer."""
    with pytest.raises(sdmx.UnpinnedKey, match="2 series"):
        sdmx.parse_csv(OECD + _oecd("2026-01", "100.5") + _oecd("2026-01", "99.0", area="DEU"))


def test_istat_editions_count_as_separate_series():
    csv = ("DATAFLOW,FREQ,REF_AREA,DATA_TYPE,EDITION,TIME_PERIOD,OBS_VALUE\n"
           "IT1:151_874(1.0),M,IT,UNEM_R,2026M09G1,2026-01,5.1\n"
           "IT1:151_874(1.0),M,IT,UNEM_R,2026M10G1,2026-01,5.09\n")
    with pytest.raises(sdmx.UnpinnedKey):
        sdmx.parse_csv(csv)


# ---- other dialects -----------------------------------------------------------

def test_ecb_csvdata_uses_its_key_column():
    csv = ("KEY,FREQ,CURRENCY,TIME_PERIOD,OBS_VALUE,OBS_STATUS,TITLE\n"
           "EXR.D.USD.EUR.SP00.A,D,USD,2026-10-01,1.1298,A,US dollar/Euro\n"
           "EXR.D.USD.EUR.SP00.A,D,USD,2026-10-02,1.13,A,US dollar/Euro\n")
    assert sdmx.parse_csv(csv)["value"].tolist() == [1.1298, 1.13]


def test_semicolons_and_a_byte_order_mark_are_read():
    csv = ("﻿DATAFLOW;FREQ;BBK_STD_SERIES;TIME_PERIOD;OBS_VALUE;BBK_UNIT\n"
           "BBK:BBSIS(1.0);D;R10XX;2026-10-01;3.62;PROZENT\n")
    assert sdmx.parse_csv(csv).values.tolist() == [[D("2026-10-01"), 3.62]]


def test_bundesbank_weekends_and_per_row_columns_after_the_period():
    """The live BBSIS answer (2026-10-16): a weekend is '.' with status K, and
    BBK_DIFF_A changes every row. Only columns before TIME_PERIOD name the
    series, so the varying tail is not an unpinned key."""
    head = "﻿DATAFLOW;BBK_STD_FREQ;BBK_SEIS_MATURITY;TIME_PERIOD;OBS_VALUE;BBK_DIFF_A;OBS_STATUS\n"
    csv = head + ("BBK:BBSIS(1.0);D;R10XX;2026-10-02;3.46;-0.16;\n"
                  "BBK:BBSIS(1.0);D;R10XX;2026-10-03;.;;K\n"
                  "BBK:BBSIS(1.0);D;R10XX;2026-10-05;3.47;0.01;\n")
    assert sdmx.parse_csv(csv).values.tolist() == [[D("2026-10-02"), 3.46],
                                                   [D("2026-10-05"), 3.47]]


def test_sdmx_csv_2_with_imf_month_periods():
    csv = ("STRUCTURE,STRUCTURE_ID,ACTION,COUNTRY,INDICATOR,FREQUENCY,TIME_PERIOD,OBS_VALUE\n"
           "dataflow,IMF.STA:CPI(5.0.0),I,USA,CPI,M,2026-M06,149.13\n"
           "dataflow,IMF.STA:CPI(5.0.0),I,USA,CPI,M,2026-M05,148.9\n")
    df = sdmx.parse_csv(csv)
    assert df.values.tolist() == [[D("2026-05-01"), 148.9], [D("2026-06-01"), 149.13]]


def test_insee_xml():
    xml = b"""<?xml version="1.0"?>
<message:StructureSpecificData xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
  xmlns:ns1="urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=FR1:SERIES_BDM(1.0):ObsLevelDim:TIME_PERIOD">
 <message:DataSet><Series IDBANK="011814630">
  <Obs TIME_PERIOD="2026-09" OBS_VALUE="102.99" OBS_STATUS="P"/>
  <Obs TIME_PERIOD="2026-08" OBS_VALUE="102.8" OBS_STATUS="A"/>
 </Series></message:DataSet></message:StructureSpecificData>"""
    df = sdmx.parse_xml(io.BytesIO(xml))
    assert df.values.tolist() == [[D("2026-08-01"), 102.8], [D("2026-09-01"), 102.99]]


def test_xml_with_two_series_is_an_unpinned_key():
    xml = b"""<D><Series><Obs TIME_PERIOD="2026" OBS_VALUE="1"/></Series>
                 <Series><Obs TIME_PERIOD="2026" OBS_VALUE="2"/></Series></D>"""
    with pytest.raises(sdmx.UnpinnedKey):
        sdmx.parse_xml(io.BytesIO(xml))


def test_every_period_form_lands_on_period_start():
    got = parse_period(pd.Series(["2026-10-08", "2026-10", "2026-M06", "2026-Q3",
                                  "2026Q1", "2026-S2", "2026", "junk"]))
    assert [str(x)[:10] for x in got] == ["2026-10-08", "2026-10-01", "2026-06-01",
                                         "2026-07-01", "2026-01-01", "2026-07-01",
                                         "2026-01-01", "NaT"]


# ---- the request ------------------------------------------------------------

class _Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text, self.content = status, text, text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


ROW = {"base_url": "https://example.org/rest/", "format": "accept", "user_agent": "default"}


def test_request_shape_by_format(monkeypatch):
    seen = {}

    def fake_get(url, params, headers, timeout):
        seen.update(url=url, params=params, headers=headers)
        return _Resp(200, OECD + _oecd("2026-01", "1") + _oecd("2026-03", "3"))
    monkeypatch.setattr(sdmx.requests, "get", fake_get)

    df = sdmx.fetch(ROW, "FLOW/M.US", start="2026-02-15")
    assert seen["url"] == "https://example.org/rest/data/FLOW/M.US"
    assert seen["params"] == {"startPeriod": "2026-02"}
    assert "sdmx.data+csv" in seen["headers"]["Accept"]
    assert df["value"].tolist() == [3.0]          # trimmed client-side to the real start

    sdmx.fetch({**ROW, "format": "csvdata", "user_agent": "browser"}, "EXR/D.USD")
    assert seen["params"] == {"format": "csvdata"} and "Accept" not in seen["headers"]
    assert "Mozilla" in seen["headers"]["User-Agent"]


def test_a_day_start_period_sends_the_whole_date(monkeypatch):
    """The Bundesbank answers startPeriod=2026-10 with 400, which an incremental
    fetch reads as "nothing new": the series would freeze with no error."""
    seen = {}

    def fake_get(url, params, headers, timeout):
        seen.update(params=params)
        return _Resp(200, "")
    monkeypatch.setattr(sdmx.requests, "get", fake_get)
    sdmx.fetch({**ROW, "start_period": "day"}, "BBSIS/D.X", start="2026-10-09")
    assert seen["params"] == {"startPeriod": "2026-10-09"}
    sdmx.fetch(ROW, "BBSIS/D.X", start="2026-10-09")
    assert seen["params"] == {"startPeriod": "2026-10"}


def test_no_data_since_the_start_is_empty_but_a_bad_code_fails(monkeypatch):
    monkeypatch.setattr(sdmx.requests, "get", lambda *a, **k: _Resp(404, "NoRecordsFound"))
    assert sdmx.fetch(ROW, "FLOW/KEY", start="2026-10-01").empty
    with pytest.raises(RuntimeError, match="404"):
        sdmx.fetch(ROW, "FLOW/KEY")


def test_latest_edition_is_kept_by_its_numbers_not_its_text():
    csv = ("DATAFLOW,FREQ,REF_AREA,EDITION,TIME_PERIOD,OBS_VALUE\n"
           "IT1:151_874(1.0),M,IT,2026M7G30,2026-06,5.67\n"
           "IT1:151_874(1.0),M,IT,2026M10G1,2026-06,6.02\n"
           "IT1:151_874(1.0),M,IT,2026M10G1,2026-08,6.15\n"
           "IT1:151_874(1.0),M,IT,2026M9G1,2026-06,5.84\n")
    df = sdmx.parse_csv(csv, latest_of="EDITION")
    assert df["value"].tolist() == [6.02, 6.15]
