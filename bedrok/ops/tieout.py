"""Tie-out: is the series a new source would give us the series we already hold?

Run BEFORE the refresh that re-points a series. For every catalog row in the
repo whose source differs from the one its stored history came from
(`macro_catalog.filled_from`), this fetches the full history from the new
source into memory and compares it with the store over the dates both have.
It writes nothing; the next `bedrok refresh` is what swaps the history in.

  bedrok tieout [--only id1,id2]

The yardstick is the one from the session-2 rebuild (docs/engineering/notes.md,
"Judge a rebuild by correlation"): equality where the publisher is the same
publisher, and otherwise correlation and the largest move in standard
deviations. Relative difference is the wrong test for series that sit near zero.

  same     at least 99% of the shared dates agree to 1e-6
  revised  not equal, but correlation >= 0.999 and no move beyond 0.5 sd —
           the same series at a different vintage
  CHECK    anything else, or a history that starts materially later or ends
           earlier than the stored one. Read it before refreshing.

Exit code 1 if any series needs a CHECK, so a script cannot sail past one.
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta

import pandas as pd

from bedrok.core import config, store
from bedrok.ingest import derived, pipeline

EQUAL_TOL = 1e-6
SAME_SHARE = 0.99
MIN_CORR = 0.999
MAX_SD_MOVE = 0.5
# A new history may start a little later than the old one without anyone
# minding (a month-end vs month-start date, one missing first print).
START_SLACK = timedelta(days=40)


def compare(old: pd.DataFrame, new: pd.DataFrame) -> dict:
    """Pure: two [date, value] frames → the tie-out numbers and a verdict."""
    out = {"old_start": None, "old_end": None, "new_start": None, "new_end": None,
           "overlap": 0, "equal": None, "corr": None, "max_sd": None,
           "old_last": None, "new_last": None, "verdict": "CHECK", "why": ""}
    if not old.empty:
        out.update(old_start=old["date"].min(), old_end=old["date"].max(),
                   old_last=float(old.sort_values("date")["value"].iloc[-1]))
    if new.empty:
        out["why"] = "new source returned nothing"
        return out
    out.update(new_start=new["date"].min(), new_end=new["date"].max(),
               new_last=float(new.sort_values("date")["value"].iloc[-1]))
    if old.empty:
        out.update(verdict="new", why="nothing stored to compare with")
        return out

    both = old.merge(new, on="date", suffixes=("_old", "_new"))
    out["overlap"] = len(both)
    if len(both) < 3:
        out["why"] = "fewer than 3 shared dates"
        return out
    diff = (both["value_new"] - both["value_old"]).abs()
    out["equal"] = float((diff <= EQUAL_TOL * both["value_old"].abs().clip(lower=1)).mean())
    sd = both["value_old"].std()
    out["corr"] = float(both["value_old"].corr(both["value_new"]))
    out["max_sd"] = float(diff.max() / sd) if sd and sd > 0 else 0.0

    why = []
    if out["new_start"] > out["old_start"] + START_SLACK:
        why.append(f"starts later ({out['new_start']} vs {out['old_start']})")
    if out["new_end"] < out["old_end"]:
        why.append(f"ends earlier ({out['new_end']} vs {out['old_end']})")
    if out["equal"] >= SAME_SHARE:
        verdict = "same"
    elif out["corr"] >= MIN_CORR and out["max_sd"] <= MAX_SD_MOVE:
        verdict = "revised"
    else:
        verdict = "CHECK"
        why.append(f"corr {out['corr']:.4f}, max move {out['max_sd']:.2f} sd")
    out["verdict"] = "CHECK" if why else verdict
    out["why"] = "; ".join(why)
    return out


def _pending(con, only) -> pd.DataFrame:
    """Repo catalog rows whose stored history came from somewhere else."""
    seed = pd.read_csv(pipeline.CATALOG_CSV, dtype=str)
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'macro_catalog'").fetchall()}
    # A store no refresh has migrated yet: its history came from the source its
    # catalog names (store._migrate_macro_catalog makes the same call).
    origin = "filled_from" if "filled_from" in cols else "source || ':' || source_code"
    held = con.execute(f"SELECT series_id, {origin} AS filled_from FROM macro_catalog "
                       "WHERE series_id IN (SELECT DISTINCT series_id FROM macro_series)"
                       ).fetch_df()
    rows = seed.merge(held, on="series_id", how="left")
    rows["origin"] = rows["source"] + ":" + rows["source_code"]
    if only:
        return rows[rows["series_id"].isin(only)]
    return rows[rows["filled_from"].notna() & (rows["filled_from"] != rows["origin"])]


def _pending_derived(con, only) -> list[dict]:
    """Derived specs whose id holds history from somewhere else (FRED's copy of
    a spread Bedrok now computes itself)."""
    held = dict(con.execute(
        "SELECT series_id, coalesce(filled_from, '') FROM macro_catalog "
        "WHERE series_id IN (SELECT DISTINCT series_id FROM macro_series)").fetchall()) \
        if "filled_from" in {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'macro_catalog'").fetchall()} else {}
    out = []
    for spec in derived.DERIVED:
        sid = spec["series_id"]
        if only is not None and sid not in only:
            continue
        if sid in held and held[sid] != derived.origin(spec):
            out.append(spec)
    return out


def _fmt(v, spec):
    return "—" if v is None else format(v, spec)


def main() -> int:
    ap = argparse.ArgumentParser(prog="bedrok tieout", description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="comma-separated series_ids (default: every re-pointed row)")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None

    con = config.connect(read_only=True)
    try:
        rows = _pending(con, only)
        specs = _pending_derived(con, only)
        if rows.empty and not specs:
            print("Nothing to tie out: every stored series came from the source "
                  "the catalog names.")
            return 0
        for source, prime in pipeline.MACRO_PRIMERS.items():
            prime(rows.loc[rows["source"] == source, "source_code"].tolist())

        print(f"\n{'SERIES':<24} {'FROM → TO':<44} {'VERDICT':<8} "
              f"{'EQUAL':>6} {'CORR':>8} {'MAXSD':>6}  OLD/NEW START   LAST OLD → NEW")
        print("-" * 140)
        checks = 0
        for _, r in rows.iterrows():
            sid = r["series_id"]
            old = con.execute("SELECT date, value FROM macro_series WHERE series_id = ? "
                              "ORDER BY date", [sid]).fetch_df()
            if not old.empty:
                old["date"] = pd.to_datetime(old["date"]).dt.date
            route = f"{r['filled_from']} → {r['origin']}"
            try:
                new = pipeline._fetch_macro(r, None)
            except pipeline.MissingKey as e:
                print(f"{sid:<24} {route:<44} {'skip':<8} {e}")
                continue
            except Exception as e:  # noqa: BLE001
                checks += 1
                print(f"{sid:<24} {route:<44} {'FAIL':<8} {pipeline.error_detail(e, 60)}")
                continue
            c = compare(old, new)
            checks += c["verdict"] == "CHECK"
            print(f"{sid:<24} {route:<44} {c['verdict']:<8} "
                  f"{_fmt(c['equal'], '6.1%')} {_fmt(c['corr'], '8.5f')} "
                  f"{_fmt(c['max_sd'], '6.2f')}  {c['old_start']}/{c['new_start']}  "
                  f"{_fmt(c['old_last'], ',.4g')} → {_fmt(c['new_last'], ',.4g')}"
                  + (f"\n{'':<24} {c['why']}" if c["why"] else ""))
            pipeline._pace(r["source"])
        for spec in specs:
            sid = spec["series_id"]
            old = con.execute("SELECT date, value FROM macro_series WHERE series_id = ? "
                              "ORDER BY date", [sid]).fetch_df()
            old["date"] = pd.to_datetime(old["date"]).dt.date
            new = derived.OPS[spec["op"]](con, spec["inputs"])
            if not new.empty:
                new["date"] = pd.to_datetime(new["date"]).dt.date
            c = compare(old, new)
            checks += c["verdict"] == "CHECK"
            route = f"{con.execute('SELECT filled_from FROM macro_catalog WHERE series_id = ?', [sid]).fetchone()[0]} → {derived.origin(spec)}"
            print(f"{sid:<24} {route[:44]:<44} {c['verdict']:<8} "
                  f"{_fmt(c['equal'], '6.1%')} {_fmt(c['corr'], '8.5f')} "
                  f"{_fmt(c['max_sd'], '6.2f')}  {c['old_start']}/{c['new_start']}  "
                  f"{_fmt(c['old_last'], ',.4g')} → {_fmt(c['new_last'], ',.4g')}"
                  + (f"\n{'':<24} {c['why']}" if c["why"] else ""))
        print("-" * 140)
        print(f"{len(rows) + len(specs)} series, {checks} to check. Nothing was written; "
              "`bedrok refresh` swaps in the new histories.")
        return 1 if checks else 0
    finally:
        store.close_checkpointed(con)


if __name__ == "__main__":
    sys.exit(main())
