"""Fed research-file parsing (pure — fixtures built in memory, no network)."""
import io

import pandas as pd
import pytest
import xlrd  # noqa: F401  — the .xls path needs it; fail loudly here if absent

from bedrok.ingest.adapters import fedfiles


# Writing a real OLE2 .xls needs xlwt, which is unmaintained and not a
# dependency — so the two .xls parsers are exercised through a stubbed
# _read_xls (their job is column selection and date handling, not decoding).

# ---- MCT: a clean CSV, so the fixture is the real shape verbatim ------------

MCT_CSV = (
    "Section/Chart Name,,MCT Common to all,,,,Trend Inflation,\n"
    "Top-Level-Route,,MCT,MCT,MCT,MCT,trend-inflation,trend-inflation\n"
    "Radio Button Routes,,,,,,,\n"
    "column name,Date,MCT,MCT,MCT,MCT,Headline PCE inflation (YoY),Core PCE inflation (YoY)\n"
    ",1/1/1960,1.26,1.56,1.87,-0.27,1.69,2.07\n"
    ",2/1/1960,1.30,1.60,1.90,-0.20,1.70,2.10\n"
    ",5/1/2026,3,3.42,3.85,1.6,4.07,3.41\n"
)


def test_mct_takes_the_point_estimate_not_a_band_edge():
    """Four columns are all named 'MCT': lower band, point estimate, upper band,
    and a fourth series. Picking the first by name would silently ship the
    68% lower bound as if it were the trend."""
    df = fedfiles._parse_mct(MCT_CSV.encode(), "MCT")
    assert list(df.columns) == ["date", "value"]
    assert df["value"].tolist() == [1.56, 1.60, 3.42]
    assert df["date"].iloc[0] == pd.Timestamp("1960-01-01").date()
    assert df["date"].iloc[-1] == pd.Timestamp("2026-05-01").date()


def test_mct_can_select_a_named_column():
    df = fedfiles._parse_mct(MCT_CSV.encode(), "Core PCE inflation (YoY)")
    assert df["value"].tolist() == [2.07, 2.10, 3.41]


def test_mct_handles_a_utf8_bom():
    df = fedfiles._parse_mct(("﻿" + MCT_CSV).encode("utf-8"), "MCT")
    assert len(df) == 3


# ---- WEI: a real .xlsx, so build one in memory with openpyxl ----------------

def _wei_xlsx() -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"note": ["front matter, not data"]}).to_excel(
            xw, sheet_name="Weekly Economic Index", index=False)
        pd.DataFrame({
            "Date": ["01/05/2008", "01/12/2008", "07/11/2026"],
            "WEI": [1.95, 1.94, 2.61],
            "WEI as of 9/25/2025": [1.96, 1.95, None],   # frozen vintage: ignored
        }).to_excel(xw, sheet_name="2008-current", index=False)
    return buf.getvalue()


def test_wei_reads_the_data_sheet_not_the_front_matter():
    df = fedfiles._parse_wei(_wei_xlsx(), "WEI")
    assert df["value"].tolist() == [1.95, 1.94, 2.61]
    assert df["date"].iloc[-1] == pd.Timestamp("2026-07-11").date()


# ---- GSCPI / ACM: exercise the parsers through a stubbed .xls reader --------

def test_gscpi_normalises_month_end_dates_to_month_start(monkeypatch):
    """Source dates are month-END; every other monthly series in the store is
    dated month-START, and a join across the two would otherwise miss."""
    raw = pd.DataFrame([["Date", "GSCPI"],
                        [None, None],
                        ["31-Jan-1998", -1.092175],
                        ["28-Feb-1998", -0.448778],
                        ["30-Jun-2026", 1.249402]])
    monkeypatch.setattr(fedfiles, "_read_xls", lambda *a, **k: raw)
    df = fedfiles._parse_gscpi(b"", "GSCPI")
    assert df["date"].tolist() == [pd.Timestamp("1998-01-01").date(),
                                   pd.Timestamp("1998-02-01").date(),
                                   pd.Timestamp("2026-06-01").date()]
    assert df["value"].iloc[0] == pytest.approx(-1.092175)


def test_acm_selects_the_requested_maturity(monkeypatch):
    raw = pd.DataFrame({"DATE": ["14-Jun-1961", "20-Jul-2026"],
                        "ACMTP10": [0.0902, 0.6871]})
    monkeypatch.setattr(fedfiles, "_read_xls", lambda *a, **k: raw)
    df = fedfiles._parse_acm(b"", "ACMTP10")
    assert df["date"].tolist() == [pd.Timestamp("1961-06-14").date(),
                                   pd.Timestamp("2026-07-20").date()]
    assert df["value"].tolist() == [0.0902, 0.6871]


# ---- dispatch --------------------------------------------------------------

def test_unknown_file_key_fails_loudly():
    with pytest.raises(ValueError, match="unknown fedfiles key"):
        fedfiles.fetch("nosuchfile:COL")


