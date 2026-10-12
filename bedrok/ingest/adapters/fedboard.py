"""Federal Reserve Board release files: one SDMX-XML zip per statistical release.
No key.

  https://www.federalreserve.gov/releases/{rel}/data/FRB_{REL}_xml.zip

`source_code` is "<REL>:<SERIES_NAME>[:<scale>]", e.g.

  H15:RIFLGFCY10_N.B            10-year Treasury constant maturity, percent
  H8:B1001NCBA:billions         bank credit, all commercial banks, billions USD

The Board's own crosswalk from its series names to FRED ids is at
federalreserve.gov/data/documents/DDP-FRED%20Data%20Series%20Crosswalk.csv.

Why these files and not the Data Download Program: the Board is retiring the
DDP ("Build Your Package" goes the week of 2026-11-09, the pre-built packages
later) and points users at FRED; its notice says historical XML files stay on
the release pages, and these are those files. `release_status` lets `smoke`
watch every one of them. SLOOS alone has no release page: if its zip goes,
`fetch` reads the survey's chart-data HTML table instead (SLOOS_HTML maps the
four series to a panel and column, tied out against the stored history on
2026-10-12). The re-point recipes for the day another zip goes are in
docs/engineering/notes.md: Treasury's par and real curves for the yields
(from 1990 and 2003), the ECB cross for GBPUSD.

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
import html
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from email.utils import parsedate_to_datetime

import pandas as pd
import requests

from bedrok.ingest.adapters._util import BROWSER_UA

URL = "https://www.federalreserve.gov/releases/{rel}/data/FRB_{REL}_xml.zip"
SLOOS_INDEX = "https://www.federalreserve.gov/data/sloos.htm"
SLOOS_CHART_DATA = "https://www.federalreserve.gov/data/sloos/sloos-{ym}-chart-data.htm"
# SLOOS series name → (words of the panel heading, the column under it). The
# chart-data page carries every survey since 1990 in HTML tables whose headers
# span columns; both parts are matched on text, never on position.
SLOOS_HTML = {
    "SUBLPDCILS_N.Q": ("tightening standards for c&i loans", "large and medium"),
    "SUBLPDCISS_N.Q": ("tightening standards for c&i loans", "small"),
    "SUBLPDCILD_N.Q": ("stronger demand for c&i loans", "large and medium"),
    "SUBLPDCLCS_N.Q": ("tightening standards on consumer loans", "credit cards"),
}
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


@functools.lru_cache(maxsize=8)        # one slot per release the catalog uses
def _download(rel: str) -> bytes:
    r = requests.get(URL.format(rel=rel.lower(), REL=rel.upper()),
                     headers=BROWSER_UA, timeout=300)
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        raise ValueError(f"the {rel} release zip is gone: the Board answered "
                         f"{r.headers.get('content-type', 'something else')} instead of a zip")
    return r.content


def release_status(rel: str) -> pd.DataFrame:
    """Is the release zip still there? One HEAD (a ranged GET if HEAD is
    refused), no download. Returns DataFrame[date, value]: the file's
    Last-Modified date and its size in bytes, so `smoke` prints a freshness a
    person can read. Raises when the zip is missing or an HTML page answers in
    its place, so the liveness run FAILs the week a release moves."""
    url = URL.format(rel=rel.lower(), REL=rel.upper())
    r = requests.head(url, headers=BROWSER_UA, timeout=60, allow_redirects=True)
    if r.status_code in (403, 405):
        r = requests.get(url, headers={**BROWSER_UA, "Range": "bytes=0-0"}, timeout=60)
    r.raise_for_status()
    kind = r.headers.get("content-type", "")
    if "zip" not in kind and "octet-stream" not in kind:
        raise ValueError(f"the {rel} release zip is gone: the Board answered {kind or 'no type'}")
    when = r.headers.get("last-modified")
    size = r.headers.get("content-range", "").rpartition("/")[-1] or r.headers.get("content-length")
    return pd.DataFrame({"date": [parsedate_to_datetime(when).date() if when else date.today()],
                         "value": [float(size or 0)]})


def _release(rel: str, names: frozenset[str]) -> dict:
    key = (rel, names)
    if key not in _parsed:
        with zipfile.ZipFile(io.BytesIO(_download(rel))) as z:
            with z.open(f"{rel.upper()}_data.xml") as f:
                _parsed[key] = parse(f, names)
    return _parsed[key]


def _cells(row_html: str) -> list[tuple[str, int, int]]:
    """(text, colspan, rowspan) for each cell of one table row."""
    out = []
    for m in re.finditer(r"<t([hd])([^>]*)>(.*?)</t\1>", row_html, flags=re.S):
        attrs, inner = m.group(2), m.group(3)
        span = re.search(r'colspan="(\d+)"', attrs)
        rows = re.search(r'rowspan="(\d+)"', attrs)
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", inner)).split()).lower()
        out.append((text, int(span.group(1)) if span else 1, int(rows.group(1)) if rows else 1))
    return out


def parse_sloos_html(text: str, name: str) -> pd.DataFrame:
    """Pure: the survey's chart-data page → DataFrame[date, value] for one
    SLOOS series, dated to the quarter's first day like the zip. Headers are
    matched on their words: the panel heading spans its columns, and the
    sub-heading names the column."""
    panel_words, column = SLOOS_HTML[name]
    for table in re.findall(r"<table.*?</table>", text, flags=re.S):
        rows = re.findall(r"<tr.*?</tr>", table, flags=re.S)
        if len(rows) < 3:
            continue
        top, sub = _cells(rows[0]), _cells(rows[1])
        # Lay the two header rows out column by column: a top cell with
        # rowspan 2 is its own column; one with a colspan covers that many
        # sub-cells.
        columns, sub_i = [], 0
        for head, span, rspan in top:
            if rspan > 1:
                columns.append((head, head))
                continue
            for _ in range(span):
                columns.append((head, sub[sub_i][0] if sub_i < len(sub) else ""))
                sub_i += 1
        hits = [i for i, (head, sub_head) in enumerate(columns)
                if panel_words in head and sub_head == column]
        if not hits:
            continue
        col = hits[0]
        out = []
        for row in rows[2:]:
            cells = [c[0] for c in _cells(row)]
            if col >= len(cells):
                continue
            m = re.fullmatch(r"(\d{4}):(\d)", cells[0])
            value = pd.to_numeric(cells[col], errors="coerce")
            if m and pd.notna(value):
                out.append((date(int(m.group(1)), 3 * (int(m.group(2)) - 1) + 1, 1), float(value)))
        return pd.DataFrame(out, columns=EMPTY).sort_values("date").reset_index(drop=True)
    raise ValueError(f"{name} not found in the SLOOS chart-data page")


def _sloos_chart_data() -> str:
    """The latest survey's chart-data page, found from the SLOOS index."""
    index = requests.get(SLOOS_INDEX, headers=BROWSER_UA, timeout=60)
    index.raise_for_status()
    stamps = sorted(set(re.findall(r"sloos-(\d{6})\.htm", index.text)))
    if not stamps:
        raise ValueError("no survey pages linked from the SLOOS index")
    page = requests.get(SLOOS_CHART_DATA.format(ym=stamps[-1]), headers=BROWSER_UA, timeout=60)
    page.raise_for_status()
    return page.text


def fetch(source_code: str, start: str | None = None) -> pd.DataFrame:
    parts = source_code.split(":")
    rel, name = parts[0], parts[1]
    scale = parts[2] if len(parts) > 2 else None
    names = _primed.get(rel, frozenset())
    if name not in names:
        names = frozenset({name})
    try:
        found = _release(rel, names)
    except (requests.HTTPError, zipfile.BadZipFile, ValueError) as e:
        # SLOOS has no release page of its own, so its zip is the likeliest
        # to go when the Board retires the download program. The survey's
        # chart-data table carries the same numbers.
        if rel != "SLOOS" or name not in SLOOS_HTML:
            raise
        df = parse_sloos_html(_sloos_chart_data(), name)
        df.attrs["fallback"] = f"SLOOS zip unavailable ({type(e).__name__}); read the chart-data page"
    else:
        if name not in found:
            raise ValueError(f"{name} not found in the {rel} release file")
        df = to_frame(*found[name], scale=scale)
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df
