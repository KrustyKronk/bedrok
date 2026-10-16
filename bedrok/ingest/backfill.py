"""Deep historical pull into the local store.

Pulls maximum available history for every catalog series and FX pair, straight
from each publisher. Idempotent — safe to re-run; existing rows are updated,
not duplicated.

  bedrok backfill                          # everything (deep history)
  bedrok backfill --only us_nfci,us_10y    # just these catalog series
  bedrok backfill --stage eurostat         # just one bulk stage (repeatable)

With --only, the bulk stages are skipped; only the per-series catalog loop
runs, filtered to the named ids. --stage is the complement: it selects whole
stages (including the bulk ones --only cannot reach), which is how a newly
added multi-geo set gets its first deep pull without re-running everything else.
"""
from __future__ import annotations

import argparse
import sys

from bedrok.core import config
from bedrok.ingest import derived
from bedrok.ingest import pipeline
from bedrok.core import store


def main() -> int:
    ap = argparse.ArgumentParser(prog="bedrok backfill")
    ap.add_argument("--only", default=None,
                    help="comma-separated catalog series_ids to backfill")
    ap.add_argument("--stage", action="append", default=None,
                    choices=["macro", "hicp", "eurostat", "pinksheet", "cot",
                             "derived", "fx"],
                    help="run only these stage(s); repeatable. --only targets "
                         "individual series, --stage targets whole bulk stages")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None
    stages = set(args.stage) if args.stage else None

    con = config.connect()
    fails = 0
    try:
        store.init_db(con)
        pipeline.load_catalog(con)

        if only is not None:
            print(f"Targeted backfill (deep history) for {len(only)} id(s): {', '.join(sorted(only))}")
            fails += pipeline.print_report("Macro series", pipeline.sync_macro(con, incremental=False, only=only))
        else:
            def run(stage, title, fn):
                if stages is not None and stage not in stages:
                    return 0
                return pipeline.print_report(title, fn(con, incremental=False))

            print("Catalog loaded. Starting "
                  + (f"backfill of stage(s): {', '.join(sorted(stages))}" if stages
                     else "full backfill (deep history)") + "...")
            fails += run("macro", "Macro series", pipeline.sync_macro)
            fails += run("hicp", "HICP Europe (bulk)", pipeline.sync_hicp_europe)
            fails += run("eurostat", "Eurostat multi-geo (bulk)", pipeline.sync_eurostat_multi)
            fails += run("pinksheet", "Commodities (Pink Sheet)", pipeline.sync_pink_sheet)
            fails += run("cot", "CFTC positioning (bulk)", pipeline.sync_cot)
            # Derived series read the store, so they must follow every fetch stage.
            fails += run("derived", "Derived series", derived.sync_derived)
            fails += run("fx", "FX rates", pipeline.sync_fx)
    finally:
        store.close_checkpointed(con)

    print(f"\nBackfill complete. {fails} failed item(s).")
    # 3, as `refresh` returns, not 1: exit 1 is `validate`'s "findings", the
    # normal state of a live store; this is a store not fully updated.
    return 3 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
