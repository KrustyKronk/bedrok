"""Eurostat multi-geo bulk sync: curation, licensing, and the non-EU carve-out."""
import duckdb
import pandas as pd
import pytest

from bedrok.core import licensing, store
from bedrok.ingest import pipeline


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    store.init_db(c)
    return c


def _frame(dates, values):
    return pd.DataFrame({"date": [pd.Timestamp(d).date() for d in dates], "value": values})


@pytest.fixture
def fake_fetch(monkeypatch):
    """Stand in for the live API with a payload spanning every interesting case."""
    fresh = ["2026-04-01", "2026-05-01", "2026-06-01"]
    payload = {
        "DE": _frame(fresh, [1.0, 2.0, 3.0]),
        "IT": _frame(fresh, [4.0, 5.0, 6.0]),
        "US": _frame(fresh, [7.0, 8.0, 9.0]),    # non-EU: never packable
        "JP": _frame(fresh, [1.0, 1.0, 1.0]),    # non-EU: never packable
        "UK": _frame(["2019-01-01"], [2.0]),     # legacy geo, long dead
        "AL": _frame(fresh, [3.0, 3.0, 3.0]),    # real geo, just not curated
        # EL deliberately absent — mirrors prc_hpi_q having no Greek HPI
    }
    labels = {"DE": "Germany", "IT": "Italy", "US": "United States"}
    calls = []

    def _fake(code, start=None):
        calls.append((code, start))
        return payload, labels

    monkeypatch.setattr(pipeline.eurostat, "fetch_multi_geo", _fake)
    monkeypatch.setenv("BEDROK_PACE_EUROSTAT", "0")   # no network → no pacing
    return calls


@pytest.fixture
def one_set(monkeypatch):
    spec = {"prefix": "testind_", "code": "test_ds?unit=X", "label": "Test indicator",
            "frequency": "monthly", "units": "percent", "staleness_days": 110,
            "name_tmpl": "Test indicator — {geo}", "notes": "a note"}
    monkeypatch.setattr(pipeline, "EUROSTAT_BULK_SETS", [spec])
    return spec


def test_only_curated_geos_are_stored(con, fake_fetch, one_set):
    pipeline.sync_eurostat_multi(con)
    got = {r[0] for r in con.execute(
        "SELECT DISTINCT series_id FROM macro_series").fetchall()}
    assert got == {"testind_de", "testind_it"}


def test_non_eu_geos_never_enter_the_pack(con, fake_fetch, one_set):
    """Eurostat's CC BY carve-out excludes US/JP/CN data from commercial reuse."""
    pipeline.sync_eurostat_multi(con)
    for banned in ("testind_us", "testind_jp"):
        assert con.execute("SELECT count(*) FROM macro_series WHERE series_id = ?",
                           [banned]).fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM macro_catalog WHERE series_id = ?",
                           [banned]).fetchone()[0] == 0


def test_dead_geo_is_skipped_not_stored(con, fake_fetch, one_set, monkeypatch):
    monkeypatch.setattr(pipeline, "EU_CORE_GEOS", ["DE", "UK"])
    report = pipeline.sync_eurostat_multi(con)
    assert ("testind_uk", "skip", "dead/stale geo") in report
    assert con.execute("SELECT count(*) FROM macro_series WHERE series_id='testind_uk'"
                       ).fetchone()[0] == 0


def test_catalog_rows_carry_licence_and_metadata(con, fake_fetch, one_set):
    pipeline.sync_eurostat_multi(con)
    row = con.execute(
        "SELECT source, source_code, name, frequency, units, staleness_days, license "
        "FROM macro_catalog WHERE series_id='testind_de'").fetchone()
    source, code, name, freq, units, stale, lic = row
    assert source == "eurostat"
    assert code == "test_ds?unit=X&geo=DE"
    assert name == "Test indicator — Germany"      # label resolved, not the bare code
    assert (freq, units, stale) == ("monthly", "percent", 110)
    assert lic == licensing.ATTRIBUTION            # never NULL — would block the gate
    assert licensing.unreviewed_series(con) == []


def test_missing_geo_is_silent(con, fake_fetch, one_set):
    """A dataset that simply does not publish a curated geo is not an error."""
    report = pipeline.sync_eurostat_multi(con)
    assert [r for r in report if r[1] == "FAIL"] == []
    assert ("Test indicator", "ok", "2 geos") in report


def test_incremental_start_uses_oldest_member(con, fake_fetch, one_set):
    pipeline.sync_eurostat_multi(con)                       # full pull first
    # IT lags DE by a month; the family must re-fetch from the OLDER edge
    con.execute("DELETE FROM macro_series WHERE series_id='testind_it' AND date='2026-06-01'")
    fake_fetch.clear()
    pipeline.sync_eurostat_multi(con, incremental=True)
    assert fake_fetch[0][1] == "2026-05"


def test_a_failing_set_does_not_abort_the_others(con, monkeypatch):
    good = {"prefix": "good_", "code": "g", "label": "Good", "frequency": "monthly",
            "units": "percent", "name_tmpl": "Good — {geo}", "notes": ""}
    bad = {"prefix": "bad_", "code": "b", "label": "Bad", "frequency": "monthly",
           "units": "percent", "name_tmpl": "Bad — {geo}", "notes": ""}
    monkeypatch.setattr(pipeline, "EUROSTAT_BULK_SETS", [bad, good])

    def _fake(code, start=None):
        if code == "b":
            raise RuntimeError("eurostat is down")
        return {"DE": _frame(["2026-06-01"], [1.0])}, {"DE": "Germany"}

    monkeypatch.setattr(pipeline.eurostat, "fetch_multi_geo", _fake)
    monkeypatch.setenv("BEDROK_PACE_EUROSTAT", "0")
    report = pipeline.sync_eurostat_multi(con)
    assert ("Bad", "FAIL", "RuntimeError: eurostat is down") in report
    assert ("Good", "ok", "1 geos") in report


def test_bulk_prefixes_cover_every_set_so_sync_macro_skips_them():
    """A set missing from BULK_PREFIXES would be re-fetched one geo at a time."""
    for spec in pipeline.EUROSTAT_BULK_SETS:
        assert spec["prefix"] in pipeline.BULK_PREFIXES
    labels = [label for label, _, _ in pipeline.bulk_summary_specs()]
    for spec in pipeline.EUROSTAT_BULK_SETS:
        assert spec["label"] in labels
