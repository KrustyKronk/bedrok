"""refresh: one path, and the exit codes a supervisor reads."""
import sys

import duckdb
import pytest

from bedrok.core import config, store
from bedrok.ingest import pipeline, refresh


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    store.init_db(c)
    yield c
    c.close()


def _one_store(monkeypatch, con):
    monkeypatch.setattr(config, "connect", lambda *a, **k: con)
    monkeypatch.setattr(store, "close_checkpointed", lambda c: None)
    monkeypatch.setattr(pipeline, "load_catalog", lambda c: None)
    monkeypatch.setattr(sys, "argv", ["bedrok refresh", "--data"])


def test_a_failed_sync_exits_3_not_1(con, monkeypatch):
    """The interlock hangs off this number.

    `validate` uses exit 1 for FINDINGS, which are the normal state of a live
    store, so a supervisor cannot treat 1 as a fault without raising a false
    alarm every run. It cannot spot a traceback either, because the sync stages
    collect failures rather than raising. 3 is what makes "the store was not
    updated" legible to a machine — on 2026-08-30 this returned 1, landed in the
    wrapper's PROBLEMS bucket, and the dead run signed itself off as done.
    """
    _one_store(monkeypatch, con)
    monkeypatch.setattr(refresh, "_sync_data", lambda c: 1)   # one failed data item
    assert refresh.main() == 3


def test_a_clean_run_still_exits_0(con, monkeypatch):
    """The other half of the contract: a healthy run must not trip the interlock."""
    _one_store(monkeypatch, con)
    monkeypatch.setattr(refresh, "_sync_data", lambda c: 0)
    assert refresh.main() == 0


def test_refresh_runs_one_path_in_order(con, monkeypatch):
    """Open, initialise, load the repo catalog, sync, close — and nothing else."""
    calls = []
    monkeypatch.setattr(config, "connect", lambda *a, **k: calls.append("connect") or con)
    monkeypatch.setattr(store, "init_db", lambda c: calls.append("init"))
    monkeypatch.setattr(pipeline, "load_catalog", lambda c: calls.append("catalog"))
    monkeypatch.setattr(refresh, "_sync_data", lambda c: calls.append("sync") or 0)
    monkeypatch.setattr(store, "close_checkpointed", lambda c: calls.append("close"))
    monkeypatch.setattr(sys, "argv", ["bedrok refresh"])
    assert refresh.main() == 0
    assert calls == ["connect", "init", "catalog", "sync", "close"]


def test_a_failed_backfill_exits_3_like_refresh(con, monkeypatch):
    """`backfill` returned 1 on failures, the code `validate` uses for findings."""
    from bedrok.ingest import backfill
    _one_store(monkeypatch, con)
    monkeypatch.setattr(sys, "argv", ["bedrok backfill", "--stage", "fx"])
    monkeypatch.setattr(pipeline, "sync_fx", lambda c, incremental=False: [])
    monkeypatch.setattr(pipeline, "print_report", lambda title, report: 1)
    assert backfill.main() == 3
    monkeypatch.setattr(pipeline, "print_report", lambda title, report: 0)
    assert backfill.main() == 0
