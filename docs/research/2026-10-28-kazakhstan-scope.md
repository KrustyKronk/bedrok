# Kazakhstan: what it would take to carry it

Scoped 2026-10-28, after "does Bedrok have comprehensive data on Kazakhstan?"
got the answer no: zero series, three warnings (crude versus condensate, mirror
trade with Russia, remittances from Russia). Every endpoint below was probed
live on 2026-10-28 unless it says otherwise. Nothing here is built; the open
item is in `STATE.md`.

## The premise: what "comprehensive" should mean

Bedrok's best-covered smaller economies (Canada, Spain, Sweden) carry
headline inflation, GDP, the labour market, the policy rate, a government
yield, the exchange rate, house prices and the external accounts, each from
the national publisher, plus the international panels. For Kazakhstan the
questions an analyst would ask are a little different, and the set should be
built around them:

- **Inflation and the policy rate.** The National Bank targets inflation and
  floats the tenge (since August 2015). CPI and the base rate are the first
  two series anyone asks for.
- **The tenge.** KZT per USD daily, and its real effective rate.
- **Oil.** Crude and condensate output separately, because the OPEC+ quota is
  set on crude only (the existing `kz_crude_condensate` warning).
- **The external side and the National Fund.** Reserves, the National Fund's
  assets, the current account, external debt.
- **Growth and the labour market.** Quarterly GDP, the monthly short-term
  economic indicator, unemployment, real wages.
- **Fiscal.** Government debt and the budget, including transfers from the
  National Fund.

A set of about 30 to 40 series covering these would put Kazakhstan level with
Bedrok's mid-sized countries. "Comprehensive" beyond that (regional data,
detailed trade, banking-sector balance sheets) is not what Bedrok carries for
any country.

## International publishers: most of the set, with no new code

Bedrok already speaks SDMX to the IMF and the BIS, and already reads
Eurostat. For these, Kazakhstan is catalog rows only.

### IMF (SDMX 2.1, already wired)

Probed every relevant dataflow with `COUNTRY=KAZ`:

| dataflow | what Kazakhstan has | latest held 2026-10-28 | verdict |
|---|---|---|---|
| `CPI` 5.0.0 | headline index and y/y, monthly from 2000, plus COICOP divisions | 2026-M09 (9.2% y/y) | **use** |
| `ER` 4.0.1 | KZT per USD and per EUR, period average and end of period, monthly | 2026-M09 | **use** (monthly only) |
| `IL` 13.0.1 | reserves with and without gold, FX, gold in ounces, monthly | 2026-M09 | **use** |
| `IRFCL` 12.0.0 | the full reserves template, monthly | 2026-M08 | optional |
| `MFS_MA` 10.0.1 | broad money and components, monthly | 2026-M07 | check: several components are reported as 0 |
| `MFS_CBS` 24.0.0 | central bank survey, monthly | 2026-M08 | optional |
| `BOP` 21.0.0 / `IIP` 13.0.0 | full BPM6 accounts and positions, quarterly | 2026-Q2 | **use** (current account, net IIP) |
| `QGFS` 12.0.0 | general government, quarterly | 2025-Q4 | **use** for the fiscal balance |
| `FSIBSIS` 18.0.0 | banking soundness, quarterly | 2025-Q3 | optional |
| `WEO` 9.0.0 | 145 annual indicators with forecasts to 2031 | 2025 actual | **use**, like the existing `weo_*` rows |
| `MCDREO` 8.0.0 | the regional outlook's 16 indicators, incl. National Fund assets | 2025 actual | **use** for the National Fund ratio |
| `GDD` 2.0.0 | public and private debt to GDP, annual | 2024 | optional |
| `MFS_IR` 9.0.0 | policy and money-market rates | **stops 2020-M12** | do not use |
| `QNEA` 7.0.0 | quarterly GDP | **stops 2024-Q1** | do not use; take GDP nationally |
| `LS` 9.0.0 | labour force, unemployment | **stops 2022** | do not use |
| `PPI` 3.0.0 / `PI` 2.0.0 | producer prices, industrial production | **stop 2017** | do not use |

Licence: as for the existing IMF rows (`attribution`, the no-LLM-training
clause stated with the series). Periods are spelt `2026-M09` on the 2.1 path,
as the engineering notes say.

### BIS (SDMX, already wired)

Kazakhstan is absent from the policy-rate, effective-exchange-rate, property
price, total credit and credit-gap datasets (all 404). It is present in:

- `WS_XRU/M.KZ.KZT.A`, KZT per USD monthly from 2000 to 2026-08. It duplicates
  the IMF's `ER`; carry one, not both.
- `WS_DEBT_SEC2_PUB`, debt securities outstanding, quarterly to 2026-Q2.

The BIS adds little. Its absence from `WS_CBPOL` means the policy rate must
come from the National Bank.

### Eurostat (already wired): the mirror-trade check

