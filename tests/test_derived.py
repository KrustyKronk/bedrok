"""Derived series: arithmetic, idempotency, and honest behaviour when inputs lag."""
import duckdb
import pandas as pd
import pytest

from bedrok.core import licensing, store
from bedrok.ingest import derived, pipeline


CYCLES = [{"peak": "", "trough": "1854-12-01"},
          {"peak": "2020-02-01", "trough": "2020-04-01"}]


@pytest.fixture(autouse=True)
def _no_nber_network(monkeypatch):
    monkeypatch.setattr(derived.nber, "fetch_cycles", lambda: CYCLES)


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    store.init_db(c)
    return c


def _seed(con, series_id, pairs):
    store.upsert_macro_series(con, series_id, pd.DataFrame(
        [{"date": pd.Timestamp(d).date(), "value": v} for d, v in pairs]))


def _value(con, series_id, date):
    return con.execute("SELECT value FROM macro_series WHERE series_id=? AND date=?",
                       [series_id, date]).fetchone()[0]


def test_spread_is_the_difference_on_shared_dates(con):
    _seed(con, "ltgov_yield_it", [("2026-05-01", 3.80), ("2026-06-01", 3.73)])
    _seed(con, "ltgov_yield_de", [("2026-05-01", 3.00), ("2026-06-01", 2.96)])
    derived.sync_derived(con)
    assert _value(con, "it_de_10y_spread", pd.Timestamp("2026-06-01").date()) == pytest.approx(0.77)


def test_spread_ignores_dates_only_one_side_has(con):
    _seed(con, "ltgov_yield_it", [("2026-05-01", 3.8), ("2026-06-01", 3.7)])
    _seed(con, "ltgov_yield_de", [("2026-06-01", 3.0)])
    derived.sync_derived(con)
    rows = con.execute("SELECT count(*) FROM macro_series WHERE series_id='it_de_10y_spread'").fetchone()[0]
    assert rows == 1


def test_three_month_annualised_matches_the_stated_formula(con):
    # a steady +1%/month on the index → (1.01^3)^4 - 1 ≈ 12.7% annualised
    idx = [("2026-01-01", 100.0), ("2026-02-01", 101.0), ("2026-03-01", 102.01),
           ("2026-04-01", 103.0301)]
    _seed(con, "us_cpi", idx)
    derived.sync_derived(con)
    got = _value(con, "us_cpi_3m_annualized", pd.Timestamp("2026-04-01").date())
    assert got == pytest.approx(((1.01 ** 3) ** 4 - 1) * 100, rel=1e-9)
    # the first three months have no t-3 comparison and must not be invented
    assert con.execute(
        "SELECT count(*) FROM macro_series WHERE series_id='us_cpi_3m_annualized'"
    ).fetchone()[0] == 1


def test_missing_input_skips_rather_than_failing(con):
    """An upstream stage that has not run yet is a sequencing fact, not a fault —
    a FAIL here would turn a fresh store into a red CI run."""
    _seed(con, "ltgov_yield_it", [("2026-06-01", 3.7)])   # no ltgov_yield_de
    report = derived.sync_derived(con)
    statuses = {sid: (status, detail) for sid, status, detail in report}
    assert statuses["it_de_10y_spread"][0] == "skip"
    assert "awaiting input" in statuses["it_de_10y_spread"][1]
    assert "ltgov_yield_de" in statuses["it_de_10y_spread"][1]
    assert not any(status == "FAIL" for _, status, _ in report)


def test_recomputation_is_idempotent_and_tracks_revised_inputs(con):
    _seed(con, "ltgov_yield_it", [("2026-06-01", 3.73)])
    _seed(con, "ltgov_yield_de", [("2026-06-01", 2.96)])
    derived.sync_derived(con)
    derived.sync_derived(con)
    assert con.execute(
        "SELECT count(*) FROM macro_series WHERE series_id='it_de_10y_spread'"
    ).fetchone()[0] == 1

    # a revised input must flow through, which is why this recomputes in full
    _seed(con, "ltgov_yield_de", [("2026-06-01", 2.50)])
    derived.sync_derived(con)
    assert _value(con, "it_de_10y_spread", pd.Timestamp("2026-06-01").date()) == pytest.approx(1.23)


def test_catalog_row_declares_its_inputs_and_licence(con):
    _seed(con, "ltgov_yield_it", [("2026-06-01", 3.73)])
    _seed(con, "ltgov_yield_de", [("2026-06-01", 2.96)])
    derived.sync_derived(con)
    source, code, notes, lic = con.execute(
        "SELECT source, source_code, notes, license FROM macro_catalog "
        "WHERE series_id='it_de_10y_spread'").fetchone()
    assert source == "derived"
    assert code == "diff:ltgov_yield_it-ltgov_yield_de"
    # a reader must never mistake a computed series for a published statistic
    assert notes.startswith("COMPUTED BY BEDROK from")
    assert lic == licensing.ATTRIBUTION
    assert licensing.unreviewed_series(con) == []


