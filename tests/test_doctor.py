"""doctor report sections + a schema/doctor CLI smoke.

The section functions are unit-tested directly on an in-memory store (no profile
needed); the full CLI is smoke-tested via subprocess against a throwaway
profile built in the test, so a clean checkout exercises it too."""
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from bedrok.core import licensing, store
from bedrok.ops import doctor

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def profile_with_store(tmp_path):
    """An empty profile: a profile.toml and an initialised store."""
    (tmp_path / "profile.toml").write_text('[owner]\nname = "Smoke"\n')
    db = tmp_path / "data" / "market.duckdb"
    db.parent.mkdir()
    con = duckdb.connect(str(db))
    store.init_db(con)
    con.close()
    return tmp_path


def _run_cli(profile: Path, cmd: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in ("BEDROK_DB", "FDP_DB", "BEDROK_PROFILE", "FDP_ROOT")}
    return subprocess.run(
        [sys.executable, "-m", "bedrok", "--profile", str(profile), cmd],
        cwd=REPO, capture_output=True, text=True, env=env)


def _store():
    con = duckdb.connect(":memory:")
    store.init_db(con)
    return con


def _seed_fx(con, day, rate: float = 1.10):
    store.upsert_fx(con, "EURUSD", pd.DataFrame(
        {"date": pd.to_datetime([day]).date, "value": [rate]}), "ecb")


def test_freshness_section_lists_streams(capsys):
    con = _store()
    store.upsert_macro_series(con, "us_cpi", pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-01"]).date, "value": [300.0]}))
    doctor._freshness_section(con)
    out = capsys.readouterr().out
    assert "macro_series" in out and "all_series" in out


def test_freshness_warns_when_the_store_has_not_been_refreshed(capsys):
    """ECB reference rates publish every business day, so FX eleven days old
    means nobody refreshed the store. Say it out loud instead of quoting it."""
    con = _store()
    _seed_fx(con, (pd.Timestamp.today() - pd.Timedelta(days=11)).date())
    warns = doctor._freshness_section(con)
    out = capsys.readouterr().out
    assert warns == 1
    assert "WARN" in out and "11 days old" in out
    assert "bedrok refresh" in out and "--data" not in out
    con.close()


def test_freshness_quiet_on_a_fresh_store(capsys):
    con = _store()
    _seed_fx(con, (pd.Timestamp.today() - pd.Timedelta(days=2)).date())
    warns = doctor._freshness_section(con)
    assert warns == 0
    assert "WARN" not in capsys.readouterr().out
    con.close()


def test_restricted_section_reports_status(capsys, monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "")
    con = _store()
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "us_hy_oas", "source": "fred", "source_code": "BAMLH0A0HYM2",
         "name": "HY OAS", "license": licensing.RESTRICTED, "cache": "session"},
        {"series_id": "us_philly_fed", "source": "fedfiles", "source_code": "philly_bos:GAC",
         "name": "Philly", "license": licensing.RESTRICTED, "cache": "store"}]))
    doctor._restricted_section(con)
    out = capsys.readouterr().out
    assert "us_hy_oas" in out and "not stored" in out
    assert "FRED key present: False" in out
    assert "personal use, stored for you" in out and "us_philly_fed" in out

    store.upsert_macro_series(con, "us_hy_oas", pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-02"]).date, "value": [3.1]}))
    doctor._restricted_section(con)
    assert "still stored" in capsys.readouterr().out

def test_doctor_cli_smoke(profile_with_store):
    r = _run_cli(profile_with_store, "doctor")
    assert r.returncode == 0, r.stderr
    assert "=== PROFILE ===" in r.stdout and "DATA FRESHNESS" in r.stdout


def test_schema_cli_smoke(profile_with_store):
    r = _run_cli(profile_with_store, "schema")
    assert r.returncode == 0, r.stderr
    assert "macro_catalog" in r.stdout and "all_series" in r.stdout
