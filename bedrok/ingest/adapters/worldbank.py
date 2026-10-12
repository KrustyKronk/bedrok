"""World Bank "Pink Sheet" adapter — monthly commodity prices, ~70 commodities
back to 1960, nominal USD. CC-BY 4.0, reusable with attribution.

One bulk xlsx → many series (energy, metals, precious, grains, softs, fertilizers,
timber). Preferred over IMF PCPS (mid-migration API churn 2024–25). The download
URL embeds a content hash that changes on republish, so a landing-page fallback
scrape is used if the pinned URL 404s. Column labels carry '**' footnote marks
and stray whitespace, so matching is done on a normalised form.

Returns (per_slug: dict[slug -> DataFrame[date, value]], meta: dict[slug -> {name, units}]).
"""
from __future__ import annotations

import io
import re

import pandas as pd
import requests

# The workbook URL embeds a content hash that changes every monthly republish, so
# the current link is scraped from the landing page (primary, always fresh); this
# last-known-good URL is only the fallback if the scrape fails.
LANDING_URL = "https://www.worldbank.org/en/research/commodity-markets"
PINK_SHEET_URL = ("https://thedocs.worldbank.org/en/doc/"
                  "74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/"
                  "CMO-Historical-Data-Monthly.xlsx")
_UA = {"User-Agent": "Mozilla/5.0"}
CCBY = "World Bank Pink Sheet (Commodity Markets); monthly nominal USD; CC-BY 4.0"

SHEET = "Monthly Prices"
NAME_ROW, UNIT_ROW, DATA_START = 4, 5, 6

# slug -> clean column label (matched normalised: lowercased, '*' and spaces stripped).
SERIES = {
    # energy
    "wb_crude_avg": "Crude oil, average", "wb_crude_brent": "Crude oil, Brent",
    "wb_crude_wti": "Crude oil, WTI", "wb_crude_dubai": "Crude oil, Dubai",
    "wb_coal_au": "Coal, Australian", "wb_natgas_us": "Natural gas, US",
    "wb_natgas_eu": "Natural gas, Europe", "wb_lng_japan": "Liquefied natural gas, Japan",
    # base metals
    "wb_aluminum": "Aluminum", "wb_copper": "Copper", "wb_iron_ore": "Iron ore, cfr spot",
    "wb_lead": "Lead", "wb_tin": "Tin", "wb_nickel": "Nickel", "wb_zinc": "Zinc",
    # precious metals
    "wb_gold": "Gold", "wb_platinum": "Platinum", "wb_silver": "Silver",
    # grains & oilseeds
    "wb_wheat_hrw": "Wheat, US HRW", "wb_wheat_srw": "Wheat, US SRW", "wb_maize": "Maize",
    "wb_rice_thai5": "Rice, Thai 5%", "wb_barley": "Barley", "wb_sorghum": "Sorghum",
    "wb_soybeans": "Soybeans", "wb_soybean_oil": "Soybean oil", "wb_soybean_meal": "Soybean meal",
    "wb_palm_oil": "Palm oil", "wb_palm_kernel_oil": "Palm kernel oil",
    "wb_coconut_oil": "Coconut oil", "wb_rapeseed_oil": "Rapeseed oil",
    "wb_sunflower_oil": "Sunflower oil",
    # softs
    "wb_cocoa": "Cocoa", "wb_coffee_arabica": "Coffee, Arabica",
    "wb_coffee_robusta": "Coffee, Robusta", "wb_tea_avg": "Tea, avg 3 auctions",
    "wb_sugar_world": "Sugar, world", "wb_cotton": "Cotton, A Index",
    "wb_rubber_tsr20": "Rubber, TSR20", "wb_tobacco": "Tobacco, US import u.v.",
    # livestock & other food
    "wb_beef": "Beef", "wb_chicken": "Chicken", "wb_shrimp": "Shrimps, Mexican",
    "wb_banana_us": "Banana, US", "wb_orange": "Orange",
    # fertilizers
    "wb_dap": "DAP", "wb_urea": "Urea", "wb_potash": "Potassium chloride",
    "wb_phosphate_rock": "Phosphate rock", "wb_tsp": "TSP",
    # timber
    "wb_sawnwood_malay": "Sawnwood, Malaysian", "wb_plywood": "Plywood",
}


def _norm(s) -> str:
    return str(s).replace("*", "").strip().lower()


def _parse_workbook(content: bytes) -> tuple[dict, dict]:
    raw = pd.read_excel(io.BytesIO(content), sheet_name=SHEET, header=None)
    names = raw.iloc[NAME_ROW].tolist()
    units = raw.iloc[UNIT_ROW].tolist()
    periods = raw.iloc[DATA_START:, 0].astype(str).str.strip()
    # Loud shape assertion: periods look like '1960M01' and the header carries oil.
    assert re.match(r"^\d{4}M\d{2}$", periods.iloc[0]), \
        f"Pink Sheet layout changed: period {periods.iloc[0]!r} not YYYYMdd"
    assert any(_norm(n) == "crude oil, brent" for n in names if isinstance(n, str)), \
        "Pink Sheet layout changed: 'Crude oil, Brent' column not found on NAME_ROW"

    col_by_norm = {_norm(n): i for i, n in enumerate(names) if isinstance(n, str) and n.strip()}
    per = pd.to_datetime(periods, format="%YM%m", errors="coerce")

    out, meta = {}, {}
    for slug, label in SERIES.items():
        idx = col_by_norm.get(_norm(label))
        if idx is None:
            continue  # column dropped upstream — skip rather than crash the run
        vals = pd.to_numeric(raw.iloc[DATA_START:, idx], errors="coerce")
        df = pd.DataFrame({"date": per.dt.date.values, "value": vals.values})
        df = df.dropna(subset=["date", "value"]).reset_index(drop=True)
        if df.empty:
            continue
        out[slug] = df
        unit = str(units[idx]).strip() if idx < len(units) else ""
        meta[slug] = {"name": label, "units": unit}
    return out, meta


def _current_url() -> str | None:
    """Scrape the landing page for the current monthly workbook link."""
    try:
        html = requests.get(LANDING_URL, timeout=60, headers=_UA).text
        m = re.search(r'https://[^"\']+CMO-Historical-Data-Monthly\.xlsx', html)
        return m.group(0) if m else None
    except Exception:
        return None


def _download() -> bytes:
    # Prefer the scraped current URL (fresh); fall back to the pinned last-known-good.
    url = _current_url() or PINK_SHEET_URL
    r = requests.get(url, timeout=120, headers=_UA)
    r.raise_for_status()
    return r.content


def fetch_pink_sheet(start: str | None = None) -> tuple[dict, dict]:
    per_slug, meta = _parse_workbook(_download())
    if start:
        cut = pd.to_datetime(start).date()
        per_slug = {k: v[v["date"] >= cut].reset_index(drop=True)
                    for k, v in per_slug.items()}
        per_slug = {k: v for k, v in per_slug.items() if not v.empty}
    return per_slug, meta
