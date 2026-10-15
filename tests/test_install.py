"""An installed Bedrok has no repo around it: the seeds travel inside the
package, there is always a home folder, and blank bundle fields mean unset."""
from pathlib import Path

import pytest

from bedrok.core import config, sources
from bedrok.ingest import pipeline
from bedrok.ops import docs

REPO = Path(__file__).resolve().parents[1]


def test_the_seeds_live_inside_the_package():
    pkg = Path(config.__file__).resolve().parents[1]          # .../bedrok
    assert config.SEEDS_DIR == pkg / "seeds"
    assert pipeline.CATALOG_CSV.is_file() and sources.SOURCES_CSV.is_file()
    assert sources.SOURCES_CSV.parent == pipeline.CATALOG_CSV.parent == config.SEEDS_DIR


def test_pyproject_ships_the_seeds_as_package_data():
    text = (REPO / "pyproject.toml").read_text()
    # The csv seeds, and gaps.md, which the server serves as bedrok://gaps.
    assert 'bedrok = ["seeds/*.csv", "seeds/*.md"]' in text


@pytest.mark.parametrize("value, unset", [
    ("", True), ("   ", True), (None, True), ("${user_config.data_dir}", True),
    ("/Users/x/Bedrok/profile", False), ("abc123", False)])
def test_blank_and_placeholder_values_are_unset(value, unset):
    assert config.unset(value) is unset


def test_home_falls_back_to_the_per_user_folder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)                                 # no ./profile here
    monkeypatch.setenv("BEDROK_PROFILE", "${user_config.data_dir}")
    monkeypatch.delenv("FDP_ROOT", raising=False)
    assert config._resolve_default_dir() == config.default_home()
    assert "bedrok" in str(config.default_home())


def test_an_explicit_home_wins(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BEDROK_PROFILE", str(tmp_path / "mine"))
    assert config._resolve_default_dir() == tmp_path / "mine"


def test_a_placeholder_key_is_no_key(monkeypatch):
    monkeypatch.setenv("BEA_API_KEY", "${user_config.bea_api_key}")
    assert sources.key("bea") == ""
    monkeypatch.setenv("BEA_API_KEY", " real-key ")
    assert sources.key("bea") == "real-key"


def test_every_keyed_source_says_where_to_get_its_key():
    for s, r in sources.load().items():
        assert bool(r["key_env"]) == r["key_url"].startswith("https://"), s


@pytest.mark.parametrize("name", sorted(docs.PAGES))
def test_the_generated_pages_are_up_to_date(name):
    """Run `bedrok docs` after changing a seed."""
    assert (REPO / "docs" / name).read_text(encoding="utf-8") == docs.PAGES[name]()


def test_the_bundle_manifest_matches_the_package():
    import json
    import tomllib
    manifest = json.loads((REPO / "packaging" / "mcpb" / "manifest.json").read_text())
    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    assert manifest["version"] == version
    env = manifest["server"]["mcp_config"]["env"]
    keyed = {r["key_env"] for r in sources.load().values() if r["key_env"]}
    assert keyed <= set(env), "every API key needs a field in the bundle"


def test_the_bundle_lists_every_tool_the_server_has():
    """Claude Desktop shows the manifest's list before the server starts; a
    tool missing from it is a tool nobody is told about."""
    import json

    import anyio
    from mcp import Client

    from bedrok.surfaces import mcp_server
    manifest = json.loads((REPO / "packaging" / "mcpb" / "manifest.json").read_text())

    async def go():
        async with Client(mcp_server.server) as c:
            return {t.name for t in (await c.list_tools()).tools}
    assert {t["name"] for t in manifest["tools"]} == anyio.run(go)


def test_a_fresh_store_already_knows_derived_and_eurostat_series():
    """The first question on a new install may name a computed series or a
    Eurostat country series; neither waits for a full refresh any more."""
    import duckdb
    from bedrok.core import store
    con = duckdb.connect(":memory:")
    store.init_db(con)
    pipeline.load_catalog(con)
    have = {r[0] for r in con.execute("SELECT series_id FROM macro_catalog").fetchall()}
    assert {"us_breakeven_10y", "us_recession", "ltgov_yield_de", "esi_fr"} <= have
    # and a second load never overwrites a name a download has refined
    con.execute("UPDATE macro_catalog SET name = 'from Eurostat' WHERE series_id = 'ltgov_yield_de'")
    pipeline.load_catalog(con)
    assert con.execute("SELECT name FROM macro_catalog WHERE series_id = 'ltgov_yield_de'"
                       ).fetchone()[0] == "from Eurostat"


def test_a_derived_series_fetches_its_empty_inputs_first(monkeypatch):
    import duckdb
    import pandas as pd
    from bedrok.core import store
    con = duckdb.connect(":memory:")
    store.init_db(con)
    pipeline.load_catalog(con)
    fetched = []

    def fake(code, start):
        fetched.append(code)
        return pd.DataFrame({"date": [pd.Timestamp("2026-10-08").date()],
                             "value": [5.22 if "RIFLGFCY10_N" in code else 2.87]})
    monkeypatch.setitem(pipeline.MACRO_FETCHERS, "fedboard", fake)
    monkeypatch.setenv("BEDROK_PACE_FEDBOARD", "0")
    stage, rows = pipeline.refresh_series(con, "us_breakeven_10y")
    assert stage == "derived" and len(fetched) == 2
    assert con.execute("SELECT value FROM macro_series WHERE series_id = 'us_breakeven_10y'"
                       ).fetchone()[0] == 2.35


def test_a_blank_bundle_field_does_not_hide_the_key_in_env(monkeypatch, tmp_path):
    """The bundle sets FRED_API_KEY to '' or '${user_config.fred_api_key}' when
    the field is left blank; the home folder's .env must still supply it."""
    (tmp_path / ".env").write_text("FRED_API_KEY=from-dotenv-1234\nBEA_API_KEY=bea-from-dotenv\n")
    monkeypatch.setenv("FRED_API_KEY", "${user_config.fred_api_key}")
    monkeypatch.setenv("BEA_API_KEY", "")
    monkeypatch.setenv("EIA_API_KEY", "typed-into-the-field")
    try:
        config.set_profile(tmp_path)
        assert sources.key("fred") == "from-dotenv-1234"
        assert sources.key("bea") == "bea-from-dotenv"
        assert sources.key("eia") == "typed-into-the-field"     # a filled field still wins
    finally:
        config.set_profile()
