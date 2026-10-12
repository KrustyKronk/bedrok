"""The second-wave publishers (session 7a): the PxWeb client and the Bank of
Canada, Statistics Canada, INE, Destatis and e-Stat adapters. Pure parsing, no network; every
payload is the shape captured from a live response on 2026-10-10, cut short."""
import io
import zipfile
from datetime import date

import pytest

from bedrok.ingest.adapters import _util, boc, destatis, estat, ine, pxweb, statcan


def _jsonstat2(dims: dict[str, list[str]], values, time="Tid"):
    """A JSON-stat 2.0 dataset as PxWebApi 2 and StatFin answer it."""
    return {
        "version": "2.0", "class": "dataset", "id": list(dims),
        "size": [len(v) for v in dims.values()], "role": {"time": [time]},
        "dimension": {d: {"category": {"index": {c: i for i, c in enumerate(v)}}}
                      for d, v in dims.items()},
        "value": values,
    }


def test_jsonstat2_reads_one_series_and_drops_nulls():
    js = _jsonstat2({"ContentsCode": ["000007ZN"], "Tid": ["1987M01", "1987M02", "2026M08"]},
                    [44.71, None, 123.43])
    df = _util.parse_jsonstat(js)
    assert df["date"].tolist() == [date(1987, 1, 1), date(2026, 8, 1)]
    assert df["value"].tolist() == [44.71, 123.43]


def test_jsonstat1_inside_a_dataset_wrapper_as_statbank_answers():
    js = {"dataset": {"dimension": {
        "id": ["VAREGR", "ENHED", "ContentsCode", "Tid"], "size": [1, 1, 1, 2],
        "role": {"metric": ["ContentsCode"], "time": ["Tid"]},
        "Tid": {"category": {"index": {"2000M12": 0, "2026M08": 1}}}},
        "value": [63.45, 102.58]}}
    df = _util.parse_jsonstat(js)
    assert df["value"].tolist() == [63.45, 102.58]
    assert df["date"].iloc[-1] == date(2026, 8, 1)


def test_a_selection_that_crosses_two_series_is_refused():
    js = _jsonstat2({"contentscode": ["ip_0_1972", "ip_0_2025"], "timeperiod_m": ["2026M08"]},
                    [927.11, 101.95], time="timeperiod_m")
    with pytest.raises(_util.UnpinnedKey):
        _util.parse_jsonstat(js)


def test_sparse_values_and_nordic_quarters():
    js = _jsonstat2({"ContentsCode": ["x"], "Tid": ["2026K1", "2026K2"]}, {"1": 4.5})
    df = _util.parse_jsonstat(js)
    assert df["date"].tolist() == [date(2026, 4, 1)]     # K2 = second quarter, at its start


def test_a_plus_in_a_value_code_stays_a_plus():
    """Statistics Sweden spells 'both sexes' 1+2; parse_qsl would make it '1 2'."""
    table, sel = pxweb.split_code("TAB6387?Kon=1+2&Arbetskraftstillh=ALÖSP&Tid=*")
    assert table == "TAB6387"
    assert sel == [("Kon", "1+2"), ("Arbetskraftstillh", "ALÖSP"), ("Tid", "*")]


def test_boc_valet_skips_days_without_a_value():
    js = {"observations": [
        {"d": "2026-10-08", "V39079": {"v": "2.25"}},
        {"d": "2026-10-09", "FXUSDCAD": {"v": "1.4271"}},
        {"d": "2026-10-07", "V39079": {"v": ""}},
        {"d": "2009-04-21", "V39079": {"v": "0.25"}}]}
    df = boc._parse(js, "V39079")
    assert df["date"].tolist() == [date(2009, 4, 21), date(2026, 10, 8)]
    assert df["value"].tolist() == [0.25, 2.25]


def test_statcan_reads_a_vector_and_refuses_a_failed_one():
    ok = [{"status": "SUCCESS", "object": {"vectorId": 2062815, "vectorDataPoint": [
        {"refPer": "1976-01-01", "value": 7.1, "scalarFactorCode": 0},
        {"refPer": "2026-09-01", "value": 6.5, "scalarFactorCode": 0}]}}]
    df = statcan._parse(ok)
    assert df["value"].tolist() == [7.1, 6.5]
    with pytest.raises(ValueError):
        statcan._parse([{"status": "FAILED", "object": "Vector not found"}])


