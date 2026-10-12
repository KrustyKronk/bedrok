"""`bedrok smoke` is the weekly liveness check, so its verdicts are pinned.

PASS for fresh data, WARN for data older than the series' allowance (a moved
publisher can leave the old file answering, frozen), SKIP for a missing key,
FAIL for an error or an empty answer — and only a FAIL fails the run. Checks
are paced the way a refresh is. No network: every fetcher is a fake, and the
values are synthetic.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from bedrok.ingest import pipeline
from bedrok.ops import smoke


def _frame(days_ago: int) -> pd.DataFrame:
    d = (date.today() - timedelta(days=days_ago)).isoformat()
    return pd.DataFrame({"date": [d], "value": [1.5]})


def _row(series: str, key_env: str = "", required: str = "no") -> dict:
    return {"smoke_series": series, "key_env": key_env, "key_required": required,
            "pacing_s": 0.0}


@pytest.fixture
def world(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.csv"
    catalog.write_text(
        "series_id,source,source_code,frequency,staleness_days\n"
        "s_fresh,fresh,F1,monthly,90\n"
        "s_old,old,O1,monthly,90\n"
        "s_empty,empty,E1,monthly,90\n"
        "s_keyed,keyed,K1,monthly,90\n")
    monkeypatch.setattr(pipeline, "CATALOG_CSV", catalog)
    monkeypatch.setattr(smoke.sources, "load", lambda: {
        "fresh": _row("s_fresh"), "old": _row("s_old"),
        "empty": _row("s_empty"), "keyed": _row("s_keyed", "KEYED_API_KEY", "yes"),
        "optional": _row("s_fresh", "OPTIONAL_API_KEY", "optional"),
        "derived": _row("")})

    def missing(code, start):
        raise pipeline.MissingKey("KEYED_API_KEY is not set")

    monkeypatch.setattr(pipeline, "MACRO_FETCHERS", {
        "fresh": lambda code, start: _frame(5),
        "old": lambda code, start: _frame(400),
        "empty": lambda code, start: pd.DataFrame(columns=["date", "value"]),
        "keyed": missing,
        "optional": lambda code, start: _frame(5)})
    monkeypatch.setattr(smoke.worldbank, "fetch_pink_sheet",
                        lambda: ({"wb_crude_brent": _frame(20)}, {}))
    monkeypatch.setattr(smoke.eurostat, "fetch_multi_geo",
                        lambda code: ({"DE": _frame(20)}, {}))
    monkeypatch.setattr(smoke.cftc, "fetch_cot",
                        lambda years: ({"cot_gold_net_noncomm": _frame(3)}, []))
    monkeypatch.setattr(smoke.fedboard, "release_status", lambda rel: _frame(3))
    paced = []
    monkeypatch.setattr(pipeline, "_pace", paced.append)
    return paced


def _run(monkeypatch, capsys, *args):
    monkeypatch.setattr("sys.argv", ["bedrok smoke", *args])
    code = smoke.main()
    # The table is fixed width: SOURCE 12, a space, CHECK 28, a space, RESULT.
    lines = {ln[:12].strip(): ln[42:50].strip() for ln in capsys.readouterr().out.splitlines()
             if ln[:12].strip() in {"fresh", "old", "empty", "keyed", "optional",
                                    "worldbank", "eurostat", "cftc"}}
    return code, lines


def test_each_verdict_and_only_a_failure_fails_the_run(world, monkeypatch, capsys):
    code, verdict = _run(monkeypatch, capsys)
    assert verdict["fresh"] == "PASS"
    assert verdict["old"] == "WARN"
    assert verdict["empty"] == "FAIL"
    assert verdict["keyed"] == "SKIP"
    assert {verdict[s] for s in ("worldbank", "eurostat", "cftc")} == {"PASS"}
    assert code == 1


def test_a_run_with_only_warnings_and_skips_passes(world, monkeypatch, capsys):
    pipeline.MACRO_FETCHERS["empty"] = lambda code, start: _frame(1)
    code, verdict = _run(monkeypatch, capsys)
    assert verdict["empty"] == "PASS" and verdict["old"] == "WARN"
    assert code == 0


def test_keyless_leaves_out_only_sources_that_require_a_key(world, monkeypatch, capsys):
    _, verdict = _run(monkeypatch, capsys, "--keyless")
    assert "keyed" not in verdict
    assert verdict["optional"] == "PASS"  # the BLS works without its key
    assert "fresh" in verdict


def test_a_source_without_a_smoke_series_has_no_check(world):
    assert "derived" not in {source for source, *_ in smoke.checks()}


def test_every_check_after_the_first_is_paced(world, monkeypatch, capsys):
    _run(monkeypatch, capsys)
    sources = [source for source, *_ in smoke.checks()]
    assert world == sources[1:]


def test_every_fed_board_release_file_gets_its_own_check(tmp_path, monkeypatch, capsys):
    """Session B: the Board is retiring its download program around the
    release zips Bedrok reads; one of eight going must show the week it goes,
    not when a refresh happens to need it."""
    catalog = tmp_path / "catalog.csv"
    catalog.write_text(
        "series_id,source,source_code,frequency,staleness_days\n"
        "us_sloos,fedboard,SLOOS:S1,quarterly,150\n"
        "us_10y,fedboard,H15:Y10,daily,10\n"
        "us_m2,fedboard,H6:M2:billions,monthly,90\n")
    monkeypatch.setattr(pipeline, "CATALOG_CSV", catalog)
    monkeypatch.setattr(smoke.sources, "load", lambda: {"fedboard": _row("us_sloos")})
    monkeypatch.setattr(pipeline, "MACRO_FETCHERS", {"fedboard": lambda code, start: _frame(5)})
    monkeypatch.setattr(smoke.worldbank, "fetch_pink_sheet", lambda: ({"wb_crude_brent": _frame(2)}, {}))
    monkeypatch.setattr(smoke.eurostat, "fetch_multi_geo", lambda code: ({"DE": _frame(2)}, {}))
    monkeypatch.setattr(smoke.cftc, "fetch_cot", lambda years: ({"cot_gold_net_noncomm": _frame(3)}, []))
    monkeypatch.setattr(pipeline, "_pace", lambda s: None)
    seen = []

    def status(rel):
        seen.append(rel)
        if rel == "H6":
            raise ValueError("the H6 release zip is gone")
        return _frame(1)
    monkeypatch.setattr(smoke.fedboard, "release_status", status)
    monkeypatch.setattr("sys.argv", ["bedrok smoke"])
    code = smoke.main()
    out = capsys.readouterr().out
    assert seen == ["H15", "H6"]                    # SLOOS is the smoke series itself
    assert "H15 release zip" in out and "PASS" in out.split("H15 release zip")[1].split("\n")[0]
    assert "FAIL" in out.split("H6 release zip")[1].split("\n")[0] and code == 1