`ext_st_eu27_2020sitc?partner=KZ&geo=EU27_2020&sitc06=TOTAL&indic_et=TRD_VAL&stk_flow=EXP&freq=M`
answers, monthly to 2026-08. This is exactly the cross-check the
`cca_mirror_trade_russia_sanctions` warning names, so carrying EU exports to
Kazakhstan (and to Armenia and the Kyrgyz Republic) lets the warning point at
a Bedrok series. Imports from Kazakhstan are the same row with `stk_flow=IMP`.

### World Bank indicators (new adapter, small)

The World Development Indicators API (`api.worldbank.org/v2/country/KAZ/indicator/…`)
answers JSON with no key, annual from the early 1990s, last updated
2026-10-08: GDP growth and inflation to 2025, government debt to 2023,
external debt to 2024, oil rents to 2021. Licence CC BY 4.0. Bedrok's
`worldbank` source is the Pink Sheet bulk file only, so this needs a second
small adapter (one GET per series, paged JSON). It is the longest annual
history available and is worth having for every country, not just this one;
that makes it a decision of its own.

### JODI oil (new adapter, small)

JODI's primary oil CSVs (`jodidata.org/_resources/files/downloads/oil-data/annual-csv/primary/<year>.csv`)
give Kazakhstan's crude (`CRUDEOIL`) and condensate (`NGL`) production
separately, in thousand barrels a day, monthly to 2026-07. For June 2026: crude
1,859 and NGL 351. That is the split the `kz_crude_condensate` warning says
headline figures blur. Every Kazakh row carries assessment code 3, which in
JODI's scheme means its comparability has not been assessed; say so in the
notes. The website's terms of use cover the site and its forum and say nothing
about reusing the data, so the licence class is **unresolved**: ask JODI or
find a data-use statement before choosing `store`.

### Probed and of little use

- **ILOSTAT SDMX** (`sdmx.ilo.org`): Kazakhstan's quarterly unemployment stops
  in 2019.
- **ADB Key Indicators**: no SDMX endpoint at the path tried (404).
- **EIA international**: the API needs Bedrok's existing EIA key, which this
  probe did not use. Not verified. JODI covers the same ground monthly.
- **OECD**: Kazakhstan is not a member and is not in the CLI or confidence
  flows Bedrok carries.

## National publishers

None of them speaks SDMX or PxWeb (`/sdmx` paths on stat.gov.kz,
nationalbank.kz and data.nationalbank.kz all 404 or 401). The two that matter
are key-free, fast, reusable with credit, and need one custom adapter each.

### National Bank of Kazakhstan: the first adapter

Terms (`nationalbank.kz/en/page/data-usage-terms`): free reuse with reference
to the source, software included, so `attribution`, `store`. No Cloudflare;
answers in under two seconds. Two interfaces, and an adapter wants both:

- **The statistics pages' own JSON.** Each table page has a `/records` path
  that returns its full history in one GET, and `/excel` for XLSX. Verified:
  `/en/international-reserve-and-asset/mezhdunarodnye-rezervy-i-aktivy-nacionalnogo-fonda-rk/records`
  returns 394 monthly rows from 1993-12, with gross and net reserves,
  monetary gold and the **National Fund's FX assets**. The same shape serves
  the money base and M0 to M3 (from 1994), deposit rates (1996), deposits
  (1997), the monetary survey (1997) and the interbank market (1997). Field
  names are terse (`m_b_r_m`, `n_i_r`) and need a mapping. The day in
  `reporting_date` is unreliable in early years (`1994-01-08`), so key on the
  month.
