"""engines/rates_infl (`bedrok rates`), on an in-memory store of inline rows.

Item 12 of STATE.md: until 2026-11-23 nothing ran this module, and it took a
year-on-year rate as "twelve rows up", which is wrong for any series with a
missing month. US CPI has one: October 2025 was never published.
"""
import sys

import duckdb
import pandas as pd
import pytest

from bedrok.core import config, store
from bedrok.engines import rates_infl

MONTHS = pd.date_range("2021-01-01", "2026-09-01", freq="MS")


def _put(con, sid, dates, values):
    store.upsert_macro_series(con, sid, pd.DataFrame({"date": dates.date, "value": values}))


@pytest.fixture
def con(monkeypatch):
    c = duckdb.connect(":memory:")
    store.init_db(c)
    n = len(MONTHS)
    # Index levels growing 2% a year, so every annual rate is 2.00%; US CPI
    # lacks October 2025 as the real one does.
    level = [100 * 1.02 ** (i / 12) for i in range(n)]
    cpi = pd.Series(level, index=MONTHS).drop(pd.Timestamp("2025-10-01"))
    _put(c, "us_cpi", cpi.index, cpi.values)
    for sid in ("us_core_cpi", "us_core_pce"):
        _put(c, sid, MONTHS, level)
    _put(c, "ea_hicp_yoy", MONTHS, [2.5] * n)
    for sid, v in (("us_fed_funds", 4.0), ("ecb_deposit_rate", 2.0), ("us_3m", 3.9),
                   ("ecb_estr", 1.9), ("us_10y", 4.2), ("ea_10y_yield", 2.7)):
        _put(c, sid, MONTHS, [v + 0.01 * i for i in range(n)])
    days = pd.date_range("2021-01-01", "2026-09-30", freq="B")
    store.upsert_fx(c, "EURUSD", pd.DataFrame({"date": days.date,
                                              "value": [1.1 + 0.0001 * i for i in range(len(days))]}),
                    "ecb")
    monkeypatch.setattr(config, "connect", lambda *a, **k: c)
    monkeypatch.setattr(config, "BASE_CCY", "EUR")
    monkeypatch.setattr(sys, "argv", ["bedrok rates"])
    return c


def test_annual_rates_are_by_date_across_a_missing_month(con, capsys):
    assert rates_infl.main() == 0
    out = capsys.readouterr().out
    # Twelve rows up from September 2026 is August 2025 once October is gone:
    # 13 months of 2% growth, which printed +2.17%.
    assert "US CPI       +2.00%  (2026-09-01)" in out
    assert "US core CPI  +2.00%" in out
    assert "EA HICP      +2.50%" in out


def test_differentials_carry_and_eras_are_printed_and_written(con, capsys, tmp_path, monkeypatch):
    panel = tmp_path / "panel.csv"
    monkeypatch.setattr(sys, "argv", ["bedrok rates", "--csv", str(panel)])
    assert rates_infl.main() == 0
    out = capsys.readouterr().out
    # 4.00 vs 2.00 rising together: the gap stays 2 points; 10y 4.2 vs 2.7.
    assert "policy +2.00pts" in out and "carry (3m−€STR, ≈ hedge cost) +2.00pts" in out
    assert "10y +1.50pts" in out
    # Eras with fewer than twelve months are skipped, the rest say their size.
    assert "since 2022" in out and "since 2024" in out and "since 2010" in out
    df = pd.read_csv(panel)
    assert list(df.columns) == ["date", "policy_diff", "carry", "y10_diff", "eurusd"]
    assert len(df) == len(MONTHS)


def test_it_refuses_a_non_euro_base_rather_than_answer_for_the_wrong_investor(con, monkeypatch):
    monkeypatch.setattr(config, "BASE_CCY", "USD")
    with pytest.raises(SystemExit, match="base.currency = 'EUR'"):
        rates_infl.main()
