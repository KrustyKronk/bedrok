"""Federal Reserve Board release files: one SDMX-XML zip per statistical release.
No key.

  https://www.federalreserve.gov/releases/{rel}/data/FRB_{REL}_xml.zip

`source_code` is "<REL>:<SERIES_NAME>[:<scale>]", e.g.

  H15:RIFLGFCY10_N.B            10-year Treasury constant maturity, percent
  H8:B1001NCBA:billions         bank credit, all commercial banks, billions USD

The Board's own crosswalk from its series names to FRED ids is at
federalreserve.gov/data/documents/DDP-FRED%20Data%20Series%20Crosswalk.csv.

Why these files and not the Data Download Program: the Board is retiring the
DDP ("Build Your Package" goes the week of 2026-11-09) and points users at
FRED. The release zips are what survives. If they go too, Treasury's par-curve
XML feed is the fallback for the yields only (from 1990, not 1962).

Every release uses the same shape — <Series SERIES_NAME FREQ UNIT_MULT> holding
<Obs TIME_PERIOD OBS_VALUE OBS_STATUS> — so one parser serves all of them.
Format traps, confirmed against the live files on 2026-10-10:
  * monthly (FREQ 129) and quarterly (FREQ 162) observations are dated to the
    period END; the store dates them to the period START.
  * OBS_STATUS "ND" (no data: holidays) carries a placeholder value; dropped.
  * a SERIES_NAME can repeat across datasets inside one file (G.17 carries
    IP.B50001.S three times, identically); the first one wins.
  * UNIT_MULT is the multiplier to dollars. H.8 publishes in thousands where
    FRED and the catalog use billions — hence the optional scale.
  * the files are big (H.4.1 unzips to ~127 MB), so they are stream-parsed and
    only the series the run asked for are kept.

Licence: public domain — a US government work.
"""
from __future__ import annotations

import functools
import io
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd
import requests

from bedrok.ingest.adapters._util import BROWSER_UA

URL = "https://www.federalreserve.gov/releases/{rel}/data/FRB_{REL}_xml.zip"
SCALES = {"units": 1, "thousands": 1e3, "millions": 1e6, "billions": 1e9}
MONTHLY, QUARTERLY = "129", "162"
EMPTY = ["date", "value"]

_primed: dict[str, frozenset[str]] = {}
_parsed: dict[tuple, dict[str, tuple[dict, list]]] = {}


def prime(codes) -> None:
    """Declare every fedboard series the coming run will fetch, so each release
    file is downloaded and parsed once for all of them."""
    _primed.clear()
    _parsed.clear()
    by_rel: dict[str, set[str]] = {}
    for code in codes:
        rel, name = code.split(":")[:2]
        by_rel.setdefault(rel, set()).add(name)
    _primed.update({rel: frozenset(names) for rel, names in by_rel.items()})


def parse(stream, names) -> dict[str, tuple[dict, list]]:
    """Pure: an SDMX-XML release stream → {series name: (attributes, obs)} for
    the names asked for. obs is a list of (TIME_PERIOD, OBS_VALUE, OBS_STATUS)."""
    found: dict[str, tuple[dict, list]] = {}
    current = None
    for event, el in ET.iterparse(stream, events=("start", "end")):
        tag = el.tag.rsplit("}", 1)[-1]
        if event == "start" and tag == "Series":
            name = el.get("SERIES_NAME")
            current = None
            if name in names and name not in found:
                current = (dict(el.attrib), [])
                found[name] = current
        elif event == "end" and tag == "Obs":
            if current is not None:
                current[1].append((el.get("TIME_PERIOD"), el.get("OBS_VALUE"),
                                   el.get("OBS_STATUS")))
            el.clear()
        elif event == "end" and tag == "Series":
            current = None
            el.clear()
    return found


def to_frame(attrs: dict, obs: list, scale: str | None = None) -> pd.DataFrame:
    """Pure: one parsed series → DataFrame[date, value] on the store's dating."""
    df = pd.DataFrame([(t, v) for t, v, s in obs if s != "ND"], columns=EMPTY)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna()
    freq = attrs.get("FREQ")
    if freq == MONTHLY:
        df["date"] = df["date"].values.astype("datetime64[M]")
    elif freq == QUARTERLY:
        df["date"] = pd.PeriodIndex(df["date"], freq="Q").to_timestamp()
    if scale:
        df["value"] = df["value"] * float(attrs.get("UNIT_MULT") or 1) / SCALES[scale]
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return (df.drop_duplicates(subset=["date"], keep="last")
              .sort_values("date").reset_index(drop=True))[EMPTY]


@functools.lru_cache(maxsize=4)
def _download(rel: str) -> bytes:
    r = requests.get(URL.format(rel=rel.lower(), REL=rel.upper()),
                     headers=BROWSER_UA, timeout=300)
    r.raise_for_status()
    return r.content


def _release(rel: str, names: frozenset[str]) -> dict:
    key = (rel, names)
    if key not in _parsed:
        with zipfile.ZipFile(io.BytesIO(_download(rel))) as z:
            with z.open(f"{rel.upper()}_data.xml") as f:
                _parsed[key] = parse(f, names)
    return _parsed[key]


def fetch(source_code: str, start: str | None = None) -> pd.DataFrame:
    parts = source_code.split(":")
    rel, name = parts[0], parts[1]
    scale = parts[2] if len(parts) > 2 else None
    names = _primed.get(rel, frozenset())
    if name not in names:
        names = frozenset({name})
    found = _release(rel, names)
    if name not in found:
        raise ValueError(f"{name} not found in the {rel} release file")
    df = to_frame(*found[name], scale=scale)
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df
