"""Fed Board release files: one SDMX parser for every release. No network."""
import io

import pandas as pd
import pytest

from bedrok.ingest.adapters import fedboard

XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<message:MessageGroup xmlns:message="http://www.SDMX.org/resources/SDMXML/schemas/v1_0/message"
  xmlns:frb="http://www.federalreserve.gov/structure/compact/common">
 <frb:DataSet id="H15" xmlns:kf="http://www.federalreserve.gov/structure/compact/H15_H15">
  <kf:Series FREQ="9" SERIES_NAME="RIFLGFCY10_N.B" UNIT_MULT="1">
   <frb:Obs OBS_STATUS="A" OBS_VALUE="5.28" TIME_PERIOD="2026-10-07" />
   <frb:Obs OBS_STATUS="ND" OBS_VALUE="-9999" TIME_PERIOD="2026-10-12" />
   <frb:Obs OBS_STATUS="A" OBS_VALUE="5.22" TIME_PERIOD="2026-10-08" />
  </kf:Series>
  <kf:Series FREQ="129" SERIES_NAME="RIFSPFF_N.M" UNIT_MULT="1">
   <frb:Obs OBS_STATUS="A" OBS_VALUE="3.75" TIME_PERIOD="2026-09-30" />
  </kf:Series>
  <kf:Series FREQ="162" SERIES_NAME="SUBLPDCILS_N.Q" UNIT_MULT="1">
   <frb:Obs OBS_STATUS="A" OBS_VALUE="0.0" TIME_PERIOD="2026-09-30" />
  </kf:Series>
  <kf:Series FREQ="19" SERIES_NAME="B1001NCBA" UNIT_MULT="1000000">
   <frb:Obs OBS_STATUS="A" OBS_VALUE="19883186.3" TIME_PERIOD="2026-09-30" />
  </kf:Series>
  <kf:Series FREQ="129" SERIES_NAME="RIFSPFF_N.M" UNIT_MULT="1">
   <frb:Obs OBS_STATUS="A" OBS_VALUE="99" TIME_PERIOD="2026-09-30" />
  </kf:Series>
 </frb:DataSet>
