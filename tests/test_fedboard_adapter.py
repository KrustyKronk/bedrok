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
