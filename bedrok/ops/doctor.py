"""Store + freshness report — run this first in any Bedrok session.

  bedrok doctor

Offline (no network). Answers, in one place, the questions an agent otherwise
reverse-engineers from the filesystem: which store am I reading, how fresh is
each data stream, and are the restricted series populated. Every section is
independently guarded, so a missing store degrades to a partial report rather
than a traceback.
"""
from __future__ import annotations

import sys
from pathlib import Path

from bedrok.core import config


def _mb(p: Path) -> str:
    try:
        return f"{p.stat().st_size / 1e6:,.1f} MB"
    except OSError:
        return "MISSING"


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _profile_section() -> None:
    _section("PROFILE")
    print(f"  owner        {config.PROFILE['owner']['name']}")
    print(f"  base ccy     {config.BASE_CCY}")
    print(f"  profile dir  {config.PROFILE_DIR}")


def _stores_section() -> int:
    _section("STORE")
    size = _mb(Path(config.DB_PATH)) if config.DB_PATH else "MISSING"
    print(f"  store     {config.DB_PATH}  ({size})")
    return 1 if size == "MISSING" else 0


# ECB reference rates publish every TARGET business day, so the newest FX date
# is the store's heartbeat: older than this means the store has not been
# refreshed. Five days clears a weekend plus a public holiday without crying wolf.
STORE_STALENESS_WARN_DAYS = 5


def _freshness_section(con) -> int:
    """Print per-stream freshness; return the count of things worth flagging."""
    _section("DATA FRESHNESS (rows · max date)")
    from datetime import date as _date

    from bedrok.core import store
    warns = 0
    for t in ("macro_series", "fx_rates"):
        try:
            n, _lo, hi = store.table_range(con, t)
            print(f"  {t:<20} {n:>9,} · {hi}")
        except Exception as e:  # noqa: BLE001
            print(f"  {t:<20} (n/a: {str(e)[:50]})")
    try:
        hi_fx = con.execute("SELECT max(date) FROM fx_rates").fetchone()[0]
    except Exception:  # noqa: BLE001
        hi_fx = None
    if hi_fx is not None:
        age = (_date.today() - hi_fx).days
        if age > STORE_STALENESS_WARN_DAYS:
            warns += 1
            print(f"  WARN  FX rates are {age} days old (max {hi_fx}): the store has "
                  f"not been refreshed.\n        Refresh Mac-side: bedrok refresh  "
                  f"(it cannot run from a sandbox).")
    try:
        kinds = con.execute(
            "SELECT kind, count(*) n, max(date) hi FROM all_series "
            "GROUP BY kind ORDER BY kind").fetchall()
        print("  all_series:  " + " · ".join(f"{k} {n:,} (→{hi})" for k, n, hi in kinds))
    except Exception as e:  # noqa: BLE001
        print(f"  all_series   (n/a: {str(e)[:50]})")
    return warns


def _restricted_section(con) -> None:
    """Session-only series (never stored; fetched live with your own key) and
    personal-use series the store does hold."""
    _section("SESSION-ONLY SERIES (fetched live with your own key, never stored)")
    from bedrok.core import licensing
    from bedrok.core import sources
    have_key = bool(sources.key("fred"))  # never print the value
    session = licensing.session_series(con)
    if not session:
        print("  none in the catalog")
    for sid in session:
        n = con.execute("SELECT count(*) FROM macro_series WHERE series_id = ?",
                        [sid]).fetchone()[0]
        state = (f"!! {n:,} rows still stored — `bedrok refresh` removes them"
                 if n else "not stored")
        print(f"  {sid:<24} {state}")
    if session:
        print(f"  FRED key present: {have_key}"
              + ("" if have_key else " — add one to profile/.env to fetch these live"))

    held = [sid for sid in licensing.restricted_series(con) if sid not in session]
    if held:
        print("  personal use, stored for you and never to be passed on: "
              + ", ".join(held))


def _guard(fn, *args) -> None:
    """Run a report section; a failure prints a one-liner, never a traceback."""
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001
        print(f"  (section unavailable: {type(e).__name__}: {str(e)[:70]})")


def main() -> int:
    _guard(_profile_section)
    missing = 0
    try:
        missing = _stores_section()
    except Exception as e:  # noqa: BLE001
        print(f"  (store unavailable: {str(e)[:70]})")

    try:
        con = config.connect(read_only=True)
    except SystemExit as e:
        print(f"\ncould not open the store: {e}")
        return 1
    try:
        _guard(_freshness_section, con)
        _guard(_restricted_section, con)
    finally:
        con.close()

    print()
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
