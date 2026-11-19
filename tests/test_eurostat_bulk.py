"""Eurostat multi-geo bulk sync: the country seed, licensing, and the non-EU carve-out."""
import duckdb
import pandas as pd
import pytest

from bedrok.core import licensing, store
from bedrok.ingest import eurostat_geos, pipeline


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
        "PL": _frame(["2019-01-01"], [2.0]),     # inside the grant, stopped publishing
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
    # EL is in the seed but not the payload: a country that drops out of a
    # dataset between seed and sync.
    _seed_countries(monkeypatch, {"testind_": ["DE", "IT", "EL"]})
    return spec


def _seed_countries(monkeypatch, countries):
    monkeypatch.setattr(eurostat_geos, "COUNTRIES", countries)


def test_only_seeded_geos_are_stored(con, fake_fetch, one_set):
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
    _seed_countries(monkeypatch, {"testind_": ["DE", "PL"]})
    report = pipeline.sync_eurostat_multi(con)
    assert ("testind_pl", "skip", "dead/stale geo") in report
    assert con.execute("SELECT count(*) FROM macro_series WHERE series_id='testind_pl'"
                       ).fetchone()[0] == 0


def test_a_seed_country_outside_the_reuse_grant_stops_the_sync(con, fake_fetch, one_set,
                                                                 monkeypatch):
    """The seed is an allowlist inside the grant; a US row in it is a bug, not data."""
    _seed_countries(monkeypatch, {"testind_": ["DE", "US"]})
    report = pipeline.sync_eurostat_multi(con)
    assert report[0][1] == "FAIL" and "outside Eurostat's reuse grant" in report[0][2]
    assert con.execute("SELECT count(*) FROM macro_series WHERE series_id='testind_us'"
                       ).fetchone()[0] == 0


def test_the_seed_stays_inside_the_reuse_grant_and_names_real_families():
    """Every seeded country is an EU, EFTA or candidate country (never the
    aggregates, which each family names itself), and every seeded prefix is a
    family. Kosovo, a potential candidate only, is never seeded (2026-10-12)."""
    prefixes = {s["prefix"] for s in pipeline.EUROSTAT_BULK_SETS}
    aggregates = {"EA", "EA19", "EA20", "EA21", "EU", "EU27_2020", "EEA"}
    for prefix, geos in eurostat_geos.load().items():
        assert prefix in prefixes
        assert set(geos) <= pipeline.REUSE_GEOS - aggregates, prefix
        assert len(geos) == len(set(geos)) and "XK" not in geos
    assert set(eurostat_geos.load()) == prefixes


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


def test_incremental_start_uses_oldest_member(con, fake_fetch, one_set, monkeypatch):
    # Every seeded country held: a seeded one never stored (EL in one_set)
    # would make every refresh fetch the whole history.
    _seed_countries(monkeypatch, {"testind_": ["DE", "IT"]})
    pipeline.sync_eurostat_multi(con)                       # full pull first
    # IT lags DE by a month; the family must re-fetch from the OLDER edge
    con.execute("DELETE FROM macro_series WHERE series_id='testind_it' AND date='2026-06-01'")
    fake_fetch.clear()
    pipeline.sync_eurostat_multi(con, incremental=True)
    assert fake_fetch[0][1] == "2026-05"


def test_a_country_new_to_the_seed_gets_its_whole_history(con, fake_fetch, one_set,
                                                          monkeypatch):
    """2026-11-19: the first incremental refresh after widening the families
    asked only for the periods since the others' latest, so every new country
    arrived with one or two rows. A seeded country the store does not hold
    yet makes the family fetch everything; once held, increments resume."""
    _seed_countries(monkeypatch, {"testind_": ["DE"]})
    pipeline.sync_eurostat_multi(con)
    _seed_countries(monkeypatch, {"testind_": ["DE", "IT"]})
    fake_fetch.clear()
    pipeline.sync_eurostat_multi(con, incremental=True)
    assert fake_fetch[0][1] is None
    fake_fetch.clear()
    pipeline.sync_eurostat_multi(con, incremental=True)
    assert fake_fetch[0][1] == "2026-06"


