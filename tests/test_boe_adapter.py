"""Bank of England IADB CSV parsing and chunked fetch (pure — no network)."""
import pandas as pd
import pytest

from bedrok.ingest.adapters import boe

# Shape captured from a live IADB response (note the CRLF line endings).
SAMPLE_CSV = ("DATE,IUDBEDR\r\n"
              "02 Jan 2024,5.25\r\n"
              "03 Jan 2024,5.25\r\n"
              "01 Feb 2024,5.0\r\n")


def test_parses_iadb_csv_including_its_date_format():
    df = boe._parse_csv(SAMPLE_CSV)
    assert list(df.columns) == ["date", "value"]
    assert df["date"].tolist() == [pd.Timestamp("2024-01-02").date(),
                                   pd.Timestamp("2024-01-03").date(),
                                   pd.Timestamp("2024-02-01").date()]
    assert df["value"].tolist() == [5.25, 5.25, 5.0]


def test_value_column_is_found_by_position_not_by_name():
    """Each series has its own column header, so the parser cannot hardcode one."""
    df = boe._parse_csv("DATE,IUDSOIA\r\n02 Jan 2024,5.19\r\n")
    assert df["value"].tolist() == [5.19]


def test_blank_and_unparseable_rows_are_dropped():
    df = boe._parse_csv("DATE,IUDBEDR\r\n02 Jan 2024,5.25\r\n03 Jan 2024,\r\nnot-a-date,1.0\r\n")
    assert len(df) == 1


def test_html_body_is_rejected_loudly(monkeypatch):
    """The endpoint answers 200 with an HTML error page when a window is too
    wide — parsing that as CSV would produce silent nonsense."""
    class _R:
        text = "<!DOCTYPE html><html><body>Error</body></html>"
        def raise_for_status(self): pass

    monkeypatch.setattr(boe.requests, "get", lambda *a, **k: _R())
    with pytest.raises(boe.BoEResponseError, match="HTML page instead of CSV"):
        boe._request("IUDBEDR", "01/Jan/1963", "31/Dec/2026")


def test_fetch_chunks_history_and_dedupes_overlapping_edges(monkeypatch):
    windows = []

    def _fake_request(code, date_from, date_to):
        windows.append((date_from, date_to))
        # every chunk reports the same edge date — the overlap the dedupe exists for
        return ("DATE,IUDBEDR\r\n"
                "01 Jan 2024,1.0\r\n"
                f"02 Jan 2024,{len(windows)}.0\r\n")

    monkeypatch.setattr(boe, "_request", _fake_request)
    monkeypatch.setattr(boe, "EARLIEST_YEAR", 1990)
    monkeypatch.setattr(pd.Timestamp, "now", classmethod(lambda cls: pd.Timestamp("2026-07-21")))

    df = boe.fetch("IUDBEDR")
    assert len(windows) == 3                     # 1990-2004, 2005-2019, 2020-2026
    assert windows[0] == ("01/Jan/1990", "31/Dec/2004")
    assert windows[-1] == ("01/Jan/2020", "31/Dec/2026")
    assert len(df) == 2                          # deduped, not 6 rows
    assert df["value"].tolist() == [1.0, 3.0]    # keep='last' wins the overlap


def test_fetch_with_start_narrows_the_window_and_filters(monkeypatch):
    monkeypatch.setattr(boe, "_request",
                        lambda c, f, t: "DATE,X\r\n01 Jan 2024,1.0\r\n01 Jan 2026,2.0\r\n")
    monkeypatch.setattr(pd.Timestamp, "now", classmethod(lambda cls: pd.Timestamp("2026-07-21")))
    df = boe.fetch("IUDBEDR", start="2025-01-01")
    assert df["date"].tolist() == [pd.Timestamp("2026-01-01").date()]
