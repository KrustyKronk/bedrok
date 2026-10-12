"""Destatis GENESIS-Online adapter (free API token, DESTATIS_API_KEY).

`source_code` is a GENESIS table and a selection, as a query string:

  61111-0002?value_variable_code=PREIS1&value_unit=2020=100
  42153-0001?classifyingvariable1=WZ08V1&classifyingkey1=WZ08-B-05
            &classifyingvariable2=WERT03&classifyingkey2=X13JDKSB

Lower-case GENESIS parameters (`classifyingvariable1`, `classifyingkey1`, …)
are sent with the request and narrow what the server cuts; any other name is a
column of the flat CSV it answers with (`value_unit`, `value_variable_code`),
and only rows equal to it are kept. A table holds several measures side by side
(Germany's CPI table carries the index and two percent changes), so the
selection must leave one value per period or the answer is refused.

  POST https://genesis.destatis.de/genesisWS/rest/2020/data/tablefile
       headers username=<token>, password=""   (the token never goes in a URL)
       form    name, area=all, format=ffcsv, startyear, language=en, …

The answer is a zip holding one ';'-separated flat CSV: `time` is the year,
the month or quarter is an attribute code (MONAT01–12, QUART1–4), and missing
values are '...', '.', '-' or 'x'.

A table too big to cut on the spot answers 200 with Status Code 98 and asks for
a background job. Narrow the selection with classifying keys instead: every
series wired here fits in one direct request.

The old GAST guest login no longer returns data (checked 2026-10-10).
Licence: Datenlizenz Deutschland – Namensnennung – Version 2.0.
"""
from __future__ import annotations

import io
import re
import zipfile

import pandas as pd
import requests

from bedrok.ingest.adapters._util import UnpinnedKey

BASE = "https://genesis.destatis.de/genesisWS/rest/2020"
_REQUEST_PARAM = re.compile(r"^[a-z]+\d*$")   # classifyingvariable1, regionalkey, …


def split_code(code: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """'T?classifyingkey1=A&value_unit=2020=100' → (T, {request}, {column filters})."""
    table, _, query = code.partition("?")
    request, columns = {}, {}
    for part in filter(None, query.split("&")):
        name, _, value = part.partition("=")
        (request if _REQUEST_PARAM.match(name) else columns)[name] = value
    return table, request, columns


def fetch(source_code: str, api_key: str, start: str | None = None) -> pd.DataFrame:
    table, request, columns = split_code(source_code)
    form = {"name": table, "area": "all", "format": "ffcsv", "compress": "false",
            "transpose": "false", "language": "en",
            "startyear": str(start)[:4] if start else "1900", **request}
    r = requests.post(f"{BASE}/data/tablefile", data=form, timeout=180,
                      headers={"username": api_key, "password": ""})
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        status = (r.json().get("Status") or {}) if "json" in r.headers.get("content-type", "") else {}
        raise ValueError(f"GENESIS {table}: {status.get('Code')} {status.get('Content') or r.text[:200]}")
    df = _parse(r.content, columns)
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df


def _period_start(year: str, codes: list[str]):
    for c in codes:
        m = re.fullmatch(r"MONAT(\d{2})", str(c))
        if m:
            return pd.Timestamp(int(year), int(m.group(1)), 1).date()
        q = re.fullmatch(r"QUART(\d)", str(c))
        if q:
            return pd.Timestamp(int(year), (int(q.group(1)) - 1) * 3 + 1, 1).date()
    return pd.Timestamp(int(year), 1, 1).date()


def _parse(zipped: bytes, columns: dict[str, str]) -> pd.DataFrame:
    """Pure: a GENESIS ffcsv zip → [date, value], keeping rows equal to `columns`."""
    z = zipfile.ZipFile(io.BytesIO(zipped))
    raw = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])), sep=";", dtype=str,
                      encoding="utf-8-sig", keep_default_na=False)
    for name, value in columns.items():
        if name not in raw.columns:
            raise ValueError(f"GENESIS: no column {name!r} in {list(raw.columns)}")
        raw = raw[raw[name] == value]
    if raw.empty:
        raise ValueError(f"GENESIS: nothing matches {columns} (rebased or renamed?)")
    attr = [c for c in raw.columns if c.endswith("_variable_attribute_code")]
    raw = raw.assign(date=[_period_start(y, codes) for y, codes in
                           zip(raw["time"], raw[attr].values.tolist())])
    others = [c for c in attr + ["value_variable_code", "value_unit"]
              if c in raw.columns and not re.fullmatch(r"(MONAT|QUART)\w*", str(raw[c].iloc[0]))]
    wide = {c: sorted(raw[c].unique())[:4] for c in others if raw[c].nunique() > 1}
    if wide:
        raise UnpinnedKey(f"GENESIS selection holds several series along {wide}: pin them")
    df = pd.DataFrame({"date": raw["date"],
                       "value": pd.to_numeric(raw["value"].str.replace(",", "."), errors="coerce")})
    return (df.dropna(subset=["date", "value"])
              .drop_duplicates(subset="date", keep="last")
              .sort_values("date").reset_index(drop=True))