def test_a_failing_set_does_not_abort_the_others(con, monkeypatch):
    good = {"prefix": "good_", "code": "g", "label": "Good", "frequency": "monthly",
            "units": "percent", "name_tmpl": "Good — {geo}", "notes": ""}
    bad = {"prefix": "bad_", "code": "b", "label": "Bad", "frequency": "monthly",
           "units": "percent", "name_tmpl": "Bad — {geo}", "notes": ""}
    monkeypatch.setattr(pipeline, "EUROSTAT_BULK_SETS", [bad, good])
    _seed_countries(monkeypatch, {"good_": ["DE"], "bad_": ["DE"]})

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


def test_the_broad_hicp_set_keeps_only_geos_inside_the_reuse_grant(con, monkeypatch):
    """Eurostat's notice allows commercial reuse for EU, EFTA and candidate
    countries only; the broad set loops over the API response, so Kosovo
    (a potential candidate) and the US must be skipped there (2026-10-12)."""
    fresh = ["2026-04-01", "2026-05-01", "2026-06-01"]
    payload = {g: _frame(fresh, [1.0, 2.0, 3.0]) for g in ("PL", "TR", "CH", "XK", "US")}
    monkeypatch.setattr(pipeline.eurostat, "fetch_multi_geo", lambda code, start=None: (payload, {}))
    report = pipeline.sync_hicp_europe(con)
    got = {r[0] for r in con.execute("SELECT DISTINCT series_id FROM macro_series").fetchall()}
    assert got == {"hicp_yoy_pl", "hicp_yoy_tr", "hicp_yoy_ch"}
    assert ("hicp_yoy_xk", "skip", "outside Eurostat's reuse grant") in report
    assert "hicp_yoy_xk" in pipeline.RETIRED


def test_an_annual_family_is_not_mistaken_for_a_dead_geography(con, monkeypatch):
    """Session C: a year is dated to its first day, so 2025's value is ~650
    days old in late 2026. The 400-day dead-geo rule would have dropped every
    annual geography on the first sync."""
    spec = {"prefix": "testann_", "code": "nama_test?unit=X", "label": "Annual test",
            "frequency": "annual", "units": "percent", "staleness_days": 800,
            "ea_codes": ["EA21"], "name_tmpl": "Annual test — {geo}", "notes": "n"}
    monkeypatch.setattr(pipeline, "EUROSTAT_BULK_SETS", [spec])
    _seed_countries(monkeypatch, {"testann_": ["DE", "FR"]})
    year = pd.Timestamp.today().year - 1
    payload = {"EA21": _frame([f"{year}-01-01"], [1.2]), "DE": _frame([f"{year}-01-01"], [0.2]),
               "FR": _frame(["2019-01-01"], [1.0])}          # FR: really stopped
    monkeypatch.setattr(pipeline.eurostat, "fetch_multi_geo", lambda code, start=None: (payload, {}))
    report = pipeline.sync_eurostat_multi(con)
    got = {r[0] for r in con.execute("SELECT DISTINCT series_id FROM macro_series").fetchall()}
    assert got == {"testann_ea21", "testann_de"}
    assert ("testann_fr", "skip", "dead/stale geo") in report


def test_the_gdp_breakdown_families_name_their_parts_and_leave_out_de_nl_quarterly():
    rows = pipeline.static_catalog_rows().set_index("series_id")
    for part, _, _ in pipeline.GDP_PARTS:
        assert f"gdp_contrib_{part}_de" in rows.index
        assert f"gdp_contrib_q_{part}_es" in rows.index
        assert f"gdp_contrib_q_{part}_de" not in rows.index
        assert f"gdp_contrib_q_{part}_nl" not in rows.index
    assert rows.loc["gdp_annual_ea21", "frequency"] == "annual"
    assert rows.loc["gdp_contrib_hh_es", "units"] == "percentage points"
    assert "gdp_annual_ea20" not in rows.index           # EA21 only