def test_ine_dates_by_year_and_period_not_by_timestamp():
    """Fecha is local midnight in Madrid: in UTC it falls on the previous day."""
    js = {"Data": [
        {"Fecha": 1785535200000, "FK_Periodo": 8, "Anyo": 2026, "Valor": 104.638, "Secreto": False},
        {"Fecha": 1780264800000, "FK_Periodo": 20, "Anyo": 2026, "Valor": 9.87, "Secreto": False},
        {"Fecha": 0, "FK_Periodo": 7, "Anyo": 2026, "Valor": 1.0, "Secreto": True}]}
    df = ine._parse(js)
    assert df["date"].tolist() == [date(2026, 4, 1), date(2026, 8, 1)]   # Q2 at quarter start
    assert df["value"].tolist() == [9.87, 104.638]


# ---- the keyed two: Destatis GENESIS and e-Stat ------------------------------

GENESIS_HEADER = ("statistics_code;statistics_label;time_code;time_label;time;"
                  "1_variable_code;1_variable_label;1_variable_attribute_code;1_variable_attribute_label;"
                  "2_variable_code;2_variable_label;2_variable_attribute_code;2_variable_attribute_label;"
                  "value;value_unit;value_variable_code;value_variable_label")


def _genesis_zip(rows: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("61111-0002_en_flat.csv", "\ufeff" + "\n".join([GENESIS_HEADER] + rows))
    return buf.getvalue()


CPI_ROWS = [
    "61111;CPI;JAHR;Year;1991;MONAT;Months;MONAT01;January;DINSG;Germany;DG;Germany;60.5;2020=100;PREIS1;Consumer price index",
    "61111;CPI;JAHR;Year;1991;MONAT;Months;MONAT01;January;DINSG;Germany;DG;Germany;-;%;PREIS1;in",
    "61111;CPI;JAHR;Year;2026;MONAT;Months;MONAT08;August;DINSG;Germany;DG;Germany;125.8;2020=100;PREIS1;Consumer price index",
    "61111;CPI;JAHR;Year;2026;MONAT;Months;MONAT12;December;DINSG;Germany;DG;Germany;...;2020=100;PREIS1;Consumer price index",
]


def test_genesis_keeps_the_pinned_measure_and_dates_by_month_code():
    df = destatis._parse(_genesis_zip(CPI_ROWS), {"value_unit": "2020=100"})
    assert df["date"].tolist() == [date(1991, 1, 1), date(2026, 8, 1)]   # '...' dropped
    assert df["value"].tolist() == [60.5, 125.8]


def test_genesis_refuses_two_measures_and_a_filter_that_matches_nothing():
    with pytest.raises(_util.UnpinnedKey):
        destatis._parse(_genesis_zip(CPI_ROWS), {})
    with pytest.raises(ValueError, match="rebased"):
        destatis._parse(_genesis_zip(CPI_ROWS), {"value_unit": "2025=100"})


def test_genesis_code_splits_request_parameters_from_column_filters():
    table, request, columns = destatis.split_code(
        "42153-0001?classifyingvariable1=WZ08V1&classifyingkey1=WZ08-B-05&value_unit=2020=100")
    assert table == "42153-0001"
    assert request == {"classifyingvariable1": "WZ08V1", "classifyingkey1": "WZ08-B-05"}
    assert columns == {"value_unit": "2020=100"}


def _estat(values):
    return {"GET_STATS_DATA": {"RESULT": {"STATUS": 0, "ERROR_MSG": "ok"},
                               "STATISTICAL_DATA": {"DATA_INF": {"VALUE": values}}}}


def test_estat_keeps_months_and_drops_years_and_fiscal_years():
    v = [{"@tab": "1", "@cat01": "0161", "@area": "00000", "@time": "2026000808", "$": "102"},
         {"@tab": "1", "@cat01": "0161", "@area": "00000", "@time": "2025000000", "$": "100"},
         {"@tab": "1", "@cat01": "0161", "@area": "00000", "@time": "2025100000", "$": "100.4"},
         {"@tab": "1", "@cat01": "0161", "@area": "00000", "@time": "1970000101", "$": "27.5"},
         {"@tab": "1", "@cat01": "0161", "@area": "00000", "@time": "1970000202", "$": "-"}]
    df = estat._parse(_estat(v))
    assert df["date"].tolist() == [date(1970, 1, 1), date(2026, 8, 1)]
    assert df["value"].tolist() == [27.5, 102.0]


def test_estat_refuses_two_series_and_reports_an_api_error():
    v = [{"@cat01": "0161", "@time": "2026000808", "$": "102"},
         {"@cat01": "0178", "@time": "2026000808", "$": "102.5"}]
    with pytest.raises(_util.UnpinnedKey):
        estat._parse(_estat(v))
    bad = {"GET_STATS_DATA": {"RESULT": {"STATUS": 100, "ERROR_MSG": "invalid appId"}}}
    with pytest.raises(ValueError, match="invalid appId"):
        estat._parse(bad)
