"""The JODI oil adapter (session K4). No network: the payload is the shape of
primaryyear2026.csv captured on 2026-10-29, cut short."""
from datetime import date

import pytest
import requests

from bedrok.ingest.adapters import jodi

# A month not yet reported is "-"; another country, product and flow share the file.
Y2026 = """REF_AREA,TIME_PERIOD,ENERGY_PRODUCT,FLOW_BREAKDOWN,UNIT_MEASURE,OBS_VALUE,ASSESSMENT_CODE
AE,2026-01,CRUDEOIL,CLOSTLV,CONVBBL,7596.0000,3
KZ,2026-06,CRUDEOIL,INDPROD,KBD,1858.9597,3
KZ,2026-06,NGL,INDPROD,KBD,350.8867,3
KZ,2026-06,CRUDEOIL,INDPROD,KTONS,7712.0000,3
KZ,2026-07,CRUDEOIL,INDPROD,KBD,1596.9126,3
KZ,2026-08,CRUDEOIL,INDPROD,KBD,-,3
"""
Y2025 = """REF_AREA,TIME_PERIOD,ENERGY_PRODUCT,FLOW_BREAKDOWN,UNIT_MEASURE,OBS_VALUE,ASSESSMENT_CODE
KZ,2025-03,CRUDEOIL,INDPROD,KBD,1845.1000,3
KZ,2025-12,CRUDEOIL,INDPROD,KBD,N/A,3
"""


def test_one_series_is_read_and_placeholders_dropped():
    assert jodi.parse(Y2026, "KZ", "CRUDEOIL", "INDPROD", "KBD") == [
        (date(2026, 6, 1), 1858.9597), (date(2026, 7, 1), 1596.9126)]
    assert jodi.parse(Y2025, "KZ", "CRUDEOIL", "INDPROD", "KBD") == [(date(2025, 3, 1), 1845.1)]


class _Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def test_years_from_the_start_with_the_current_years_file_name(monkeypatch):
    asked = []

    def fake_get(url, headers=None, timeout=None):
        asked.append(url.rsplit("/", 1)[1])
        if url.endswith("primaryyear2026.csv"):
            return _Resp(200, Y2026)
        if url.endswith("/2025.csv"):
            return _Resp(200, Y2025)
        return _Resp(404)
    monkeypatch.setattr(jodi.requests, "get", fake_get)
    jodi._download.cache_clear()
    df = jodi.fetch("KZ/CRUDEOIL/INDPROD/KBD?from=2016-01", start="2025-02-01", today=date(2026, 10, 29))
    assert asked == ["2025.csv", "primaryyear2026.csv"]
    assert df["date"].tolist() == [date(2025, 3, 1), date(2026, 6, 1), date(2026, 7, 1)]
    jodi.fetch("KZ/NGL/INDPROD/KBD?from=2016-01", start="2025-02-01", today=date(2026, 10, 29))
    assert len(asked) == 2, "the NGL series reuses the downloaded years"
    # once 2026 is over its file may be renamed: a 404 tries the other name
    jodi._download.cache_clear()
    asked.clear()
    df = jodi.fetch("KZ/CRUDEOIL/INDPROD/KBD", start="2026-01-01", today=date(2027, 2, 1))
    assert asked == ["2026.csv", "primaryyear2026.csv", "primaryyear2027.csv", "2027.csv"]
    assert df["date"].iloc[-1] == date(2026, 7, 1), "2027 has no file yet: no rows, no error"
    jodi._download.cache_clear()


def test_a_malformed_code_or_an_empty_answer_is_an_error(monkeypatch):
    with pytest.raises(ValueError, match="AREA/PRODUCT/FLOW/UNIT"):
        jodi.fetch("KZ/CRUDEOIL")
    monkeypatch.setattr(jodi.requests, "get", lambda *a, **k: _Resp(200, Y2025))
    jodi._download.cache_clear()
    with pytest.raises(ValueError, match="no rows"):
        jodi.fetch("XX/CRUDEOIL/INDPROD/KBD", start="2025-01-01", today=date(2025, 6, 1))
    jodi._download.cache_clear()