def test_derived_rows_are_excluded_from_the_per_series_fetch_loop():
    from bedrok.ingest import pipeline
    assert "derived" in pipeline.BULK_SOURCES


def test_five_year_five_year_forward_uses_freds_formula(con):
    _seed(con, "us_breakeven_10y", [("2026-10-01", 2.40)])
    _seed(con, "us_breakeven_5y", [("2026-10-01", 2.20)])
    derived.sync_derived(con)
    want = (((1.024 ** 10) / (1.022 ** 5)) ** 0.2 - 1) * 100
    assert _value(con, "us_5y5y_fwd_inflation", "2026-10-01") == pytest.approx(want)


def test_recession_flag_runs_from_the_month_after_the_peak_through_the_trough():
    from bedrok.ingest.adapters import nber
    df = nber.recession_months(CYCLES, pd.Timestamp("2020-06-01").date())
    flags = dict(zip([str(d) for d in df["date"]], df["value"]))
    assert flags["1854-12-01"] == 1
    assert flags["2020-02-01"] == 0
    assert flags["2020-03-01"] == flags["2020-04-01"] == 1
    assert flags["2020-05-01"] == 0


def test_a_derived_series_replaces_what_its_id_held_before(con):
    """us_10y2y came from FRED until session 3b; deriving it must not leave
    FRED's dates behind where the inputs have none."""
    _seed(con, "us_10y2y", [("1970-01-01", 9.9), ("2026-10-01", 9.9)])
    _seed(con, "us_10y", [("2026-10-01", 4.5)])
    _seed(con, "us_2y", [("2026-10-01", 4.0)])
    derived.sync_derived(con)
    rows = con.execute("SELECT date, value FROM macro_series WHERE series_id = "
                       "'us_10y2y'").fetchall()
    assert [(str(d), v) for d, v in rows] == [("2026-10-01", 0.5)]


def test_every_derived_input_is_a_stored_series():
    """A derived row is stored, so its inputs must be too — otherwise a
    session-only series (FRED content) reaches the store through arithmetic."""
    seed = pd.read_csv(pipeline.CATALOG_CSV, dtype=str).set_index("series_id")
    derived_ids = {d["series_id"] for d in derived.DERIVED}
    for spec in derived.DERIVED:
        for sid in spec["inputs"]:
            if sid in derived_ids or sid not in seed.index:
                continue  # derived, or registered by a bulk stage: always stored
            assert seed.loc[sid, "cache"] == "store", \
                f"{spec['series_id']}: input {sid} is session-only"


def test_no_derived_id_is_also_a_seed_row():
    """One owner per id: a seed row and a derived spec with the same id would
    flip the catalog's source on every refresh."""
    seed = set(pd.read_csv(pipeline.CATALOG_CSV, dtype=str)["series_id"])
    assert not seed & {d["series_id"] for d in derived.DERIVED}


def test_stored_values_carry_no_binary_noise(con):
    _seed(con, "us_10y", [("2026-10-08", 5.22)])
    _seed(con, "us_10y_real", [("2026-10-08", 2.87)])
    derived.sync_derived(con)
    assert _value(con, "us_breakeven_10y", "2026-10-08") == 2.35


def test_net_trade_is_exports_plus_imports_because_imports_come_negative(con):
    """Session C: Eurostat publishes the imports contribution already negative,
    so net trade is a sum. Spain 2025, as published: exports 0.96, imports -1.80."""
    _seed(con, "gdp_contrib_exp_es", [("2025-01-01", 0.96)])
    _seed(con, "gdp_contrib_imp_es", [("2025-01-01", -1.80)])
    derived.sync_derived(con)
    assert _value(con, "gdp_contrib_net_es", pd.Timestamp("2025-01-01").date()) == pytest.approx(-0.84)


def test_net_trade_covers_exactly_the_geographies_its_parts_are_built_for():
    """derived cannot import pipeline (a cycle), so the geography lists are
    kept by hand and pinned here against the families."""
    def geos(prefix):
        spec = next(s for s in pipeline.EUROSTAT_BULK_SETS if s["prefix"] == prefix)
        return [g.lower() for g in pipeline.family_geos(spec)]
    assert derived.GDP_NET_GEOS == geos("gdp_contrib_exp_") == geos("gdp_contrib_imp_")
    assert derived.GDP_NET_GEOS_Q == geos("gdp_contrib_q_exp_") == geos("gdp_contrib_q_imp_")
