"""Licence vocabulary, catalog introspection, and the mandated attribution texts."""
import duckdb
import pandas as pd
import pytest

from bedrok.core import licensing, store


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    store.init_db(c)
    return c


def _catalog(con, rows):
    store.upsert_macro_catalog(con, pd.DataFrame(rows))


def test_vocabulary_is_closed():
    assert licensing.LICENSE_VALUES == {
        "public_domain", "attribution", "noncommercial", "attribution_fred_channel",
        "personal_use"}
    assert licensing.CACHE_VALUES == {"store", "session"}


def test_restricted_and_unreviewed_detection(con):
    _catalog(con, [
        {"series_id": "ok_one", "source": "eia", "source_code": "X", "name": "n",
         "license": "public_domain"},
        {"series_id": "banned", "source": "fred", "source_code": "Y", "name": "n",
         "license": "personal_use"},
        {"series_id": "forgot", "source": "fred", "source_code": "Z", "name": "n"},
        {"series_id": "typo", "source": "fred", "source_code": "W", "name": "n",
         "license": "public-domain"},
    ])
    assert licensing.restricted_series(con) == ["banned"]
    assert dict(licensing.unreviewed_series(con)) == {"forgot": None, "typo": "public-domain"}


def test_source_defaults_fill_nulls_without_overriding_or_guessing(con):
    _catalog(con, [
        # a bulk-registered row from before the licence column existed
        {"series_id": "hicp_yoy_de", "source": "eurostat", "source_code": "X", "name": "n"},
        # an explicit licence must survive the backfill — this is the whole point
        {"series_id": "us_baa10y", "source": "fred", "source_code": "BAA10Y", "name": "n",
         "license": "personal_use"},
        # an unknown source stays NULL so it still trips LICENSE-UNREVIEWED
        {"series_id": "mystery", "source": "somewhere_new", "source_code": "Q", "name": "n"},
    ])
    assert licensing.backfill_source_defaults(con) == 1

    got = dict(con.execute("SELECT series_id, license FROM macro_catalog").fetchall())
    assert got["hicp_yoy_de"] == licensing.ATTRIBUTION
    assert got["us_baa10y"] == licensing.RESTRICTED      # never downgraded
    assert got["mystery"] is None
    assert [s for s, _ in licensing.unreviewed_series(con)] == ["mystery"]

    # idempotent — a second pass has nothing left to do
    assert licensing.backfill_source_defaults(con) == 0


def test_attributions_carry_the_mandated_verbatim_notices():
    """These texts are kept for the licence notices session 6 generates; the
    publishers that mandate exact wording must get it verbatim."""
    fred = licensing.ATTRIBUTIONS["fred"]
    assert "not endorsed or certified by the Federal Reserve Bank of St. Louis" in fred
    # the Bank of England mandates this exact SONIA sentence
    assert licensing.SERIES_ATTRIBUTIONS["uk_sonia"] == (
        "SONIA and/or SONIA Compounded Index data licensed under the Open "
        "Government Licence v3.0 and copyright the Governor and Company of "
        "the Bank of England.")
    assert licensing.SERIES_ATTRIBUTIONS["us_vix"].startswith("Source: Cboe via FRED")
    assert ("Contains public sector information licensed under the Open Government"
            in licensing.ATTRIBUTIONS["ons"])
    # every source with a default class has an attribution line to quote
    assert set(licensing.SOURCE_DEFAULT_LICENSE) <= set(licensing.ATTRIBUTIONS)
