"""World Bank Pink Sheet adapter: the pure workbook parser handles the real
layout — two header rows, YYYYMdd periods, '…' missing values, '**' footnote
marks and stray whitespace in column labels."""
import datetime as dt
import io

from openpyxl import Workbook

from bedrok.ingest.adapters import worldbank


def _pinksheet_bytes():
    wb = Workbook()
    ws = wb.active
    ws.title = worldbank.SHEET
    ws.append(["World Bank Commodity Price Data (The Pink Sheet)"])   # row 1 (idx 0)
    ws.append(["monthly prices in nominal US dollars, 1960 to present"])
    ws.append(["(monthly series are available only in nominal US dollars)"])
    ws.append(["Updated on ..."])                                     # row 4 (idx 3)
    ws.append([None, "Crude oil, Brent", "Gold", "Rubber, TSR20 **", "Urea "])  # NAME_ROW (idx 4)
    ws.append([None, "($/bbl)", "($/troy oz)", "($/kg)", "($/mt)"])   # UNIT_ROW (idx 5)
    ws.append(["1960M01", 1.63, 35.0, "…", 100.0])                    # DATA_START (idx 6)
    ws.append(["1960M02", 1.70, 36.0, 0.5, "…"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_workbook_shape_and_values():
    per, meta = worldbank._parse_workbook(_pinksheet_bytes())
    # three of our slugs match (Urea via trailing-space label, Rubber via '**')
    assert set(per) == {"wb_crude_brent", "wb_gold", "wb_rubber_tsr20", "wb_urea"}

    brent = per["wb_crude_brent"]
    assert list(brent.columns) == ["date", "value"]
    assert isinstance(brent["date"].iloc[0], dt.date)
    assert brent["date"].tolist() == [dt.date(1960, 1, 1), dt.date(1960, 2, 1)]
    assert brent["value"].tolist() == [1.63, 1.70]

    # '…' missing values are dropped, leaving one row each
    assert per["wb_rubber_tsr20"]["value"].tolist() == [0.5]
    assert per["wb_urea"]["value"].tolist() == [100.0]

    assert meta["wb_crude_brent"]["units"] == "($/bbl)"
    assert meta["wb_crude_brent"]["name"] == "Crude oil, Brent"
