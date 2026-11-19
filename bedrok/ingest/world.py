"""The world layer: IMF, BIS and OECD indicators for every country they cover.

Each family is one SDMX request with the country dimension left open, split
by country (sdmx.fetch_by), the way the Eurostat families split one download
by geography. Which countries each family carries is a seed
(seeds/world_geos.csv, written by scripts/world_geos.py): every country the
publisher serves with a current series, less those for which Bedrok already
carries the same measure from a closer source (`skip_held`): Germany's
inflation is HICP, not the BIS's copy of its CPI; Britain's policy rate is the
Bank of England's own.

Ids are publisher, indicator, country: `weo_gdp_growth_br`, `bis_policy_rate_kr`,
`oecd_10y_mx`. The country is Bedrok's code (geography.py), which is ISO 3166
alpha-2 in lower case except `uk` and `el`. A family id that a seed row
already answers (`bis_policy_rate_jp` is `jp_policy_rate`) is an alias
(core/aliases.py), and a seed row with the family's own id (`weo_gdp_growth_kz`,
with its own notes) stays a per-series row: neither is built here.

Its own module, data only, so that the generator script and the tests can read
it without the pipeline.
"""
from __future__ import annotations

import csv

from bedrok.core import config, geography

SEED = config.SEEDS_DIR / "world_geos.csv"

IMF_TERMS = "IMF terms: not to be used to train large language models without permission. "
WEO = ("{what}, from the latest IMF World Economic Outlook. Points after the current "
       "year are IMF FORECASTS, revised each April and October, and dated in the future")
BIS_NOTE = ("Compiled by the BIS from national sources, whose own terms may be tighter; "
            "in a commercial product BIS statistics must not add to its price")

# IMF aggregates kept, and what Bedrok calls them.
WEO_AGGREGATES = {"G001": "world", "G163": "ea"}


def _weo(prefix, indicator, label, short, what, units):
    return {"prefix": prefix, "source": "imf", "by": "COUNTRY", "codes": "iso3",
            "code": "IMF.RES,WEO,9.0.0/{}." + indicator + ".A", "label": label,
            "frequency": "annual", "units": units, "staleness_days": 450,
            "name_tmpl": "{geo} " + short + ", IMF World Economic Outlook",
            "notes": IMF_TERMS + WEO.format(what=what[0].upper() + what[1:]),
            "aggregates": WEO_AGGREGATES, "full": True}


