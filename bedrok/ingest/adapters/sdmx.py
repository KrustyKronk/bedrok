"""One client for every publisher that speaks SDMX REST 2.1.

  {base_url}/data/{flow}/{key}?startPeriod=…

Which publisher, and how it likes to be asked, comes from its row in the source
list (bedrok/seeds/sources.csv): `base_url`, `format`, `user_agent` and
`start_period` (blank: startPeriod as YYYY-MM; `day`: YYYY-MM-DD). A
catalog `source_code` is the path after /data/ exactly as the publisher spells
it — "EXR/D.USD.EUR.SP00.A" (ECB), "OECD.SDD.STES,DSD_STES@DF_CLI,4.1/USA.M…"
(OECD, agency,flow,version), "WS_CBPOL/M.JP" (BIS).

  format  accept   SDMX-CSV via the Accept header (OECD, BIS, IMF, Istat, ABS)
          csvdata  ?format=csvdata — the ECB answers the Accept header 406
          xml      SDMX-ML StructureSpecificData — INSEE serves nothing else

Every format comes back as the store's [date, value]: sorted, de-duplicated,
periods on period-start (_util.parse_period). And every request must name ONE
series: a key with an unpinned dimension returns several series stacked in one
answer, which is how the OECD once interleaved three CLI definitions and how
Istat returns every edition of a release. That raises here instead of being
averaged, overwritten or silently stored.

Quirks confirmed live on 2026-10-10:
  * CSV may be ',' or ';' separated and may start with a byte-order mark
    (Bundesbank, Norges Bank); columns are found by name, never by position.
  * The Bundesbank refuses a month-form startPeriod with 400 and takes only
    YYYY-MM-DD (2026-10-16): its row says start_period = day.
  * SDMX-CSV 2.0 (IMF's 3.0 path) leads with STRUCTURE, STRUCTURE_ID, ACTION
    where 1.0 leads with DATAFLOW; the ECB's own CSV leads with KEY.
  * OECD and INSEE return the newest observation first.
  * Istat versions each series by EDITION; the row's `latest_of` names that
    dimension and only the newest edition is kept (ordered by its numbers).
  * "no data" is a 404 (ABS answers NoRecordsFound), and a startPeriod past the
    last observation is a 400 at some publishers; on an incremental request
    both mean an empty result. On a full pull they mean a wrong code.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from bedrok.ingest.adapters._util import BROWSER_UA, UnpinnedKey, parse_period

CSV_ACCEPT = "application/vnd.sdmx.data+csv;version=1.0.0"
EMPTY = ["date", "value"]
# Columns that describe the message, not the series: never part of its key.
_NOT_DIMENSIONS = {"DATAFLOW", "STRUCTURE", "STRUCTURE_ID", "STRUCTURE_NAME", "ACTION",
                   "KEY", "LAST UPDATE"}


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=EMPTY)


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={"TIME_PERIOD": "date", "OBS_VALUE": "value"})[EMPTY].copy()
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"] = parse_period(df["date"])
    df = (df.dropna(subset=["date", "value"])
            .drop_duplicates(subset="date", keep="last")
            .sort_values("date").reset_index(drop=True))
    df["date"] = df["date"].dt.date
    return df


def _edition_order(label: str) -> tuple[int, ...]:
    """'2026M10G1' → (2026, 10, 1): editions ordered by their numbers, because
    as text '2026M7G30' sorts after '2026M10G1'."""
    return tuple(int(n) for n in re.findall(r"\d+", str(label)))


def parse_csv(text: str, latest_of: str | None = None) -> pd.DataFrame:
    """Pure: an SDMX-CSV (1.0, 2.0, or the ECB's csvdata) body → [date, value].

    `latest_of` names a dimension that versions the whole series (Istat's
    EDITION): only the newest value of it is kept, since each edition carries
    the full history as published that day."""
    text = text.lstrip("﻿")
    if not text.strip():
        return _empty()
    first = text.split("\n", 1)[0]
    sep = ";" if first.count(";") > first.count(",") else ","
    raw = pd.read_csv(io.StringIO(text), sep=sep, dtype=str)
    raw.columns = [c.strip() for c in raw.columns]
    if "TIME_PERIOD" not in raw.columns or "OBS_VALUE" not in raw.columns:
        return _empty()
    if latest_of and latest_of in raw.columns and raw[latest_of].notna().any():
        newest = max(raw[latest_of].dropna().unique(), key=_edition_order)
        raw = raw[raw[latest_of] == newest].drop(columns=[latest_of])
    if "KEY" in raw.columns:
        dims = ["KEY"]
    else:
        lead = list(raw.columns[:list(raw.columns).index("TIME_PERIOD")])
        dims = [c for c in lead if c not in _NOT_DIMENSIONS]
    if dims and len(raw[dims].drop_duplicates()) > 1:
        sample = raw[dims].drop_duplicates().head(3).to_dict("records")
        raise UnpinnedKey(f"key matches {len(raw[dims].drop_duplicates())} series, "
                          f"e.g. {sample}: pin every dimension")
    return _finish(raw)


def parse_xml(stream) -> pd.DataFrame:
    """Pure: an SDMX-ML (StructureSpecific or Generic-compact) body → [date, value]."""
    rows, series = [], 0
    for _, el in ET.iterparse(stream, events=("end",)):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "Obs":
            rows.append((el.get("TIME_PERIOD"), el.get("OBS_VALUE")))
            el.clear()
        elif tag == "Series":
            series += 1
            el.clear()
    if series > 1:
        raise UnpinnedKey(f"key matches {series} series: pin every dimension")
    return _finish(pd.DataFrame(rows, columns=["TIME_PERIOD", "OBS_VALUE"]))


def _split_scale(code: str) -> tuple[str, float]:
    """`FLOW/KEY#scale=1e-6` → ("FLOW/KEY", 1e-6). The IMF answers balance of
    payments in whole US dollars and states the display scale only as a
    `SCALE` attribute, so a row asks for millions with this suffix; it never
    reaches the publisher."""
    key, _, frag = code.partition("#")
    if not frag:
        return code, 1.0
    name, _, value = frag.partition("=")
    if name != "scale":
        raise ValueError(f"unknown SDMX code option {frag!r}")
    return key, float(value)


def fetch(spec: dict, code: str, start: str | None = None) -> pd.DataFrame:
    """Fetch one series. `spec` is the publisher's row from the source list."""
    code, scale = _split_scale(code)
    fmt = spec.get("format") or "accept"
    headers = dict(BROWSER_UA) if spec.get("user_agent") == "browser" else {}
    params = {}
    if fmt == "accept":
        headers["Accept"] = CSV_ACCEPT
    elif fmt == "csvdata":
        params["format"] = "csvdata"
    if start:
        # YYYY-MM is understood by most publishers for daily, monthly and
        # quarterly flows; the client-side filter below trims it. A row with
        # start_period = day sends the date: the Bundesbank answers YYYY-MM
        # with 400, which on an incremental request reads as "nothing new".
        day = spec.get("start_period") == "day"
        params["startPeriod"] = str(start)[:10] if day else str(start)[:7]
    r = requests.get(f"{spec['base_url'].rstrip('/')}/data/{code}",
                     params=params, headers=headers, timeout=120)
    # With a start date, 404/400 means "nothing since then" (ABS, Eurostat).
    # Without one it means the code is wrong, and that must fail loudly.
    if r.status_code in (400, 404) and start:
        return _empty()
    r.raise_for_status()
    df = (parse_xml(io.BytesIO(r.content)) if fmt == "xml"
          else parse_csv(r.text, latest_of=spec.get("latest_of") or None))
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    if scale != 1.0 and not df.empty:
        df["value"] = df["value"] * scale
    return df
