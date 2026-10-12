# Data licensing

The Bedrok **code** is MIT. The **data** is not. Every series keeps its
publisher's terms. Two catalog columns record them:

- `license` — what you may do with a number you hold, and what to say when you
  quote it.
- `cache` — whether your store may hold it at all: `store`, or `session`
  (fetched for the command or conversation, never written).

Since session 2 of the rethink Bedrok redistributes nothing: there is no shared
pack and nothing is hosted. Each install fetches every series from its
publisher, with its own keys, into its own store. That removes the question
"may Bedrok pass this on?" and leaves two narrower ones, which the two columns
answer.

## The five licence classes

| class | what it means | what you must do |
|---|---|---|
| `public_domain` | A US federal work, taken from the agency that produced it (BLS, BEA, Census, the Fed Board, FHFA, EIA, CFTC). | Nothing. Citation is polite; FHFA asks for its notice. |
| `attribution` | Reuse with credit: ECB, Eurostat, DG ECFIN, World Bank, OECD, ONS, Bank of England, the NY Fed's files and Markets API, NBER's business-cycle dates, the BIS, the IMF, Istat, INSEE, the ABS. | Carry the source's attribution line when you publish anything built on it. The BIS and IMF add conditions — see below. |
| `noncommercial` | Reuse with credit, not for gain: the Chicago, Cleveland, Dallas and Atlanta Feds' files. | Credit them when you quote; never use the numbers for gain without their permission. |
| `attribution_fred_channel` | Pulled through the FRED API. See below. | Carry the FRED notice; never store it. |
| `personal_use` | The publisher forbids redistribution or reserves all rights: Moody's, ICE, the University of Michigan (all via FRED), the Philadelphia Fed. | Your own use only. Never pass the numbers on. |

`personal_use` was called `restricted_no_pack` until session 3a, after a pack
that no longer exists. The vocabulary and the verbatim attribution texts live
in `bedrok/core/licensing.py`.

## The cache column

`store` is the default. `session` means the publisher's terms do not allow
keeping the numbers, so sync never writes the row and deletes any history it
once held. `bedrok pctile` fetches a session series live, ranks it in memory
and says so; `bedrok query` cannot see one, because SQL runs over the store.
The MCP server (session 5) is where a conversation-lifetime copy really lives.

Nine series are `session` today, all served through FRED:
- the four `personal_use` series FRED carries for ICE, Moody's and Michigan;
- the St. Louis Fed's stress index, published only on FRED;
- VIX — Cboe's own terms require a licence for its files, so FRED's
  citation-required copy is the only route, and only for the conversation;
- the three weekly jobless-claims series — the Labor Department's own feeds
  (the r539 report and its open-data API) match FRED exactly but ran four
  weeks behind the Thursday release on 2026-10-10, too late for the most
  timely recession gauge there is (the owner's call, 2026-10-10).

## How it is checked

`bedrok validate` emits `LICENSE-UNREVIEWED` for any catalog row whose licence
is NULL or not in the vocabulary, and `SESSION-STORED` for any session row with
observations in the store. Personal-use series held in the store are listed as
information.

### Adding a series

Set `license` and `cache` in the seed CSV, and `license` in every runtime
`cat_rows` builder — about two thirds of the catalog is registered by code.
Runtime rows default to `cache = store`, which is right for everything Bedrok
builds itself. `licensing.backfill_source_defaults` fills NULL licences from a
per-source default map, but a source absent from that map stays NULL
deliberately: a newly wired-up source with undecided terms should show up in
`validate`, not quietly inherit someone else's terms.

A derived series is stored, so every input must be a `store` series —
otherwise a session series reaches the store through arithmetic.
`tests/test_derived.py` pins that.

## The restricted series

| series | owner | why | cache |
|---|---|---|---|
| `us_baa10y` | Moody's | Explicitly forbids reproduction, redistribution, or storage for redistribution. | session (FRED only) |
| `us_hy_oas`, `us_ig_oas` | ICE Data Indices | "Pre-approval required." FRED also clamps them to ~3 years of history. | session (FRED only) |
| `us_consumer_sentiment` | University of Michigan | Non-commercial use only without permission; FRED withholds the latest value. | session (FRED only) |
| `us_philly_fed` | Federal Reserve Bank of Philadelphia | "All rights reserved", with no reuse grant found. | store, from the Philadelphia Fed |

Each catalog row explains the restriction and names a substitute: `us_nfci`
for credit stress, `oecd_cci_usa` for consumer confidence,
`us_infl_exp_1y_clevfed` for inflation expectations, `us_empire_fed` and
`us_cfnai` for manufacturing.

Also deliberately absent: the ICE dollar index (DXY) is commercially licensed —
Bedrok carries the Fed Board's broad dollar index instead; Case-Shiller (S&P
DJI) and NAR existing-home sales are replaced by FHFA and Census series; ifo
and ZEW require prior written permission and are not carried at all. Equity and
ETF prices left the core in session 2.