WORLD_SETS: list[dict] = [
    _weo("weo_gdp_growth_", "NGDP_RPCH", "IMF WEO real GDP growth", "real GDP growth",
         "real GDP growth, percent change on the previous year", "percent"),
    _weo("weo_inflation_", "PCPIPCH", "IMF WEO inflation", "consumer price inflation",
         "consumer price inflation, annual average, percent change", "percent"),
    _weo("weo_unemployment_", "LUR", "IMF WEO unemployment", "unemployment rate",
         "unemployment rate, percent of the labour force", "percent"),
    _weo("weo_gov_debt_", "GGXWDG_NGDP", "IMF WEO government debt",
         "general government gross debt, % of GDP",
         "general government gross debt, percent of GDP", "percent of GDP"),
    _weo("weo_gov_balance_", "GGXCNL_NGDP", "IMF WEO government balance",
         "government budget balance, % of GDP",
         "general government net lending or borrowing (the budget balance), percent "
         "of GDP; negative is a deficit", "percent of GDP"),
    _weo("weo_current_account_", "BCA_NGDPD", "IMF WEO current account",
         "current account balance, % of GDP",
         "current account balance, percent of GDP", "percent of GDP"),

    {"prefix": "bis_policy_rate_", "source": "bis", "by": "REF_AREA", "codes": "iso2",
     "code": "WS_CBPOL/M.{}", "label": "BIS central bank policy rates",
     "frequency": "monthly", "units": "percent", "staleness_days": 90,
     "name_tmpl": "{geo} central bank policy rate (BIS)",
     "notes": "BIS central bank policy rates; month-end value. The BIS mixes regime "
              "concepts across countries (target rate, corridor midpoint, key rate). "
              + BIS_NOTE,
     # The ECB's own rate answers the euro area; the Fed, Bank of England and
     # National Bank of Kazakhstan series their own countries.
     "skip_held": ["{cc}_policy_rate", "{cc}_fed_funds", "{cc}_boe_bank_rate", "{cc}_base_rate"],
     "skip_codes": ["ea"]},
    {"prefix": "bis_cpi_yoy_", "source": "bis", "by": "REF_AREA", "codes": "iso2",
     "code": "WS_LONG_CPI/M.{}.771", "label": "BIS consumer price inflation",
     "frequency": "monthly", "units": "percent", "staleness_days": 110,
     "name_tmpl": "{geo} consumer price inflation, annual rate (BIS)",
     "notes": "consumer prices, percent change on the same month a year earlier, from the "
              "BIS long series of national CPIs. " + BIS_NOTE,
     "skip_held": ["{cc}_cpi", "{cc}_cpi_yoy", "{cc}_hicp_yoy", "hicp_yoy_{cc}", "{cc}_cpif",
                   "{cc}_cpih_yoy", "{cc}_cpi_ate"],
     "skip_codes": ["ea"]},                   # euro-area HICP
    {"prefix": "bis_reer_", "source": "bis", "by": "REF_AREA", "codes": "iso2",
     "code": "WS_EER/M.R.B.{}", "label": "BIS real effective exchange rates",
     "frequency": "monthly", "units": "index 2020=100", "staleness_days": 90,
     "name_tmpl": "{geo} real broad effective exchange rate (BIS)",
     "notes": "CPI-deflated, broad basket (64 economies); a rise is a real appreciation. "
              + BIS_NOTE},
    {"prefix": "bis_real_house_prices_", "source": "bis", "by": "REF_AREA", "codes": "iso2",
     "code": "WS_SPP/Q.{}.R.628", "label": "BIS real house prices",
     "frequency": "quarterly", "units": "index 2010=100", "staleness_days": 330,
     "name_tmpl": "{geo} real residential property prices (BIS)",
     "notes": "CPI-deflated residential property prices compiled by the BIS from national "
              "sources. " + BIS_NOTE},
    {"prefix": "bis_credit_gdp_", "source": "bis", "by": "BORROWERS_CTY", "codes": "iso2",
     "code": "WS_TC/Q.{}.P.A.M.770.A", "label": "BIS credit to the private sector",
     "frequency": "quarterly", "units": "percent of GDP", "staleness_days": 330,
     "name_tmpl": "{geo} credit to the private non-financial sector, % of GDP (BIS)",
     "notes": "total credit to the private non-financial sector from all lenders, at market "
              "value, as a share of GDP; adjusted for breaks. " + BIS_NOTE},
    {"prefix": "bis_credit_gap_", "source": "bis", "by": "BORROWERS_CTY", "codes": "iso2",
     "code": "WS_CREDIT_GAP/Q.{}.P.A.C", "label": "BIS credit-to-GDP gaps",
     "frequency": "quarterly", "units": "percentage points of GDP", "staleness_days": 330,
     "name_tmpl": "{geo} credit-to-GDP gap (BIS)",
     "notes": "private non-financial credit to GDP minus its one-sided HP trend (lambda "
              "400,000), the Basel III countercyclical-buffer guide; publishes about two "
              "quarters behind. " + BIS_NOTE},

    {"prefix": "oecd_unemployment_", "source": "oecd", "by": "REF_AREA", "codes": "iso3",
     "code": "OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0/{}.UNE_LF_M.PT_LF_SUB._Z.Y._T.Y_GE15._Z.M",
     "label": "OECD unemployment rates", "frequency": "monthly",
     "units": "percent", "staleness_days": 110,
     "name_tmpl": "{geo} unemployment rate (OECD)",
     "notes": "harmonised unemployment rate, aged 15 and over, seasonally adjusted, monthly "
              "(OECD Infra-annual labour statistics)",
     "skip_held": ["{cc}_unemployment", "{cc}_unemployment_rate", "{cc}_unemployment_insee",
                   "{cc}_unemployment_epa"]},
    {"prefix": "oecd_10y_", "source": "oecd", "by": "REF_AREA", "codes": "iso3",
     "code": "OECD.SDD.STES,DSD_STES@DF_FINMARK,4.0/{}.M.IRLT.PA._Z._Z._Z._Z.N",
     "label": "OECD long-term interest rates", "frequency": "monthly", "units": "percent",
     "staleness_days": 110,
     "name_tmpl": "{geo} long-term government bond yield (OECD)",
     "notes": "long-term interest rate, usually the 10-year government bond yield, monthly "
              "average (OECD Main Economic Indicators)",
     "skip_held": ["{cc}_10y", "{cc}_10y_yield", "ltgov_yield_{cc}", "{cc}_gilt_10y"]},
    {"prefix": "oecd_3m_", "source": "oecd", "by": "REF_AREA", "codes": "iso3",
     "code": "OECD.SDD.STES,DSD_STES@DF_FINMARK,4.0/{}.M.IR3TIB.PA._Z._Z._Z._Z.N",
     "label": "OECD short-term interest rates", "frequency": "monthly", "units": "percent",
     "staleness_days": 110,
     "name_tmpl": "{geo} 3-month interest rate (OECD)",
     "notes": "short-term interest rate, usually the 3-month interbank rate, monthly average "
              "(OECD Main Economic Indicators)",
     "skip_held": ["{cc}_3m"]},
    {"prefix": "oecd_gdp_qoq_", "source": "oecd", "by": "REF_AREA", "codes": "iso3",
     "code": "OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA_EXPENDITURE_GROWTH_OECD,1.1/"
             "Q.Y.{}.S1.S1.B1GQ._Z._Z._Z.PC.L.G1.T0102",
     "label": "OECD real GDP growth, quarterly", "frequency": "quarterly",
     "units": "percent change on previous quarter", "staleness_days": 260,
     "name_tmpl": "{geo} real GDP growth on the previous quarter (OECD)",
     "notes": "real GDP, chain-linked volumes, seasonally adjusted, percent change on the "
              "previous quarter (OECD Quarterly National Accounts)",
     "skip_held": ["{cc}_gdp_qoq", "gdp_qoq_{cc}", "{cc}_gdp_growth_saar"]},
]

PREFIXES = tuple(s["prefix"] for s in WORLD_SETS)


def to_code(spec: dict, area: str) -> str | None:
    """A publisher's country code → Bedrok's, or None for one Bedrok does not
    place (a regional aggregate, an unknown code)."""
    if area in spec.get("aggregates", {}):
        return spec["aggregates"][area]
    if spec["codes"] == "iso3":
        return geography.ISO3.get(area)
    if area == "XM":
        return "ea"
    cc = {"GB": "uk", "GR": "el"}.get(area, area.lower())
    return cc if cc in geography.CODES or cc in geography.FAMILY_CODES else None


def load() -> dict[str, list[str]]:
    """Family prefix → its publisher country codes, in seed order."""
    out: dict[str, list[str]] = {}
    with SEED.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.setdefault(row["prefix"], []).append(row["area"])
    return out


AREAS = load() if SEED.exists() else {}