- **The open-data API** (`data.nationalbank.kz/api/v1`, "no keys, no
  registration", JSON, `Accept-Language: en`). Verified:
  `/data/base-rate?from=1990-01-01&to=…` returns 92 decisions from
  2015-09-02, with the corridor, the latest 15.75% effective 2026-10-27. It
  lists about 300 tables (BoP, external debt, IIP, REER, inflation
  expectations, banking), but it is a beta and most tables start only in 2018
  to 2023. REER and NEER (from 1995) and inflation expectations (from 2016)
  are the exceptions worth taking from it.
- **Official KZT rates.** Daily XML at
  `nationalbank.kz/rss/get_rates.cfm?fdate=DD.MM.YYYY` (from about 1999-12),
  and the whole USD history from 1999-10-19 as one HTML table from the rates
  `/report` page. Each rate is published the day before the date it applies
  to, so the latest row is dated tomorrow.
- **Release dates.** Every statistics page prints "Published on" and "Next"
  dates, and the release schedule exports to XLSX. That is the publisher's
  own date, which `release_rules` wants.

### Bureau of National Statistics: the second adapter

Terms (on the Taldau pages): reuse freely and free of charge, with reference
to the source. `attribution`, `store`.

- **The Taldau API** (`taldau.stat.gov.kz/en/Api/…`, documented at
  `/en/Api/Dev`, no key). Search returns an index id, then `GetIndexAttributes`,
  `GetSegmentList` and `GetPeriodList` give the measure, dimension terms and
  period id the data call needs. Verified: `GetIndexTreeData` for index
  703076 returns monthly CPI against the same month a year earlier, 189
  months from 2011-01 to 2026-09 (109.3, so 9.3% y/y; the IMF's own
  computation gives 9.24%). Keys look like `"y092026"`.
- **Gotchas the adapter must carry.** A wrong `p_measure_id` returns an empty
  body, not an error. The comparison base (previous month, same month a year
  earlier, December) is a separate term id, so each is its own catalog row.
  **Quarterly GDP is published year to date only**: nominal levels can be
  differenced into quarters, real growth rates cannot. Depth is short: CPI
  from 2011, monthly unemployment from 2013 with gaps.
- **Also there, ids found but not pulled:** core CPI (703082), wages,
  retail, industrial output, housing price indices, and "oil, including gas
  condensate" (index 701622, combined only).
- **Dynamic tables** (`stat.gov.kz/api/iblock/element/{id}/csv/file/en/`)
  are stable links, but annual files get new ids, so they suit one-off
  tables rather than a series.
- **Release calendar**: HTML by date, names but no times
  (`stat.gov.kz/en/release-calendar/?date=…`).

### Weak fits and licensed

- **Ministry of Finance.** `minfin.gov.kz` timed out repeatedly; the content
  is on `gov.kz/org/minfin` as per-release XLSX documents with hashed links.
  No series URL. The IMF's `QGFS` and the WEO are the practical fiscal source.
- **Ministry of Energy.** News and legal documents only. No machine-readable
  crude and condensate split was found anywhere official; the Bureau
  publishes them combined. JODI is the only source of the split.
- **KASE.** An XLSX archive answers without a key (daily TONIA from
  2001-09-02, and TWINA, TRION, SWAP-1D, a government-bond yield index), but
  the footer says copying needs written permission. `personal_use` at best;
  better left to `gaps.md` as the Central Asian yields already are.
- **data.egov.kz** needs an eGov account key and mostly duplicates the
  Bureau and the National Bank. Skip.
- **The National Fund** (`nationalfund.kz` 403) and the regulator's site
  (does not resolve): their numbers are on the National Bank's pages.

## What it would take, in order

Bedrok fetches each series from the publisher that produces it, and the
national publishers here are open, so they come first. The IMF fills what the
national APIs do not give as a clean series.

1. **K1, the National Bank adapter.** About 12 series: the base rate (a
   step series from 2015), KZT per USD daily (1999), gross reserves and the
   National Fund's FX assets monthly (1993), M2 and M3 (1994), the weighted
   deposit rate (1996), the REER (1995), households' inflation expectations
   (2016). Release dates from the pages' "Next" line. Also the KZT row for
   currency conversion. One session.
2. **K2, the Taldau adapter.** About 12 series: CPI headline and core y/y and
   m/m (2011), GDP (annual from 2000; quarterly nominal by differencing the
   year-to-date levels, with that stated in the notes), unemployment, real
   wages, industrial output, oil and condensate combined, the housing price
   index. Release dates from the HTML calendar. One session; the parameter
   discovery is the work.
3. **K3, catalog rows only.** About 12 series on clients Bedrok already has:
   the IMF's current account and net IIP (quarterly, USD), the general
   government balance (`QGFS`), the WEO's GDP growth, inflation and debt
   with forecasts (as the existing `weo_*` rows), the regional outlook's
   National Fund ratio, and Eurostat's EU exports to Kazakhstan, Armenia and
   the Kyrgyz Republic for the mirror-trade warning. Half a session. It can
   go first if a quick result matters more than the order above.
4. **K4, JODI crude and condensate,** once its licence is settled. A small
   bulk adapter (yearly CSVs). It would let `kz_crude_condensate` point at a
   series.

Not proposed: the World Bank indicators adapter (worth deciding for every
country at once, not for this one), KASE (licensed), the IMF's interest-rate,
quarterly GDP, labour and producer-price flows (stale for Kazakhstan), and
pre-2011 CPI from the IMF (a splice by returns, rule 6, only if someone needs
the 2000s).

With K1 to K3, Kazakhstan has about 35 series, which matches Bedrok's
mid-sized countries.

## Changes outside the catalog

- **Geography.** Add `kz` to `bedrok/core/geography.py`: `CODES`
  ("Kazakhstan") and `NAMES` ("kazakhstan", "kazakh", "kazakhstani"). The
  names test then requires every `kz_` row's name to begin "Kazakhstan".
- **Currency.** Rule 5: currency is a column. Values in tenge carry `KZT` in
  `units`; the IMF's reserves and BOP are in USD. Bedrok's `fx_rates` come from
  the ECB, which publishes no KZT reference rate, so converting tenge to euros
  needs a KZT rate of its own (the National Bank's official rate, or the IMF's
  monthly `ER` series in KZT per EUR). Do not cross through USD silently.
- **Breaks to write as warnings or caveats.** The August 2015 move to a
  floating tenge (a level break in KZT per USD and a spike in CPI). The base
  rate series starts with the float, so earlier policy rates (the
  refinancing rate) are a different instrument and are not spliced on.
  Quarterly GDP being year to date is a caveat on every row built from it.
  Whether any tax or methodology change since 2025 affects CPI comparability
  is background still to check.
- **Gaps.** `bedrok/seeds/gaps.md` already lists Central Asian sovereign
  yields as licensed by index providers and exchanges.
