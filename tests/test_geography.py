"""Which place a series is about, and which words name one (session 11)."""
import duckdb
import pandas as pd

from bedrok.core import geography, store
from bedrok.core.aliases import ALIASES, canonical
from bedrok.ingest import pipeline


def test_a_series_place_comes_from_its_first_or_last_token():
    assert geography.of_series("es_hicp_yoy") == "es"
    assert geography.of_series("hicp_core_es") == "es"
    assert geography.of_series("gdp_qoq_ea20") == "ea"
    assert geography.of_series("ltgov_yield_eu27_2020") == "eu"
    assert geography.of_series("oecd_cli_deu") == "de"
    assert geography.of_series("weo_gdp_growth_us") == "us"


def test_family_only_countries_and_the_ecb_have_a_place():
    """The 2026-10-12 self-test: a stored Turkish HICP was unreachable by
    "turkey", and "euro area deposit rate" filtered the ECB's rates out."""
    assert geography.of_series("hicp_yoy_tr") == "tr"
    assert geography.of_series("hicp_yoy_pl") == "pl"
    assert geography.of_series("hicp_yoy_ea19") == "ea"
    assert geography.of_series("hicp_yoy_eea") is None
    assert geography.of_series("ecb_deposit_rate", "ecb") == "ea"
    assert geography.in_text("turkey inflation") == ({"tr"}, ["inflation"])
    assert geography.in_text("is me")[0] == set()       # codes that are words stay words


def test_commodities_and_positioning_have_no_place():
    assert geography.of_series("wb_banana_us", "worldbank") is None
    assert geography.of_series("cot_es_net_noncomm", "cftc") is None   # es is the E-mini S&P


def test_place_words_are_filters_and_pronouns_are_not_places():
    assert geography.in_text("spain inflation") == ({"es"}, ["inflation"])
    assert geography.in_text("euro area core") == ({"ea"}, ["core"])
    assert geography.in_text("is it no good")[0] == set()


def test_every_alias_points_at_a_seed_row_and_is_never_built():
    seed = set(pd.read_csv(pipeline.CATALOG_CSV, dtype=str)["series_id"])
    built = set(pipeline.static_catalog_rows()["series_id"])
    for alias, target in ALIASES.items():
        assert target in seed and alias not in seed and alias not in built
        assert canonical(alias) == target and canonical(target) == target


def test_no_family_registers_a_euro_area_code_its_dataset_does_not_fill():
    for spec in pipeline.EUROSTAT_BULK_SETS:
        ea = [g for g in pipeline.family_geos(spec) if g.startswith("EA")]
        assert ea, spec["prefix"]
        assert set(ea) <= {"EA", "EA20", "EA21"}


def test_load_catalog_prunes_aliases_and_empty_family_rows_but_keeps_unfilled_ones():
    con = duckdb.connect(":memory:")
    store.init_db(con)
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "esi_ea", "source": "eurostat", "source_code": "x", "name": "a"},
        {"series_id": "hicp_yoy_es", "source": "eurostat", "source_code": "x", "name": "b"}]))
    store.upsert_macro_series(con, "hicp_yoy_es",
                              pd.DataFrame({"date": [pd.Timestamp("2026-01-01").date()], "value": [1.0]}))
    pipeline.load_catalog(con)
    ids = {r[0] for r in con.execute("SELECT series_id FROM macro_catalog").fetchall()}
    assert "esi_ea" not in ids and "hicp_yoy_es" not in ids      # neither produced any more
    assert "esi_ea20" in ids                                      # produced, not yet filled: stays
    assert con.execute("SELECT count(*) FROM macro_series WHERE series_id='hicp_yoy_es'").fetchone()[0] == 0
