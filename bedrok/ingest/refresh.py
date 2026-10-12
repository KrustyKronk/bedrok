"""Incremental refresh of the local store.

  bedrok refresh            → incremental sync from every publisher
  bedrok refresh --data     → the same; the flag is accepted and ignored, so
                              existing callers (the skill, old docs) keep working

Exit codes: 0 the store is fully updated; 2 bad arguments; 3 the store was NOT
fully updated. 3 rather than 1 because 1 means "there are findings" everywhere
else in Bedrok, and this is a fault — see the note at the foot of `main`.

Incremental sync re-fetches a small overlap window per series so revisions are
picked up; all writes are idempotent upserts. Safe to run repeatedly.

One store, one path: every series, restricted ones included, is fetched from
its publisher with your own keys; a missing key skips its series cleanly.
"""
from __future__ import annotations

import argparse
import sys

from bedrok.core import config, store
from bedrok.ingest import pipeline


def _sync_data(con) -> int:
    print("Incremental sync (overlap window, idempotent upserts)...")
    pipeline.clear_download_caches()
    fails = 0
    for _, title, fn in pipeline.REFRESH_STAGES:
        fails += pipeline.print_report(title, fn(con, incremental=True))
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(prog="bedrok refresh")
    ap.add_argument("--data", action="store_true",
                    help="accepted and ignored (compatibility)")
    ap.parse_args()

    con = config.connect()
    try:
        store.init_db(con)
        pipeline.load_catalog(con)
        fails = _sync_data(con)
    finally:
        store.close_checkpointed(con)

    # The summary is deliberately the LAST thing written, so a failure is never
    # followed on screen by a success line it invalidates.
    if not fails:
        print("\nRefresh complete. 0 failed data item(s).")
        return 0

    print("\n" + "=" * 68)
    print("REFRESH INCOMPLETE — the store was NOT fully updated")
    print("=" * 68)
    print(f"  {fails} failed data item(s) (see the report above)")
    print("\nEverything that did parse was written. Upserts are idempotent, so\n"
          "fix the above and re-run the same command — nothing duplicates.")
    sys.stdout.flush()
    # 3, not 1, and the distinction is the whole point: `validate` uses exit 1
    # to report FINDINGS, which are the normal state of a live store. This is a
    # FAULT — the store was not fully updated. A supervisor cannot tell the two
    # apart from the code alone, and it cannot fall back on spotting a
    # traceback either, because the sync stages deliberately collect failures
    # rather than raising them. A run that reached here must never sign off as
    # done.
    return 3


if __name__ == "__main__":
    sys.exit(main())