## FRED

FRED's terms forbid storing or archiving FRED content "in any database", and
using FRED content "in connection with the development or training of any
software program or system or machine learning, including … large language
models". Both bind the user, whatever key is used, and both apply even to
series that are public domain at their source.

Sessions 3a and 3b moved every series that has another source to the agency
that produces it, tied out against the FRED copy first (`bedrok tieout`):

| adapter | covers | key | state |
|---|---|---|---|
| `bls.py` | CPI and components, PPI, import prices, payrolls, earnings, unemployment, participation, U-6, JOLTS, ECI | optional | 17 series, identical to FRED |
| `nyfed.py` | SOFR, overnight reverse repo, Empire State survey | no | 3 series, identical |
| `fedboard.py` | H.15 rates and yields, H.10 dollar index and GBPUSD, G.17, H.6, H.8, H.4.1, SLOOS, delinquencies | no | 22 series and one FX pair, identical |
| `fedfiles.py` | Chicago, Cleveland, Dallas, Atlanta and Philadelphia Fed indicators; FHFA house prices | no | 10 series, identical |
| `derived` | 10y−2y, 10y−3m, the 10y and 5y breakevens, the 5y5y forward, the NBER recession flag | no | 6 series, equal to FRED's arithmetic |
| `bea.py` | real GDP, PCE and core PCE price indexes | yes | 3 series, identical |
| `census.py` | retail sales, durable goods, core capex orders, housing starts, permits, new home sales, trade balance | yes | 7 series; six identical, the trade balance starts 1995 where FRED's started 1992 |

**No FRED content is stored any more.** Every FRED row is `cache = session`,
and `tests/test_macro_seed.py` fails if one is not.

## The SDMX publishers added in session 4

| publisher | terms | what it means here |
|---|---|---|
| BIS | Terms of permitted use of BIS statistics: unrestricted provided the BIS is cited and, in a commercial product, the statistics add nothing to its price. "No other use is permissible." | Stored; cite the BIS. Most BIS series are compiled from national central banks, whose own terms can be tighter — the BIS copy does not relicense them. |
| IMF | Copyright and Usage: data may be downloaded, used and redistributed with attribution, but "the IMF does not permit use of its Content or Sites for the training of large language models … without explicit permission". | Stored (the owner's call, 2026-10-10): Bedrok keeps the numbers and an assistant reads them when asked, which is retrieval, not training. Every IMF row and its attribution carry the clause so it travels with the number. |
| Istat | CC BY 4.0. | Stored; credit Istat and state changes. |
| INSEE | Licence Ouverte / Open Licence 2.0 (Etalab). | Stored; "Source : Insee". |
| ABS | CC BY 4.0 for aggregate statistics. | Stored; credit the ABS. |

Not wired, deliberately: the Bundesbank (no reuse grant could be found on its
site, and its Bund yields come through the ECB anyway), the National Bank of
Belgium (non-commercial only), Banque de France Webstat and Stats NZ (both need
credentials for data).

## Source terms, for reference

Every publisher's terms page is in the source list,
`bedrok/seeds/sources.csv` (`terms_url`), beside its licence class, its
attribution line and whether it needs a key. That file is the one place to
look; the list below is kept only for the per-series terms it adds.

- Eurostat — **data on countries other than EU members, EFTA members and official EU candidate countries is excluded from commercial reuse** (the notice's examples are the US, Japan and China). Curated multi-geo sets iterate their own list; the broad HICP set loops over the API response and keeps only `pipeline.REUSE_GEOS`. Kosovo, a potential candidate only, is not carried.
- Bank of England — SONIA requires a verbatim statement, and selected FX series are excluded from the OGL grant.
- The `fedfiles` row covers several publishers, each with its own terms: NY Fed <https://www.newyorkfed.org/privacy/termsofuse>; Chicago Fed <https://www.chicagofed.org/utilities/legal-notices> (not for private or commercial gain, with credit); Cleveland Fed <https://www.clevelandfed.org/privacy> (noncommercial, credited); Dallas Fed <https://www.dallasfed.org/fed/disclaimer> (not for private gain, credited); Atlanta Fed <https://www.atlantafed.org/terms-of-use> (not for commercial gain); Philadelphia Fed <https://www.philadelphiafed.org/about-us/terms-of-use> (all rights reserved); FHFA <https://www.fhfa.gov/about/fhfa-policies/api-terms-of-service> (required notice).
- NBER — <https://www.nber.org/research/data/us-business-cycle-expansions-and-contractions> (copy with attribution), for the derived recession flag.

Terms change. Re-read them rather than trusting this table.
