"""What the public website (KrustyKronk/bedrok-web) relies on in this package.

The site depends on `bedrok` as a library (STATE.md item 21) and calls these
internals directly. Nothing else here treats them as a public API, so a
refactor could rename a parameter or drop a field without any other test
noticing, and the site would break on its next build. Each test below pins
one of them: its signature, and the fields the site reads from what it
returns, taken from bedrok-web's own code on 2026-11-23.

If a test here fails, the change breaks the website. Either keep the old
shape, or change bedrok-web in step and update this file with it.
"""
import inspect
from datetime import date
from importlib import resources

import duckdb
import pandas as pd
import pytest

from bedrok.core import config, geography, sources, store, units, warnings
from bedrok.engines import pctile
from bedrok.ingest import live, pipeline, transforms
from bedrok.ops import freshness


def _params(fn) -> list[str]:
    return list(inspect.signature(fn).parameters)


def test_profile_and_a_read_only_connection(tmp_path, monkeypatch):
    """bedrok_web.data.open_store: set_profile(path), then connect(read_only=True)."""
    assert _params(config.set_profile) == ["path"]
    assert _params(config.connect) == ["read_only"]
    for name in ("PROFILE_DIR", "PROFILE", "ROOT", "DATA_DIR", "DB_PATH", "BASE_CCY", "BASE_SYMBOL"):
        monkeypatch.setattr(config, name, getattr(config, name))   # restored after the test
    monkeypatch.delenv("BEDROK_DB", raising=False)
    monkeypatch.delenv("FDP_DB", raising=False)
    config.set_profile(tmp_path)
    assert config.PROFILE_DIR == tmp_path and tmp_path in config.DB_PATH.parents
    con = config.connect()
    store.init_db(con)
    con.close()
    ro = config.connect(read_only=True)
    with pytest.raises(duckdb.Error):
        ro.execute("CREATE TABLE t (x INT)")
    ro.close()


def test_the_store_tables_and_columns_the_site_selects():
    """bedrok_web.data.Data._load reads these columns by name; the site's own
    tests build their store with store.init_db and the two upserts."""
    assert _params(store.init_db) == ["con"]
    assert _params(store.upsert_macro_catalog) == ["con", "df"]
    assert _params(store.upsert_macro_series) == ["con", "series_id", "df"]
    con = duckdb.connect(":memory:")
    store.init_db(con)
    cat = con.execute("SELECT * FROM macro_catalog LIMIT 0").fetch_df().columns
    ser = con.execute("SELECT * FROM macro_series LIMIT 0").fetch_df().columns
    assert {"series_id", "source", "name", "frequency", "units", "notes", "license",
            "cache"} <= set(cat)
    assert {"series_id", "date", "value"} <= set(ser)


def test_sources_and_their_attribution():
    assert _params(sources.load) == [] and _params(sources.attribution) == ["source"]
    loaded = sources.load()
    assert isinstance(loaded, dict) and loaded
    for row in loaded.values():
        assert {"name", "license", "terms_url"} <= set(row)
    assert isinstance(sources.attribution(next(iter(loaded))), str)


def test_warnings_and_the_fields_the_site_prints():
    """Pages print a warning's severity pill, what happened, its effect, the
    cross-check and its source, and link it to series and to a period."""
    assert _params(warnings.load) == []
    loaded = warnings.load()
    assert loaded
    for w in loaded:
        d = dict(w)                                    # the site copies each one
        assert {"warning_id", "severity", "what_happened", "effect_on_data", "cross_check",
                "source_name", "source_url", "period_start", "period_end", "series_ids"} <= set(d)
        assert d["severity"] in {"disqualifying", "material", "caveat"}
        assert isinstance(d["series_ids"], (tuple, list))
        assert d["period_start"] is None or isinstance(d["period_start"], str)


def test_geography_of_a_series_and_its_code_lists():
    assert _params(geography.of_series) == ["series_id", "source"]
    assert geography.of_series("usdkzt", "nbk") == "kz"
    assert isinstance(geography.CODES, dict) and isinstance(geography.FAMILY_CODES, dict)


def test_money_levels():
    assert _params(units.is_money_level) == ["units"]
    assert units.is_money_level("millions USD") and not units.is_money_level("percent")


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    store.init_db(c)
    pipeline.load_catalog(c)
    months = pd.date_range(end="2026-10-01", periods=40, freq="MS")
    store.upsert_macro_series(c, "us_cpi", pd.DataFrame({"date": months.date,
                                                          "value": [300.0 + i for i in range(40)]}))
    yield c
    c.close()


def test_freshness_fields_the_site_reads(con):
    """model.releases groups the calendar by next_release; pages show
    latest_period and status."""
    assert _params(freshness.assess)[:2] == ["con", "series_id"]
    f = freshness.assess(con, "us_cpi")
    assert {"latest_period", "status", "next_release"} <= set(f)
    assert f["latest_period"] == "October 2026"
    nr = f["next_release"]
    # Always there when a rule answers; `date_exact` and `calendar` come only
    # with a publisher's own date, and the site reads them with .get.
    assert {"due_at", "expected_by", "period", "message", "source", "approximate"} <= set(nr)
    assert isinstance(nr["due_at"], str)
    assert freshness.assess(con, "us_vix")["status"] == "session"


def test_a_percentile_of_a_frame_held_in_memory(con):
    """data.percentile ranks the series as shown, passed as frame=."""
    assert _params(pctile.compute) == ["con", "series_id", "value", "since", "frame"]
    frame = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=30, freq="MS").date,
                          "value": [float(i) for i in range(30)]})
    r = pctile.compute(con, "_site_x", frame=frame)
    assert {"value", "value_date", "pctile", "n", "d0", "d1"} <= set(r)
    assert r["n"] == 30 and r["pctile"] == 100.0


def test_frequency_and_annual_rate_transforms():
    """data.frame puts daily series on months with to_frequency and reads its
    partial-month notes; yoy is the site's annual rate."""
    assert _params(transforms.to_frequency) == ["df", "native", "frequency", "how", "today"]
    assert _params(transforms.yoy) == ["df"]
    days = pd.bdate_range("2026-09-01", "2026-11-20")
    df, notes = transforms.to_frequency(pd.DataFrame({"date": days.date, "value": 1.0}),
                                        "daily", "monthly", how="mean", today=date(2026, 11, 23))
    assert list(df.columns) == ["date", "value"]
    assert notes["partial_last"] and {"n", "first", "last"} <= set(notes["partial"])
    months = pd.date_range("2024-01-01", periods=24, freq="MS")
    y = transforms.yoy(pd.DataFrame({"date": months, "value": [100.0] * 12 + [102.0] * 12}))
    assert list(y.columns) == ["date", "value"] and y["value"].iloc[-1] == pytest.approx(2.0)


def test_live_fetch_of_a_session_series():
    """The FRED-channel rows are fetched with live.fetch(con, series_id)."""
    assert _params(live.fetch) == ["con", "series_id"]


def test_the_seed_catalogs_and_the_logo_ship_in_the_package():
    """The site reads both catalogs from the installed package and copies the
    logo from bedrok.surfaces."""
    import csv
    seeds = resources.files("bedrok") / "seeds"
    for name in ("macro_catalog.csv", "bulk_catalog.csv"):
        with resources.as_file(seeds / name) as p, open(p) as f:
            header = next(csv.reader(f))
        assert {"series_id", "source", "name"} <= set(header), name
    with resources.as_file(resources.files("bedrok.surfaces") / "icon.png") as icon:
        assert icon.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
