"""Profile loading: defaults, toml overrides, fx-pair derivation."""
from pathlib import Path

from bedrok.core import config


def test_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("BEDROK_DB", raising=False)
    monkeypatch.delenv("FDP_DB", raising=False)
    config.set_profile(tmp_path)  # no profile.toml → pure defaults
    assert config.BASE_CCY == "EUR"
    assert set(config.PROFILE) == {"owner", "base", "macro"}
    assert config.DB_PATH == tmp_path / "data" / "market.duckdb"
    # The store carries the fetchable pairs plus their reciprocals, so GBP- and
    # USD-base profiles resolve f"{BASE_CCY}{ccy}" too.
    pairs = [p[0] for p in config.fx_pairs()]
    assert pairs[:3] == ["EURUSD", "EURGBP", "GBPUSD"]
    assert set(pairs) == {"EURUSD", "EURGBP", "GBPUSD",
                          "USDEUR", "GBPEUR", "USDGBP"}


def test_toml_overrides(tmp_path: Path):
    (tmp_path / "profile.toml").write_text(
        '[owner]\nname = "Testy"\n'
        '[base]\ncurrency = "USD"\nsymbol = "$"\n'
        '[macro]\ncommodities = false\n'
    )
    config.set_profile(tmp_path)
    assert config.PROFILE["owner"]["name"] == "Testy"
    assert config.BASE_CCY == "USD" and config.BASE_SYMBOL == "$"
    pairs = dict((p[0], p[1]) for p in config.fx_pairs())
    assert pairs["USDEUR"] == "derived"
    assert config.PROFILE["macro"]["commodities"] is False
    # unspecified keys keep defaults
    assert config.PROFILE["macro"]["hicp_europe"] is True


def test_legacy_portfolio_sections_still_load(tmp_path):
    """A profile.toml written for the portfolio era carries [broker], [sleeves]
    and [strategy]. Nothing reads them any more, but loading must not fail —
    _merge passes unknown sections through untouched."""
    (tmp_path / "profile.toml").write_text(
        '[broker]\nname = "examplebroker"\nexport_dir = "~/Desktop/exports"\n'
        '[sleeves]\ncore = "Core"\n'
        '[strategy]\nbenchmark = "SPY"\n')
    config.set_profile(tmp_path)
    assert config.BASE_CCY == "EUR"
    assert config.PROFILE["broker"]["name"] == "examplebroker"


def test_a_leftover_market_block_is_ignored(tmp_path, monkeypatch):
    """A profile from the days of the pack still says subscriber mode and names a
    pack URL. There is one store now: the block loads and changes nothing."""
    monkeypatch.delenv("BEDROK_DB", raising=False)
    monkeypatch.delenv("FDP_DB", raising=False)
    (tmp_path / "profile.toml").write_text(
        '[market]\nmode = "subscriber"\npack_url = "https://example.invalid/v1"\n')
    config.set_profile(tmp_path)
    assert config.DB_PATH == tmp_path / "data" / "market.duckdb"
    assert not hasattr(config, "MARKET_MODE")


def test_fx_pairs_ship_full_cross_set_for_eur_usd_gbp():
    orig = config.BASE_CCY
    try:
        for ccy in ("EUR", "USD", "GBP"):
            config.BASE_CCY = ccy
            pairs = {p for p, _, _ in config.fx_pairs()}
            # every base needs its own outbound crosses available in the store
            assert {"EURUSD", "EURGBP", "GBPUSD", "USDEUR", "GBPEUR", "USDGBP"} <= pairs
    finally:
        config.BASE_CCY = orig


def teardown_module():
    config.set_profile()  # restore env-based resolution for other tests


def test_sandbox_copy_recipe_names_the_real_path(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", Path("/mnt/x/data/market.duckdb"))
    txt = config.sandbox_copy_recipe()
    assert "/mnt/x/data/market.duckdb" in txt
    assert "export BEDROK_DB=/tmp/bedrok/data/market.duckdb" in txt
    assert "DIES WITH THE SESSION" in txt
    assert "BEDROK_PERSONAL_DB" not in txt


def test_stale_wal_open_explains_itself(tmp_path, monkeypatch):
    """The un-unlinkable .wal must arrive as instructions, not a traceback.

    DuckDB removes the .wal on open; the Cowork mount allows write and truncate
    but not unlink, so a leftover log makes every subsequent open fail.
    """
    import duckdb
    import pytest

    def boom(*_a, **_k):
        raise duckdb.IOException(
            'IO Error: Could not remove file "/mnt/profile/data/market.duckdb.wal": '
            'Operation not permitted')

    monkeypatch.setattr(duckdb, "connect", boom)
    with pytest.raises(SystemExit) as ei:
        config._open(tmp_path / "market.duckdb", False)

    msg = str(ei.value)
    assert ": >" in msg              # the truncate workaround
    assert "BEDROK_DB" in msg        # the copy-store fallback


def test_unrelated_io_errors_still_propagate(tmp_path, monkeypatch):
    """Only the unlink case is reinterpreted — everything else stays itself."""
    import duckdb
    import pytest

    def boom(*_a, **_k):
        raise duckdb.IOException("IO Error: disk is on fire")

    monkeypatch.setattr(duckdb, "connect", boom)
    with pytest.raises(duckdb.IOException):
        config._open(tmp_path / "market.duckdb", False)
