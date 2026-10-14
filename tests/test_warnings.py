"""The warnings seed (bedrok/seeds/warnings.csv) and how it is matched."""
import csv
from datetime import date

import pandas as pd
import pytest

from bedrok.core import warnings
from bedrok.ingest import pipeline


def _row(**kw):
    base = {c: "" for c in warnings.COLUMNS}
    base.update(warning_id="x", country="Testland", severity="material",
                period_start="2025-10", period_end="2025-10", series="us_cpi")
    base.update(kw)
    base["series_ids"] = tuple(base["series"].split())
    return base


@pytest.fixture
def fake(monkeypatch):
    rows = (
        _row(warning_id="gap", series="us_cpi us_unemployment"),
        _row(warning_id="note", severity="caveat", period_start="2025-08", period_end=""),
        _row(warning_id="ar_cpi", country="Argentina", concept="cpi inflation consumer prices",
             period_start="2007", period_end="2015", series=""),
        _row(warning_id="us_cps", country="United States", concept="household survey unemployment",
             series=""),
    )
    monkeypatch.setattr(warnings, "load", lambda: rows)
    return rows


# ---- the seed itself ---------------------------------------------------------

def test_the_seed_has_the_agreed_columns_and_every_row_is_complete():
    with open(warnings.WARNINGS_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == warnings.COLUMNS
        rows = list(reader)
    assert len(rows) >= 40
    ids = [r["warning_id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate warning_id"
    for r in rows:
        for col in ("warning_id", "country", "severity", "concept", "official_series",
                    "what_happened", "effect_on_data", "cross_check",
                    "source_name"):
            assert r[col].strip(), f"{r['warning_id']}: empty {col}"
        assert r["severity"] in warnings.SEVERITIES, r["warning_id"]
        assert r["source_url"].startswith("https://"), f"{r['warning_id']}: source_url"
        w0, w1 = warnings.period_bounds(r)
        assert w1 is None or w1 >= w0, r["warning_id"]


def test_every_linked_series_exists():
    known = set(pd.read_csv(pipeline.CATALOG_CSV, dtype=str)["series_id"])
    known |= set(pipeline.static_catalog_rows()["series_id"])
    # Bulk families name their ids only when they download (hicp_yoy_tr).
    bulk = tuple(pipeline.BULK_STAGE_FOR_PREFIX)
    for r in warnings.load():
        missing = {s for s in r["series_ids"] if s not in known and not s.startswith(bulk)}
        assert not missing, f"{r['warning_id']}: unknown series {missing}"


def test_a_warning_reaches_every_series_measured_the_same_way():
    """The 2026-10-12 self-test: a session that picked the unadjusted CPI or the
    participation rate got the October 2025 hole without its reason."""
    def ids(sid):
        return {w["warning_id"] for w in warnings.for_series(sid)}
    for sid in ("us_cpi", "us_cpi_nsa", "us_cpi_3m_annualized"):
        assert "us_cpi_oct2025_gap" in ids(sid), sid
    for sid in ("us_unemployment", "us_participation", "us_u6"):
        assert "us_cps_oct2025_gap" in ids(sid), sid
    assert "tr_cpi_item_prices_2022" in ids("hicp_yoy_tr")


# ---- matching ----------------------------------------------------------------

def test_periods_cover_whole_months_and_years():
    assert warnings.period_bounds(_row(period_start="2007", period_end="2015")) == \
        (date(2007, 1, 1), date(2015, 12, 31))
    assert warnings.period_bounds(_row(period_start="2025-10", period_end="2025-10")) == \
        (date(2025, 10, 1), date(2025, 10, 31))
    assert warnings.period_bounds(_row(period_end=""))[1] is None
    with pytest.raises(ValueError):
        warnings.period_bounds(_row(period_start="Oct 2025"))


def test_a_loud_warning_needs_its_period_inside_the_window(fake):
    inside = warnings.for_series("us_cpi", date(2025, 9, 1), date(2025, 12, 1), loud_only=True)
    assert [w["warning_id"] for w in inside] == ["gap"]
    after = warnings.for_series("us_cpi", date(2026, 1, 1), date(2026, 8, 1), loud_only=True)
    assert after == []


def test_a_caveat_is_never_loud_but_is_always_described(fake):
    assert "note" not in [w["warning_id"] for w in
                          warnings.for_series("us_cpi", date(2026, 1, 1), None, loud_only=True)]
    assert {w["warning_id"] for w in warnings.for_series("us_cpi")} == {"gap", "note"}


def test_search_needs_a_place_and_knows_its_short_names(fake):
    assert [w["warning_id"] for w in warnings.search("argentina inflation")] == ["ar_cpi"]
    assert warnings.search("inflation") == []
    assert [w["warning_id"] for w in warnings.search("US unemployment")] == ["us_cps"]
    assert warnings.search("in it no") == []        # ISO codes are words first


def test_a_place_is_a_name_never_the_words_describing_the_case():
    """The 2026-10-12 self-test: "trade balance" matched "Caucasus and Central
    Asia (mirror trade)" and "government debt" matched "Global (debt statistics
    vintages)", and search then dropped every series. The real seed is used."""
    for text in ("trade balance", "government debt", "central bank balance sheet",
                 "global supply chain", "cross country", "producers prices", "yields from 2020"):
        assert warnings.search(text) == [], text
    assert "cca_mirror_trade_russia_sanctions" in \
        [w["warning_id"] for w in warnings.search("central asia trade")]
    assert "za_census_2022_undercount" in [w["warning_id"] for w in warnings.search("south africa census")]
    assert "opec_crude_direct_communication" in [w["warning_id"] for w in warnings.search("opec output")]
    assert "tr_cpi_item_prices_2022" in [w["warning_id"] for w in warnings.search("turkey inflation")]


def test_a_place_bedrok_covers_also_needs_the_words_to_be_about_the_warning():
    """The D2 self-test: every "us …" search carried the five BLS warnings,
    a Treasury yield included. The real seed is used."""
    def ids(text):
        return {w["warning_id"] for w in warnings.search(text)}
    assert ids("us 10 year yield") == set()
    assert ids("us") == set()
    assert ids("us cpi") == {"us_cpi_oct2025_gap", "us_cpi_sample_cuts_2025"}
    assert {"us_cps_oct2025_gap", "us_cps_response_rates"} <= ids("us unemployment rate")
    # where Bedrok carries nothing, the place alone still answers
    assert "ar_indec_cpi_2007" in ids("argentina")
    assert "ar_indec_cpi_2007" in ids("argentina inflation")
