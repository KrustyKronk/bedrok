"""The world layer: IMF, BIS and OECD families for every country they cover."""
import duckdb
import pandas as pd
import pytest

from bedrok.core import geography, licensing, store, units
from bedrok.core.aliases import ALIASES
from bedrok.ingest import pipeline, world
from bedrok.ingest.adapters import sdmx
from bedrok.ingest.adapters._util import UnpinnedKey

CSV = """DATAFLOW,REF_AREA,FREQ,MEASURE,TIME_PERIOD,OBS_VALUE
BIS:WS_CBPOL(1.0),KR,M,P,2026-08,2.5
BIS:WS_CBPOL(1.0),KR,M,P,2026-09,2.25
BIS:WS_CBPOL(1.0),BR,M,P,2026-09,15
BIS:WS_CBPOL(1.0),XM,M,P,2026-09,2
"""


def test_one_answer_splits_into_one_series_per_country():
    out = sdmx.parse_csv_by(CSV, "REF_AREA")
    assert set(out) == {"KR", "BR", "XM"}
    assert out["KR"]["value"].tolist() == [2.5, 2.25]


def test_two_definitions_for_one_country_still_refuse():
    """The one-series rule holds within each country: an open dimension other
    than the country is how the OECD once interleaved three CLI definitions."""
    bad = CSV + "BIS:WS_CBPOL(1.0),KR,M,Q,2026-09,9\n"
    with pytest.raises(UnpinnedKey, match="KR"):
        sdmx.parse_csv_by(bad, "REF_AREA")


def test_country_codes_map_to_bedrok_s_own():
    weo = next(s for s in world.WORLD_SETS if s["prefix"] == "weo_gdp_growth_")
    bis = next(s for s in world.WORLD_SETS if s["prefix"] == "bis_policy_rate_")
    assert world.to_code(weo, "GBR") == "uk" and world.to_code(weo, "GRC") == "el"
    assert world.to_code(weo, "G001") == "world" and world.to_code(weo, "G119") is None
    assert world.to_code(bis, "GB") == "uk" and world.to_code(bis, "XM") == "ea"
    assert world.to_code(bis, "5R") is None                  # a BIS regional aggregate


def test_the_seed_places_every_country_and_names_real_families():
    prefixes = set(world.PREFIXES)
    for spec in world.WORLD_SETS:
        for area in world.AREAS.get(spec["prefix"], []):
            cc = world.to_code(spec, area)
            assert cc, (spec["prefix"], area)
            assert cc == "world" or cc in geography.CODES or cc in geography.FAMILY_CODES
    assert set(world.AREAS) <= prefixes


def test_a_family_skips_what_bedrok_already_carries():
    """One measure per concept per country: no BIS inflation where HICP or a
    national CPI is held, no OECD 10-year where Eurostat's or a national yield
    is, no BIS policy rate for the euro area (the ECB's own rate)."""
    seed = set(pd.read_csv(pipeline.CATALOG_CSV, dtype=str)["series_id"])
    static = set(pipeline.static_catalog_rows()["series_id"])
    bulk = set(pipeline.bulk_catalog_rows()["series_id"])
    held = seed | bulk | {s for s in static if not s.startswith(world.PREFIXES)} | set(ALIASES)
    for spec in world.WORLD_SETS:
        for area in world.AREAS.get(spec["prefix"], []):
            cc = world.to_code(spec, area)
            assert cc not in spec.get("skip_codes", ()), (spec["prefix"], cc)
            for t in spec.get("skip_held", ()):
                assert t.format(cc=cc) not in held, (spec["prefix"], cc, t.format(cc=cc))


def test_no_world_id_is_an_alias_or_a_seed_row():
    """bis_reer_us answers as us_reer_bis and weo_gdp_growth_kz is a seed row
    with its own notes: neither is built by the world stage."""
    built = {sid for spec in world.WORLD_SETS for _, sid in pipeline.world_family_ids(spec)}
    assert not built & set(ALIASES)
    assert not built & pipeline.SEED_IDS
    assert "weo_gdp_growth_kz" in pipeline.SEED_IDS and "weo_gdp_growth_kz" not in built
    assert ALIASES["bis_reer_us"] == "us_reer_bis"


def test_world_rows_carry_their_licence_and_the_imf_clause():
    rows = pipeline.static_catalog_rows().set_index("series_id")
    assert rows.loc["weo_gdp_growth_br", "license"] == licensing.ATTRIBUTION
    assert rows.loc["weo_gdp_growth_br", "notes"].startswith(world.IMF_TERMS)
    assert "FORECASTS" in rows.loc["weo_gdp_growth_br", "notes"]
    assert rows.loc["weo_gdp_growth_br", "name"] == \
        "Brazil real GDP growth, IMF World Economic Outlook"
    assert rows.loc["bis_policy_rate_kr", "source_code"] == "WS_CBPOL/M.KR"


def test_the_per_series_fetch_leaves_world_ids_to_their_stage_but_keeps_seed_rows():
    ids = pd.Series(["weo_gdp_growth_br", "weo_gdp_growth_kz", "jp_policy_rate", "us_cpi"])
    assert pipeline.world_owned(ids).tolist() == [True, False, False, False]


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    store.init_db(c)
    return c


def _frame(dates, values):
    return pd.DataFrame({"date": [pd.Timestamp(d).date() for d in dates], "value": values})