</message:MessageGroup>"""


def _one(name, scale=None):
    found = fedboard.parse(io.BytesIO(XML), {name})
    return fedboard.to_frame(*found[name], scale=scale)


def test_daily_keeps_its_dates_and_drops_no_data_days():
    df = _one("RIFLGFCY10_N.B")
    assert [str(d) for d in df["date"]] == ["2026-10-07", "2026-10-08"]
    assert df["value"].tolist() == [5.28, 5.22]


def test_monthly_and_quarterly_move_from_period_end_to_period_start():
    assert str(_one("RIFSPFF_N.M")["date"].iloc[0]) == "2026-09-01"
    assert str(_one("SUBLPDCILS_N.Q")["date"].iloc[0]) == "2026-07-01"


def test_a_repeated_series_name_keeps_the_first():
    assert _one("RIFSPFF_N.M")["value"].tolist() == [3.75]


def test_scale_applies_unit_mult_then_divides_to_the_catalog_unit():
    df = _one("B1001NCBA", scale="billions")
    assert df["value"].iloc[0] == pytest.approx(19883.1863)


def test_only_the_names_asked_for_are_kept():
    assert set(fedboard.parse(io.BytesIO(XML), {"RIFLGFCY10_N.B"})) == {"RIFLGFCY10_N.B"}


def test_fetch_downloads_each_release_once_for_every_primed_series(monkeypatch):
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("H15_data.xml", XML)
    calls = []
    monkeypatch.setattr(fedboard, "_download", lambda rel: calls.append(rel) or buf.getvalue())
    fedboard.prime(["H15:RIFLGFCY10_N.B", "H15:RIFSPFF_N.M"])
    fedboard.fetch("H15:RIFLGFCY10_N.B")
    fedboard.fetch("H15:RIFSPFF_N.M", start="2026-01-01")
    assert calls == ["H15"]
    with pytest.raises(ValueError, match="not found"):
        fedboard.prime(["H15:NOPE"])
        fedboard.fetch("H15:NOPE")


SLOOS_HTML = """
<table title="Figure 1"><tr><th rowspan="2">Period</th>
<th colspan="2">Panel 1: Net Percentage of Domestic Respondents Tightening Standards for C&amp;I Loans</th>
<th colspan="2">Panel 3: Net Percentage of Domestic Respondents Reporting Stronger Demand for C&amp;I Loans</th></tr>
<tr><th>Large and medium</th><th>Small</th><th>Large and medium</th><th>Small</th></tr>
<tr><td>1990:2</td><td>54.4</td><td>52.7</td><td>n.a.</td><td>n.a.</td></tr>
<tr><td>2026:3</td><td>0.0</td><td>1.8</td><td>16.1</td><td>3.6</td></tr></table>
<table title="Figure 4"><tr><th rowspan="2">Period</th>
<th colspan="2">Panel 1: Net Percentage of Domestic Respondents Tightening Standards on Consumer Loans ***</th>
<th rowspan="2">Panel 2: Increased Willingness</th></tr>
<tr><th>Credit cards</th><th>New and used autos</th></tr>
<tr><td>2026:3</td><td>6.7</td><td>-4.5</td><td>-2.0</td></tr></table>
"""


def test_the_sloos_chart_data_page_reads_by_header_words_not_position():
    """Session B: the fallback for the one release file with no release page.
    Headers span columns and 'n.a.' is a gap, not a zero."""
    small = fedboard.parse_sloos_html(SLOOS_HTML, "SUBLPDCISS_N.Q")
    assert [str(d) for d in small["date"]] == ["1990-04-01", "2026-07-01"]
    assert small["value"].tolist() == [52.7, 1.8]
    demand = fedboard.parse_sloos_html(SLOOS_HTML, "SUBLPDCILD_N.Q")
    assert demand["value"].tolist() == [16.1] and str(demand["date"].iloc[0]) == "2026-07-01"
    cards = fedboard.parse_sloos_html(SLOOS_HTML, "SUBLPDCLCS_N.Q")
    assert cards["value"].tolist() == [6.7]
    with pytest.raises(ValueError, match="not found"):
        fedboard.parse_sloos_html("<table></table>", "SUBLPDCILS_N.Q")


def test_sloos_falls_back_to_the_chart_data_page_only_when_its_zip_is_gone(monkeypatch):
    import requests

    def gone(rel):
        err = requests.HTTPError("404 Client Error")
        raise err
    monkeypatch.setattr(fedboard, "_download", gone)
    monkeypatch.setattr(fedboard, "_sloos_chart_data", lambda: SLOOS_HTML)
    fedboard.prime(["SLOOS:SUBLPDCILS_N.Q", "H15:RIFLGFCY10_N.B"])
    df = fedboard.fetch("SLOOS:SUBLPDCILS_N.Q", start="2026-01-01")
    assert df["value"].tolist() == [0.0] and "fallback" in df.attrs
    with pytest.raises(requests.HTTPError):          # H.15 has no fallback
        fedboard.fetch("H15:RIFLGFCY10_N.B")


def test_release_status_reads_the_zip_headers_and_refuses_an_html_answer(monkeypatch):
    import requests

    class Resp:
        def __init__(self, status, headers):
            self.status_code, self.headers = status, headers

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code))
    monkeypatch.setattr(fedboard.requests, "head", lambda url, **kw: Resp(200, {
        "content-type": "application/zip", "last-modified": "Fri, 09 Oct 2026 20:15:08 GMT",
        "content-length": "4300000"}))
    df = fedboard.release_status("H15")
    assert str(df["date"].iloc[0]) == "2026-10-09" and df["value"].iloc[0] == 4300000.0
    monkeypatch.setattr(fedboard.requests, "head", lambda url, **kw: Resp(200, {"content-type": "text/html"}))
    with pytest.raises(ValueError, match="gone"):
        fedboard.release_status("SLOOS")
    monkeypatch.setattr(fedboard.requests, "head", lambda url, **kw: Resp(404, {}))
    with pytest.raises(requests.HTTPError):
        fedboard.release_status("SLOOS")


def test_the_download_cache_holds_every_release_the_catalog_uses():
    import pandas as pd
    from bedrok.ingest import pipeline
    cat = pd.read_csv(pipeline.CATALOG_CSV, dtype=str)
    releases = {c.split(":")[0] for c in cat.loc[cat["source"] == "fedboard", "source_code"]}
    assert fedboard._download.cache_parameters()["maxsize"] >= len(releases)   # GBPUSD reads H10, already counted
