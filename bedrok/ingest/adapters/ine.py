"""INE (Spain) Tempus3 adapter (no API key).

`source_code` is an INE series code, e.g. "IPC290751" (CPI, general index):

  https://servicios.ine.es/wstempus/js/ES/DATOS_SERIE/{code}?nult=5000

`nult` asks for the last N periods; 5000 is the whole history.

Periods are read from `Anyo` and `FK_Periodo` (1–12 months, 19–22 quarters,
28 the year), never from `Fecha`: that is a millisecond timestamp of local
midnight in Madrid, which in UTC falls on the previous day.

INE retires a series code when it rebases. The CPI code IPC251852 stopped at
2025-12 when the 2025 base arrived, and the EPA unemployment code EPA815 at
2023-Q4. A stale code still answers 200, so only its last date shows it (smoke
and validate).

Licence: INE's legal notice (https://www.ine.es/aviso_legal) puts its
statistical information under CC BY 4.0. The site footer's CC BY-SA 4.0 covers
the website and its own content (press-release text, charts), not the numbers.
"""
from __future__ import annotations

import pandas as pd
import requests

BASE = "https://servicios.ine.es/wstempus/js/ES"
QUARTERS = {19: 1, 20: 4, 21: 7, 22: 10}


def fetch(source_code: str, start: str | None = None) -> pd.DataFrame:
    r = requests.get(f"{BASE}/DATOS_SERIE/{source_code}", params={"nult": 5000}, timeout=60)
    r.raise_for_status()
    df = _parse(r.json())
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df


def _period_start(year, period):
    if not year or period is None:
        return None
    period = int(period)
    if 1 <= period <= 12:
        month = period
    elif period in QUARTERS:
        month = QUARTERS[period]
    elif period == 28:
        month = 1
    else:
        return None
    return pd.Timestamp(int(year), month, 1).date()


def _parse(js: dict) -> pd.DataFrame:
    """DATOS_SERIE JSON → [date, value]. Observations INE marks secret are dropped."""
    rows = [(_period_start(o.get("Anyo"), o.get("FK_Periodo")), o.get("Valor"))
            for o in js.get("Data", []) if not o.get("Secreto")]
    df = pd.DataFrame(rows, columns=["date", "value"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return (df.dropna(subset=["date", "value"])
              .drop_duplicates(subset="date", keep="last")
              .sort_values("date").reset_index(drop=True))