def test_the_stage_stores_each_country_under_its_own_id(con, monkeypatch):
    spec = next(s for s in world.WORLD_SETS if s["prefix"] == "bis_policy_rate_")
    monkeypatch.setattr(world, "WORLD_SETS", [spec])
    monkeypatch.setattr(world, "AREAS", {"bis_policy_rate_": ["KR", "BR"]})
    monkeypatch.setenv("BEDROK_PACE_BIS", "0")
    calls = []

    def fake(row, code, by, start=None):
        calls.append((code, by, start))
        fresh = [pd.Timestamp.today().normalize() - pd.DateOffset(months=k) for k in (2, 1)]
        return {"KR": _frame(fresh, [2.5, 2.25]), "BR": _frame(fresh, [15.0, 15.0]),
                "JP": _frame(fresh, [0.5, 0.5])}           # JP: not in the seed

    monkeypatch.setattr(pipeline.sdmx, "fetch_by", fake)
    report = pipeline.sync_world(con)
    got = {r[0] for r in con.execute("SELECT DISTINCT series_id FROM macro_series").fetchall()}
    assert got == {"bis_policy_rate_kr", "bis_policy_rate_br"}
    assert ("BIS central bank policy rates", "ok", "2 countries") in report
    assert calls[0][:2] == ("WS_CBPOL/M.", "REF_AREA") and calls[0][2] is None

    # Both held: increments resume. A country new to the seed: the whole history.
    pipeline.sync_world(con, incremental=True)
    assert calls[-1][2] is not None
    monkeypatch.setattr(world, "AREAS", {"bis_policy_rate_": ["KR", "BR", "MX"]})
    pipeline.sync_world(con, incremental=True)
    assert calls[-1][2] is None


def test_the_weo_is_always_fetched_whole(con, monkeypatch):
    """Its rows run five years into the future, so "since the latest" would ask
    for nothing; each edition revises the past too."""
    spec = next(s for s in world.WORLD_SETS if s["prefix"] == "weo_gdp_growth_")
    monkeypatch.setattr(world, "WORLD_SETS", [spec])
    monkeypatch.setattr(world, "AREAS", {"weo_gdp_growth_": ["BRA"]})
    monkeypatch.setenv("BEDROK_PACE_IMF", "0")
    calls = []

    def fake(row, code, by, start=None):
        calls.append(start)
        return {"BRA": _frame(["2024-01-01", "2025-01-01", "2030-01-01"], [3.4, 2.1, 2.5])}

    monkeypatch.setattr(pipeline.sdmx, "fetch_by", fake)
    pipeline.sync_world(con)
    pipeline.sync_world(con, incremental=True)
    assert calls == [None, None]
    assert con.execute("SELECT max(date) FROM macro_series WHERE series_id='weo_gdp_growth_br'"
                       ).fetchone()[0] == pd.Timestamp("2030-01-01").date()


def test_an_exchange_rate_names_its_currency_as_the_publisher_gives_it(monkeypatch):
    """Rule 5: currency is a column, never an inference from the country. The
    BIS's CURRENCY dimension is read into the seed, and the catalog carries it."""
    body = ("DATAFLOW,FREQ,REF_AREA,CURRENCY,COLLECTION,TIME_PERIOD,OBS_VALUE\n"
            "BIS:WS_XRU(1.0),M,BR,BRL,A,2026-09,5.4\nBIS:WS_XRU(1.0),M,KR,KRW,A,2026-09,1390\n")
    monkeypatch.setattr(sdmx, "_csv", lambda spec, code, start: body)
    assert sdmx.dimension_by({}, "WS_XRU/M...A", "REF_AREA", "CURRENCY") == {"BR": "BRL", "KR": "KRW"}
    rows = pipeline.static_catalog_rows().set_index("series_id")
    assert rows.loc["bis_usd_rate_br", "units"] == "BRL per USD"
    usd = rows[rows.index.str.startswith("bis_usd_rate_")]
    assert len(usd) > 100 and usd["units"].map(units.valid).all()
    assert not usd["units"].isin(["USD per USD", "EUR per USD", "GBP per USD", "KZT per USD"]).any()
    assert "bis_usd_rate_de" not in rows.index and "bis_usd_rate_us" not in rows.index


def test_the_headline_unemployment_family_answers_through_the_older_rows():
    rows = pipeline.static_catalog_rows().set_index("series_id")
    assert rows.loc["unemp_rate_pl", "units"] == "percent"
    assert ALIASES["unemp_rate_de"] == "de_unemployment"
    assert "unemp_rate_de" not in rows.index
    oecd = {sid for _, sid in pipeline.world_family_ids(
        next(s for s in world.WORLD_SETS if s["prefix"] == "oecd_unemployment_"))}
    assert "oecd_unemployment_pl" not in oecd          # Eurostat carries Poland


def test_a_sync_refreshes_the_family_s_catalog_rows(con, monkeypatch):
    """A changed allowance or unit must reach a store that already has the row."""
    spec = next(s for s in world.WORLD_SETS if s["prefix"] == "bis_policy_rate_")
    monkeypatch.setattr(world, "WORLD_SETS", [spec])
    monkeypatch.setattr(world, "AREAS", {"bis_policy_rate_": ["KR"]})
    monkeypatch.setenv("BEDROK_PACE_BIS", "0")
    store.upsert_macro_catalog(con, pd.DataFrame([{**pipeline._world_row(spec, "KR", "bis_policy_rate_kr", "South Korea"),
                                                    "staleness_days": 1}]))
    fresh = [pd.Timestamp.today().normalize() - pd.DateOffset(months=1)]
    monkeypatch.setattr(pipeline.sdmx, "fetch_by", lambda *a, **k: {"KR": _frame(fresh, [2.5])})
    pipeline.sync_world(con)
    assert con.execute("SELECT staleness_days FROM macro_catalog WHERE series_id='bis_policy_rate_kr'"
                       ).fetchone()[0] == spec["staleness_days"]
