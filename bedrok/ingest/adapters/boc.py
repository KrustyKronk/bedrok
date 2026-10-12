"""Bank of Canada Valet adapter (no API key).

`source_code` is a Valet series name, e.g. "V39079" (target for the overnight
rate) or "FXUSDCAD". One request returns the whole series:

  https://www.bankofcanada.ca/valet/observations/{name}/json[?start_date=…]

Observations arrive as {"d": "2026-10-08", "<name>": {"v": "2.25"}}; a day the
series has no value either omits the key or carries an empty string.

Terms: attribution to the Bank of Canada (https://www.bankofcanada.ca/terms/,
§1.1). §1.3 adds a notice duty only for paid services or documents for sale.
The terms also forbid getting round the Valet API's request limits, which the
source list's pacing respects.
"""
from __future__ import annotations

import pandas as pd
import requests

BASE = "https://www.bankofcanada.ca/valet"


def fetch(source_code: str, start: str | None = None) -> pd.DataFrame:
    params = {"start_date": str(start)[:10]} if start else {}
    r = requests.get(f"{BASE}/observations/{source_code}/json", params=params, timeout=60)
    r.raise_for_status()
    return _parse(r.json(), source_code)


def _parse(js: dict, name: str) -> pd.DataFrame:
    """Valet observations JSON → [date, value] for one series."""
    rows = [(o.get("d"), (o.get(name) or {}).get("v")) for o in js.get("observations", [])]
    df = pd.DataFrame(rows, columns=["date", "value"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = (df.dropna(subset=["date", "value"])
            .drop_duplicates(subset="date", keep="last")
            .sort_values("date").reset_index(drop=True))
    df["date"] = df["date"].dt.date
    return df