def test_download_is_cached_so_one_workbook_serves_many_series(monkeypatch):
    """ACM is a 10MB workbook carrying 30 series — re-downloading per column
    would turn one request into thirty."""
    calls = []

    class _R:
        content = MCT_CSV.encode()
        def raise_for_status(self): pass

    def _get(url, **kw):
        calls.append(url)
        return _R()

    fedfiles._download.cache_clear()
    monkeypatch.setattr(fedfiles.requests, "get", _get)
    fedfiles.fetch("mct:MCT")
    fedfiles.fetch("mct:Core PCE inflation (YoY)")
    assert len(calls) == 1
    fedfiles._download.cache_clear()


def test_start_filters_client_side(monkeypatch):
    class _R:
        content = MCT_CSV.encode()
        def raise_for_status(self): pass

    fedfiles._download.cache_clear()
    monkeypatch.setattr(fedfiles.requests, "get", lambda *a, **k: _R())
    df = fedfiles.fetch("mct:MCT", start="2026-01-01")
    assert df["date"].tolist() == [pd.Timestamp("2026-05-01").date()]
    fedfiles._download.cache_clear()


# ---- Regional Fed files and FHFA (session 3b) --------------------------------

def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_chicago_nfci_reads_the_named_column():
    csv = b"Friday_of_Week,NFCI,ANFCI\n09/25/2026,-0.49,-0.51\n10/02/2026,-0.494,-0.504\n"
    df = fedfiles._parse_nfci(csv, "ANFCI")
    assert [str(d) for d in df["date"]] == ["2026-09-25", "2026-10-02"]
    assert df["value"].tolist() == [-0.51, -0.504]


def test_cfnai_reads_year_slash_month_and_drops_nan():
    csv = b"Date,CFNAI,CFNAI_MA3\n1967/03,-0.35,NaN\n2026/08,NaN,0.1\n2026/07,-0.04,0\n"
    df = fedfiles._parse_cfnai(csv)
    assert df.values.tolist() == [[pd.Timestamp("1967-03-01").date(), -0.35],
                                  [pd.Timestamp("2026-07-01").date(), -0.04]]


def test_cleveland_cpi_is_annualised_from_the_monthly_change_not_the_rounded_column():
    csv = b"date ,mcpi_monthly_chg,mcpi_ann_monthly_chg\n8/1/2026,0.0017349,2.1\n"
    df = fedfiles._parse_cleveland_cpi(csv, "mcpi_monthly_chg")
    assert df["value"].iloc[0] == pytest.approx(((1.0017349) ** 12 - 1) * 100)
    assert df["value"].iloc[0] != 2.1


def test_cleveland_expectations_are_turned_into_percent():
    content = _xlsx({"Expected Inflation": [
        ["Model Output Date", " 1 year Expected Inflation", " 2 year Expected Inflation"],
        [pd.Timestamp("2026-09-01").to_pydatetime(), 0.026438, 0.0263]]})
    df = fedfiles._parse_cleveland_expectations(content, "1")
    assert df["value"].iloc[0] == pytest.approx(2.6438)


def test_dallas_pce_finds_its_header_in_row_four_and_skips_nan_strings():
    content = _xlsx({"Web - Historical": [
        [None, None, None, None, "Trimmed mean PCE inflation rates"],
        [], [],
        [None, "1-month", "6-month", "12-month"],
        [pd.Timestamp("1977-01-01").to_pydatetime(), "#NAN", "#NAN", "#NAN"],
        [pd.Timestamp("2026-08-01").to_pydatetime(), 1.92, 2.35, 2.19]]})
    df = fedfiles._parse_dallas_pce(content)
    assert df.values.tolist() == [[pd.Timestamp("2026-08-01").date(), 2.19]]


def test_atlanta_sticky_takes_the_twelve_month_rate_of_its_own_block():
    header = ["Date", "Sticky CPI (monthly)", "1-mo", "3-mo", "12mo",
              "Core Sticky CPI (monthly)", "1-mo", "3-mo", "12mo"]
    content = _xlsx({"Data": [header,
                              [pd.Timestamp("2026-08-01").to_pydatetime(),
                               1, 2, 3, 9.9, 5, 6, 7, 2.7006]]})
    df = fedfiles._parse_atlanta_sticky(content)
    assert df["value"].tolist() == [2.7006]


def test_philly_two_digit_years_are_not_put_in_the_future():
    csv = b"DATE,GAC,NOC\nMay-68,32.2,30.6\nSep-26,37.8,1\n"
    df = fedfiles._parse_philly_bos(csv)
    assert [str(d) for d in df["date"]] == ["1968-05-01", "2026-09-01"]


def test_fhfa_keeps_the_purchase_only_monthly_national_index():
    csv = (b"hpi_type,hpi_flavor,frequency,level,place_name,place_id,yr,period,"
           b"index_nsa,index_sa\n"
           b"traditional,purchase-only,monthly,USA or Census Division,United States,USA,2026,7,440,443.52\n"
           b"traditional,all-transactions,quarterly,USA or Census Division,United States,USA,2026,2,600,\n"
           b"traditional,purchase-only,monthly,USA or Census Division,Pacific,DV_PAC,2026,7,500,501\n")
    df = fedfiles._parse_fhfa(csv)
    assert df.values.tolist() == [[pd.Timestamp("2026-07-01").date(), 443.52]]
