"""CFTC Commitments of Traders parsing (pure — zips built in memory)."""
import csv
import io
import zipfile

import pandas as pd

from bedrok.ingest.adapters import cftc

HEADER = ["Market and Exchange Names", "As of Date in Form YYMMDD",
          "As of Date in Form YYYY-MM-DD", "CFTC Contract Market Code",
          "Open Interest (All)", "Noncommercial Positions-Long (All)",
          "Noncommercial Positions-Short (All)"]


def _row(name, code, date, long_, short):
    return [name, "", date, code, "100000", str(long_), str(short)]


def _zip(rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    w.writerows(rows)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("annual.txt", buf.getvalue())
    return out.getvalue()


def test_net_positioning_is_long_minus_short():
    content = _zip([
        _row("GOLD - COMMODITY EXCHANGE INC.", "088691", "2026-07-14", 250000, 63318),
        _row("GOLD - COMMODITY EXCHANGE INC.", "088691", "2026-07-07", 240000, 70000),
    ])
    per = cftc._extract(*cftc._parse_zip(content))
    df = per["cot_gold_net_noncomm"]
    assert df["value"].tolist() == [170000.0, 186682.0]        # chronological
    assert df["date"].tolist() == [pd.Timestamp("2026-07-07").date(),
                                   pd.Timestamp("2026-07-14").date()]


def test_whitespace_padded_codes_still_match():
    """Older archive files pad the code column ("'088691 '")."""
    content = _zip([_row("GOLD", " 088691 ", "1986-05-06", 100, 40)])
    per = cftc._extract(*cftc._parse_zip(content))
    assert per["cot_gold_net_noncomm"]["value"].tolist() == [60.0]


def test_reused_contract_codes_do_not_splice_two_instruments():
    """099741 was the European Currency Unit in 1986 and became EURO FX in
    1999. Concatenating them would fabricate a continuous series across two
    different instruments separated by twelve years."""
    content = _zip([
        _row("EUROPEAN CURRENCY UNIT - IMM", "099741", "1986-08-29", 500, 200),
        _row("EURO FX - CHICAGO MERCANTILE EXCHANGE", "099741", "2026-07-14", 100, 12705),
    ])
    per = cftc._extract(*cftc._parse_zip(content))
    df = per["cot_eur_net_noncomm"]
    assert len(df) == 1
    assert df["date"].iloc[0].year == 2026


def test_markets_are_matched_by_code_not_name():
    """ICE Europe's WTI look-alike (067411) must not be mistaken for the NYMEX
    benchmark (067651), and renamed markets must still match."""
    content = _zip([
        _row("CRUDE OIL, LIGHT SWEET-WTI - ICE FUTURES EUROPE", "067411", "2026-07-14", 9, 9),
        _row("WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE", "067651", "2026-07-14", 200000, 137317),
        # the 10y note trades under this code under an older name
        _row("6.5-10 YEAR U.S. TREASURY NOTE - CHICAGO BOARD OF TRADE", "043602",
             "1986-05-06", 1000, 400),
    ])
    per = cftc._extract(*cftc._parse_zip(content))
    assert per["cot_wti_net_noncomm"]["value"].tolist() == [62683.0]
    assert per["cot_ust10y_net_noncomm"]["value"].tolist() == [600.0]
    assert "cot_ice_wti" not in per


def test_unrelated_markets_and_bad_rows_are_ignored():
    content = _zip([
        _row("SOYBEANS - CHICAGO BOARD OF TRADE", "005602", "2026-07-14", 1, 2),
        _row("GOLD", "088691", "not-a-date", 1, 2),
        _row("GOLD", "088691", "2026-07-14", "n/a", "5"),
        _row("GOLD", "088691", "2026-07-07", 10, 4),
    ])
    per = cftc._extract(*cftc._parse_zip(content))
    assert set(per) == {"cot_gold_net_noncomm"}
    assert per["cot_gold_net_noncomm"]["value"].tolist() == [6.0]


def test_empty_or_unexpected_file_yields_nothing_rather_than_raising():
    assert cftc._extract([], []) == {}
    other = _zip([])
    header, rows = cftc._parse_zip(other)
    assert cftc._extract(header, rows) == {}


def test_fetch_merges_years_and_reports_a_missing_archive(monkeypatch):
    class _R:
        def __init__(self, status, content=b""):
            self.status_code, self.content = status, content
        def raise_for_status(self): pass

    def _get(url, **kw):
        if "2026" in url:
            return _R(404)                       # early January: not published yet
        return _R(200, _zip([_row("GOLD", "088691", "2025-12-30", 10, 4)]))

    monkeypatch.setattr(cftc.requests, "get", _get)
    per, missing = cftc.fetch_cot([2025, 2026])
    assert missing == ["2026"]
    assert per["cot_gold_net_noncomm"]["value"].tolist() == [6.0]
