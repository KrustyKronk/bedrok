"""One client for the PxWeb family: the Nordic statistics offices' table APIs.

A catalog `source_code` is a table and a selection, in the form of a query
string with EVERY dimension named:

  TAB6590?ContentsCode=000007ZN&Tid=*                    (Statistics Sweden)
  14706?KPIavledetSerie=KPI-JAE&ContentsCode=KPIJustIndMnd&Tid=*   (Norway)
  khi/11xs.px?contentscode=ip_0_1972                     (Statistics Finland)
  PRIS01?VAREGR=000000&ENHED=100&Tid=*                   (Statistics Denmark)

`*` takes every value, and is meant for the time dimension only: the answer is
refused if any other dimension comes back with more than one value
(_util.parse_jsonstat), the same rule the SDMX client keeps.

How to ask comes from the publisher's row in the source list, `format`:

  v2        PxWebApi 2: GET {base}/tables/{table}/data?valueCodes[dim]=…
            (Statistics Sweden, Statistics Norway). Every dimension must be
            named, the time one with `*`: an unnamed dimension gets the
            table's default selection, which is often not the whole history.
  v1        PxWeb 1: POST {base}/{table} with a JSON query (Statistics
            Finland). Unnamed dimensions are eliminated or refused by the
            server, so name them all here too; time may be left out, and then
            every period comes back.
  statbank  Statistics Denmark's own API, PxWeb in all but name:
            GET {base}/data/{table}/JSONSTAT?{dim}={value}.

All three answer in JSON-stat. History is fetched in full and filtered here:
none of these tables is large enough to make an incremental query worth its
per-publisher syntax.

Confirmed live on 2026-10-10:
  * Tables are replaced, not revised, when a base year changes: Statistics
    Sweden's CPIF 1987=100 table and Statistics Denmark's PRIS111 both stopped
    at 2025M12 and new tables carry the series on. The smoke series shows it.
  * Values with non-ASCII letters (Swedish ALÖSP) must be percent-encoded,
    and a '+' inside a value code (Sweden's '1+2', both sexes) is a plus, not
    a space; requests encodes both correctly from the split selection.
  * Missing observations are null, and early periods of a derived series
    (Norway's CPI-ATE) are null before the series begins.
"""
from __future__ import annotations

import pandas as pd
import requests

from bedrok.ingest.adapters._util import BROWSER_UA, parse_jsonstat


def split_code(code: str) -> tuple[str, list[tuple[str, str]]]:
    """'TAB?A=1&B=*' → ('TAB', [('A', '1'), ('B', '*')]). Order is kept.

    Split by hand rather than with parse_qsl, which reads '+' as a space:
    Statistics Sweden's "both sexes" is the value code '1+2'."""
    table, _, query = code.partition("?")
    selection = [tuple(part.split("=", 1)) for part in query.split("&") if "=" in part]
    return table.strip("/"), selection


def _request(spec: dict, code: str) -> requests.Response:
    base = spec["base_url"].rstrip("/")
    fmt = spec.get("format") or "v2"
    headers = dict(BROWSER_UA) if spec.get("user_agent") == "browser" else {}
    table, selection = split_code(code)
    if fmt == "v2":
        params = [("lang", "en"), ("outputFormat", "json-stat2")]
        params += [(f"valueCodes[{dim}]", value) for dim, value in selection]
        return requests.get(f"{base}/tables/{table}/data", params=params,
                            headers=headers, timeout=120)
    if fmt == "v1":
        query = [{"code": dim,
                  "selection": ({"filter": "all", "values": ["*"]} if value == "*"
                                else {"filter": "item", "values": value.split(",")})}
                 for dim, value in selection]
        return requests.post(f"{base}/{table}", headers=headers, timeout=120,
                             json={"query": query, "response": {"format": "json-stat2"}})
    if fmt == "statbank":
        params = [("lang", "en")] + selection
        return requests.get(f"{base}/data/{table}/JSONSTAT", params=params,
                            headers=headers, timeout=120)
    raise ValueError(f"unknown PxWeb format {fmt!r}")


def fetch(spec: dict, code: str, start: str | None = None) -> pd.DataFrame:
    """Fetch one series. `spec` is the publisher's row from the source list."""
    r = _request(spec, code)
    r.raise_for_status()
    df = parse_jsonstat(r.json())
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df
