"""Shared helpers for the data adapters.

Kept deliberately small: a browser-shaped User-Agent, the period parser the
SDMX-family and PxWeb-family sources all need, and the JSON-stat reader the
PxWeb family answers in.
"""
from __future__ import annotations

import pandas as pd

# Several statistical publishers reject or throttle default HTTP-client agents:
# data-api.ecb.europa.eu returns 503, and newyorkfed.org / bankofengland.co.uk
# return 403 or an empty body. None of this is a paywall or a bot-defence being
# evaded — the same files are freely downloadable from a browser; the servers
# simply expect a UA string.
BROWSER_UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36 Bedrok/0.1"),
}


def parse_period(s: pd.Series) -> pd.Series:
    """SDMX TIME_PERIOD → period-START timestamps (NaT where unreadable).

    Publishers spell the same period differently, so every form seen live is
    accepted: daily 2026-10-08, monthly 2026-10 or 2026-M10 (IMF), quarterly
    2026-Q3, half-yearly 2026-S2, annual 2026. Quarterly labels become the
    quarter-START timestamp, matching how the store dates every other
    period-labelled series."""
    txt = s.astype(str).str.strip()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")

    def take(mask, values):
        out.loc[mask] = pd.to_datetime(values[mask], errors="coerce")

    q = txt.str.extract(r"^(\d{4})-?[QK]([1-4])$")
    is_q = q[0].notna()
    take(is_q, q[0] + "-" + ((q[1].astype(float) - 1) * 3 + 1).map(
        lambda m: f"{int(m):02d}" if m == m else "") + "-01")
    h = txt.str.extract(r"^(\d{4})-?S([12])$")
    is_h = h[0].notna()
    take(is_h, h[0] + "-" + h[1].map({"1": "01", "2": "07"}) + "-01")
    mm = txt.str.extract(r"^(\d{4})-?M?(\d{2})$")
    is_m = mm[0].notna()
    take(is_m, mm[0] + "-" + mm[1] + "-01")
    is_a = txt.str.fullmatch(r"\d{4}")
    take(is_a, txt + "-01-01")
    d = txt.str.extract(r"^(\d{4}-\d{2}-\d{2})")
    is_d = d[0].notna() & ~(is_q | is_h | is_m | is_a)
    take(is_d, d[0])
    return out


class UnpinnedKey(ValueError):
    """The request matched more than one series."""


def parse_jsonstat(js: dict) -> pd.DataFrame:
    """Pure: one JSON-stat dataset (version 1.0 inside a "dataset" wrapper, or
    2.0 at the top level) → [date, value], dated at period start.

    Every dimension but time must hold exactly one value: a dataset that
    crosses two series is refused rather than flattened, the same rule the
    SDMX client keeps. Values may be a list or a sparse {index: value} dict,
    and missing observations arrive as null."""
    ds = js.get("dataset", js)
    dims = ds["dimension"]
    ids = ds.get("id") or dims["id"]
    sizes = ds.get("size") or dims["size"]
    role = ds.get("role") or dims.get("role") or {}
    time = (role.get("time") or [None])[0]
    if time is None:
        time = next((d for d in ids if d.lower() in ("tid", "time", "vuosi", "kuukausi")
                     or d.lower().startswith("timeperiod")), None)
    if time is None:
        raise ValueError(f"no time dimension among {ids}")
    wide = {d: n for d, n in zip(ids, sizes) if d != time and n != 1}
    if wide:
        raise UnpinnedKey(f"selection matches several series along {wide}: pin every dimension")
    index = dims[time]["category"]["index"]
    periods = (sorted(index, key=index.get) if isinstance(index, dict) else list(index))
    raw = ds["value"]
    values = ([raw.get(str(i)) for i in range(len(periods))] if isinstance(raw, dict)
              else list(raw))
    df = pd.DataFrame({"date": parse_period(pd.Series(periods)),
                       "value": pd.to_numeric(pd.Series(values, dtype=object), errors="coerce")})
    df = (df.dropna(subset=["date", "value"])
            .drop_duplicates(subset="date", keep="last")
            .sort_values("date").reset_index(drop=True))
    df["date"] = df["date"].dt.date
    return df
