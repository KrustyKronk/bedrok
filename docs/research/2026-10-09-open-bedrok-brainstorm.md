# Open Bedrok — brainstorm, 2026-10-09

*A brainstorm session, not a build plan. No code was written and nothing was
wired. It records what was read, what the vision looks like once it is checked
against the evidence, the consequences the owner had not yet named, and the
decisions that were the owner's to make before anything was built. This
document is dated evidence and is not rewritten; `STATE.md` carries what is open.*

**Status:** exploratory. Nothing in §6 is decided.

---

## 0. The vision, in the owner's words

> An open source tool that economists, traders or hobbyists can use to quickly
> hook up their AI agent to a suite of publicly available sources for economic
> and financial data — FRED, central banks, statistics offices. Bedrok does the
> difficult part of connecting a user's own accounts for these free sources to
> their Claude. Instead of making a FRED account and wiring it in a bespoke way,
> then doing the same for a hundred central banks, they install Bedrok. Everyone
> uses it with their own personal API credentials for their own personal work,
> so it is always commercially fine. Not for money. Probably an MCP.

## 1. What was read

- Bedrok: `STATE.md` (main and the carve-out branch, PR #34), `README.md`,
  `docs/SETUP.md`, `docs/LICENSING.md`, `docs/rebuild-plan.md`,
  `docs/engineering/notes.md`, `bedrok/core/licensing.py`, `config.py`, the
  adapters, the CLI table, the live store (`profile/data/market.duckdb`,
  read-only), the installed `bedrok` skill, the monitor's MCP server.
- The owner's source register (kept outside this repo): its README, core
  list, coverage map, licence guide, reference projects, and `register.csv` (1,150 rows, 42 columns), profiled
  programmatically. The 31 catalogue files were not read in full; the register
  is their structured form.
- Outside: Anthropic's MCP bundle (`.mcpb`) documentation; the READMEs of the
  two nearest peer projects, `hanlulong/openecon-data` and
  `rabidlego25/macro-mcp`; a web survey of macro-data MCP servers.

## 2. Premise checks

Four things in the vision deserve a check before anything is designed on them.

### 2.1 "The hard part is connecting credentials" — mostly not

The register was filtered for what an open, personal-use, bring-your-own-key
tool could automate: quality tier 1 or 2, personal use permitted, free, an
automatable access method (REST, SDMX, bulk download, Python package), and
instant or no registration. **595 of 1,150 sources pass. 499 of those need no
authentication at all; 96 need a free key issued on the spot.** Of the 341
tier-1 sources in that cut, the keyed ones cluster in one place: the US federal
statistical system (BLS, BEA, Census, EIA) plus FRED.

So the install story is not "enter your ten keys". It is **zero keys to start,
and four or five optional keys that unlock the US agencies and energy**. Europe,
the UK, the OECD, the World Bank, the BIS, the IMF's SDMX endpoint, Treasury,
the Fed Board's release files, the New York Fed's API and most national
statistics offices are keyless.

What is actually hard, in the order it costs time:

1. **Discovery.** Knowing that the series exists, which publisher is the clean
   route, and what its code is (`CPI._T.YOY_PCH_PA_PT` is a real IMF key).
   Models guess codes; a curated catalog is what stops that.
2. **The protocol zoo.** SDMX 2.1 and 3.0, JSON-stat, PxWeb, the BLS and BEA
   APIs, Census, Treasury Fiscal Data, Socrata, and "an xlsx at a URL that moves".
3. **Hygiene.** Units, frequency, seasonal adjustment, rebasings, revisions,
   licence class, and whether the number is contested (§3.3).
4. **Maths that does not lie.** A percentile without its sample size, a
   year-to-date measured from the wrong anchor, a level spliced where a return
   should have been. Bedrok's engines already encode a ledger of these.

The credential plumbing is a day of work. The catalog and the trust layer are
the product.

### 2.2 "Everyone uses their own keys, so it is always commercially fine" — true, with one exception that matters

Personal use with one's own credentials is permitted by 976 of the 1,150
sources, and it is the right posture. But a user's own key does not change what
the terms say, and one source's terms reach the user directly:

**FRED.** Its terms, read in a browser by the register on 11 and 15 September
2026, forbid two things beyond the usual: storing or archiving FRED content "in
any database", and using FRED content "in connection with the development or
training of any software program or system or machine learning, including, but
not limited to, large language models". The register's own reading: training is
clearly barred; retrieval at inference time into an LLM's context is "genuinely
ambiguous"; a local store built from FRED pulls is out of terms *even for
series that are public domain at source*. Bedrok today holds 76 FRED-channel
series in DuckDB.

The way out is already written in `docs/LICENSING.md` and the register agrees
with it: **go to the producing agencies**. BLS, BEA, Census, Treasury Fiscal
Data, the Fed Board's statistical-release files and the New York Fed's markets
API are public domain, carry no AI clause, and 49 of the 76 re-point cleanly.
The same number from FRED and from BLS differs only in the terms attached to
the pipe. This is not just compliance — it is the one thing no peer project
does (§4.3), because every one of them wraps FRED.

A smaller instance of the same pattern: **Tiingo's free tier forbids persisting
its data** ("you may not write, save, archive, back up, or otherwise retain
Tiingo Data in any persistent or durable storage"). Bedrok's price pipeline
writes Tiingo rows into DuckDB. Yahoo's chart endpoint is unlicensed for any
third-party use and the register rates personal use as conditional. Equity
prices are a licence problem in a way official statistics are not (§3.4).

### 2.3 "Maybe a website where users add their credentials" — no, and for a structural reason

A website holding keys makes its operator the custodian of other people's accounts. A
hosted MCP makes the operator the party fetching and relaying the data, which is
exactly the redistributor position the bring-your-own-key model exists to avoid.
**Local-only is the licence posture, not a convenience.** It also removes every
hosting cost, secret and uptime obligation.

The key-entry UI the website would have provided already exists natively:
Claude Desktop's `.mcpb` bundle declares optional `user_config` fields in its
manifest, surfaces them at install time, masks sensitive ones, and passes them
to the server as environment variables. For Claude Code and other hosts a
`bedrok keys set <source>` command writing a `chmod 600` env file does the same.

### 2.4 "Fifty sources" — the number is both too small and the wrong unit

The register's cut has 595 candidates, 341 of them tier-1. But sources are not
the unit of work; **protocols and series are**. 91 of the 595 speak SDMX. One
generic SDMX adapter plus a registry row per publisher covers the ECB,
Eurostat, the OECD, the BIS, the IMF, ILOSTAT, the Bundesbank, INSEE, Istat,
the NBB, the ABS, Stats NZ and more. Bedrok today has one hand-written adapter
per source (ten of them, ~1,200 lines). The architectural shift is from
*adapter per source* to *adapter per protocol, row per source, row per series*.

## 3. The inventory

### 3.1 Bedrok after the carve-out

The live store, read on 2026-10-09:

| Table | Rows | Note |
|---|---:|---|
| `macro_catalog` | 392 | 11 sources; 388 have observations |
| `macro_series` | ~492,000 | eurostat 197 ids, fred 76 (4 restricted), worldbank 52, ecb 21, oecd 11, eia 8, ons 6, boe 6, derived 6, cftc 5, fedfiles 4 |
| `instruments` / `prices_eod` | 240 / ~1.73M | the portfolio-driven universe, Yahoo and Tiingo |
| `fx_rates` | ~56,000 | ECB reference rates plus derived crosses |

What survives the vision unchanged: the licence vocabulary and its three gates
(`licensing.py`, `validate`, `pack`), the ten macro adapters as reference
implementations, the derived-series discipline (`derived.py`: arithmetic only,
inputs named), the spike guard at the write boundary, the staleness audit, the
identity tripwire in `fetch`, the `all_series` view, `pctile` with its sample
size, `rates_infl` with its per-era correlations, `doctor`, `schema`, `query`.

What is dead weight for it, by the line counts: the nine broker parsers
(1,173), the portfolio engines (`common`, `reconstruct`, `attribution`,
`lookthrough*`, `counterfactual_*`, `drift`, `returns`, `etf_returns`, ~1,400),
`surfaces/seed.py` (1,158), `monitor/` (2,862 lines of TypeScript), news,
fundamentals, ETF holdings ingest, the KV layer, `daily_prices`, universe
requests, and the R2 publishing. Roughly half the Python and all of the
TypeScript. Of the 438 Python tests, something like half test code that goes.

What Bedrok has never had: an MCP server over the *macro* store. The retired
`bedrok-monitor` server read precomputed portfolio keys; the macro surface was a
Cowork skill that shelled into a sandbox, staged a DuckDB binary and paid a
fifteen-minute bootstrap tax every session. A local MCP process with the store
open pays none of that.

### 3.2 What the register adds

Beyond the 595-source pool: licence verdicts with the operative sentence quoted,
the access protocol and auth per source, rate limits, revision policy, whether
vintages exist, and a `gotchas` field per source. Two sections are worth more
than the source list:

- **"What no open source provides"** (coverage map §3): a harmonised
  cross-country policy-rate panel, exchange prices, PMIs, battery-metal prices,
  term premia outside the US, parallel exchange rates. Bedrok should say these
  plainly rather than approximate them.
- **"When the official number is wrong"** (coverage map §7): forty-odd
  documented cases — Argentina's CPI 2007–15, Turkey's CPI since 2021, the UK
  Labour Force Survey since 2023, the US CPS gap of October 2025, Chinese
  youth unemployment, every frontier debt restatement. **This becomes a
  `caveats` column in the catalog.** A tool that tells the model "this series
  is contested, here is the cross-check" is something nobody ships.

The register's own warnings carry over: it is not legal advice; its verdicts
are for a *commercial consultancy* and were re-filtered here for personal use;
and "a third of the mechanical detail is stale within a year" — the IMF portal
migrated, OECD.Stat was retired, the Bundesbank's legacy database closed, the
Census API's keyless tier is gone, and the Fed Board is retiring its Data
Download Program in favour of FRED, which is a licence downgrade. A source
registry needs a liveness test, not a one-off audit.

### 3.3 The peers

| Project | What it is | What Bedrok would not copy |
|---|---|---|
| `rabidlego25/macro-mcp` | Keyless local MCP over dozens of SDMX providers plus GLEIF; 12 tools; MIT; caches metadata only, never observations; runs via `uvx` | No store, so no history, no SQL, no derived series, no cross-source joins; silent on data licences |
| `hanlulong/openecon-data` | Hosted, LLM-routed, 330K indicators from FRED, World Bank, IMF, Eurostat, BIS…; AGPL-3.0 | Hosted; routes through FRED and inherits its clause; the register names "licence non-determinism" — the same question answered from a clean source one day and a restricted one the next |
| `stefanoamorelli/fred-mcp-server` and kin | FRED wrappers, bring your own key | FRED-only, and FRED's clause |
| `lzinga/us-gov-open-data-mcp` | 250+ tools over 40 US and international APIs | Tool bloat; a model given 250 tools picks worse than one given eight |
| `worldbank/data360-mcp`, `unicef-drp/sdmx-mcp` | Official single-publisher or generic-SDMX servers | Narrow by design; useful as references for the SDMX adapter |

Nothing in the field has all four of: a local store with history you can SQL
over, stable cross-source series identifiers, per-series licence and AI-clause
tagging with agency-direct routing, and a trust layer that refuses to quote a
percentile without its sample size. That is the lane. It also happens to be
what Bedrok already is, minus the portfolio.

### 3.4 Prices are a different kind of problem

Official statistics are open by the publisher's choice. Equity prices are
licensed goods that reach free endpoints second-hand: Yahoo's terms forbid
automated access and commercial use, Tiingo's free tier forbids persistence,
and every index provider (S&P, MSCI, FTSE, STOXX) is a verified no with AI
clauses. The *financial* data that is genuinely open is official: Treasury par
and real curves, ECB reference rates and effective exchange rates, EIA spot
energy, the World Bank Pink Sheet, CFTC positioning, the Fed Board's excess bond
premium, the New York Fed's reference rates and term premia, Cboe's VIX from
Cboe's own site, the Kenneth French factor library. That is a rich "markets"
layer with no licence exposure. Equity prices belong in an optional adapter the
user enables knowing whose terms they are accepting, not in the core.

## 4. The proposal

### 4.1 Shape

One Python package. `pip install bedrok` or `uvx bedrok`. `bedrok mcp` starts a
stdio MCP server with a local DuckDB store in the user's data directory. Keys
live in an env file or the host's `user_config`; absent keys simply hide the
sources that need them, the way World Monitor hides a layer. No hosted
component anywhere.

```
host (Claude Desktop · Claude Code · Cursor · any MCP client)
  └─ bedrok mcp                       seven tools, two resources
       └─ catalog + store             DuckDB; stable series_ids; licence,
          + trust gates               attribution, caveats, cache policy per row
            └─ protocol adapters      sdmx · json-stat · pxweb · bls · bea ·
                                      census · fiscaldata · socrata · file
                 └─ sources registry  one row per publisher: protocol, base URL,
                                      auth, licence class, AI clause, pacing
```

Distribution, in order of friction: `claude mcp add bedrok -- uvx bedrok mcp`
for Claude Code; a `.mcpb` bundle for Claude Desktop whose manifest declares
each optional key as a sensitive `user_config` field; a JSON snippet for
everything else. Remote MCP and OAuth are deliberately not offered.

### 4.2 Tools

Few, and each one answers a question the model would otherwise guess at.

| Tool | Answers |
|---|---|
| `list_sources` | What is wired, which keys are set, which sources are reachable right now, each one's licence class and AI clause |
| `search_series` | Curated catalog search by name, geography, frequency, topic; with `discover=true`, a live SDMX dataflow search for the long tail beyond the catalog |
| `describe_series` | Units, frequency, publisher and code, licence and the attribution line to carry, last observation and staleness, revision policy, **caveats** |
| `get_series` | Observations for one or more ids, with the transforms the engines already know (yoy, 3-month annualised, index to a date), fetched lazily and cached per the source's policy |
| `percentile` | Where a value sits in its own history — **with sample start, end and n**, never without |
| `query` | Read-only SQL over the local store: the escape hatch, and the thing no peer has |
| `refresh` | Incremental sync of what is cached |

Resources: the catalog and the generated `LICENSES.md`.

### 4.3 Cache policy is a column, not a setting

Each source row carries `cache: store | session | none`. Open publishers are
`store` and build deep local history over time. FRED, if it ships at all, is
`session`: fetched into the conversation and never written. Tiingo's free tier
would be `session` too. The licence gate that today blocks the pack build
becomes the gate that decides what the store may hold.

### 4.4 The catalog is the product

Start from Bedrok's 392 rows and the register's tier-1 cut. Keep the stable
`series_id` vocabulary (`us_cpi`, `ea_hicp`, `hicp_yoy_de`, `it_de_10y_spread`)
and extend it; models reason far better over `uk_cpih` than over
`D7G7`-under-`/economy/inflationandpriceindices`. Every row carries source,
code, units, frequency, licence, attribution, cache policy, staleness
allowance, and caveats. The register's `gotchas` seed the caveats; its §7 seeds
the contested-number flags.

The long tail is served by `discover`, not by the catalog. A curated catalog of
one or two thousand series plus live SDMX discovery is better than ten thousand
rows nobody verified.

### 4.5 Scope ceiling

Thirty sources for v1, not fifty and not five hundred: the eleven already wired
(FRED demoted to opt-in), the five US agency-direct adapters the licensing doc
already names, and the SDMX family the generic adapter unlocks for free (BIS,
IMF, ILOSTAT, Bundesbank, INSEE, Istat, NBB, ABS). Everything else is a registry
row someone can contribute with a fixture test.

## 5. Consequences the owner had not named

1. **Bedrok probably ships without FRED as a default route.** The alternative
   is to carry a clause that reaches the user's own use. Opt-in pass-through
   under the user's key, session cache only, with FRED's required notice, is
   the most that is defensible. This is the single biggest break from the
   mental model in §0, and it is also the differentiator.
2. **The current price pipeline is out of terms on two sources.** Tiingo's
   free tier forbids the DuckDB write it does today; Yahoo is unlicensed. This
   is true now, before anything is opened.
3. **The repository cannot simply be flipped public.** Its history carries
   personal material and a vendor file whose terms forbid derivative works. The clean move is a **fresh public
   repository with squashed history** and a re-audit of the tree; rewriting
   history in place is the other option and is worse.
4. **Python needs a runtime the user may not have.** A stdio Python server
   depends on Python being installed; `uv`/`uvx` is the field's answer and
   macro-mcp proves it works, with a slow first run. A TypeScript rewrite would
   bundle cleanly into `.mcpb` and throw away everything that exists.
   Recommendation: Python with `uv`.
5. **SDMX is a protocol, not a schema.** Every provider's dataflow has its own
   dimension order and codelists, and the "pin every dimension" lesson from the
   OECD adapter applies everywhere. The generic adapter is a week; the curated
   rows are the ongoing work.
6. **Facts rot, so the registry needs CI.** A weekly smoke-fetch of one series
   per source, on a public repo where Actions minutes are free, is what keeps
   "works out of the box" true. Half the engineering notes in this repo are
   about sources that moved.
7. **The pack question reopens.** Today the weekly GitHub Actions pack is what
   feeds the owner's own macro store. For an open tool there are three options:
   no pack (each install backfills locally; macro backfill is minutes, not the
   210 that Tiingo pacing cost), a licence-clean starter pack published as a
   GitHub Release, or keep the R2 pack for the owner only. FRED-channel series
   can never be in a public pack regardless.
8. **Peers are collaborators as much as competitors.** DBnomics, OpenBB and
   World Monitor overlap this register's sources heavily; OpenEcon maintains
   the `awesome-ai-for-economists` list where a tool like this gets found.
   AGPL peers cannot be copied into an MIT codebase; the shapes can.
9. **The Cowork skill becomes a pointer.** Once the MCP exists, the owner's own
   `bedrok` skill is a few lines naming the server, and the sandbox staging
   recipe in `notes.md` becomes history.
10. **`bedrok` is free on PyPI** as of 2026-10-09 (`bedrock` is taken).

## 6. Decisions that are the owner's

1. FRED: opt-in pass-through with no local cache, or not shipped?
2. Equity prices: out of the core with official rates, FX, energy and
   positioning as the "markets" layer, or an optional plugin from day one?
3. Python with `uv`, or a TypeScript rewrite?
4. Fresh public repository with squashed history, or rewrite this one?
5. The v1 scope ceiling: thirty sources curated plus SDMX discovery?
6. The pack: none, a clean starter pack via Releases, or owner-only?
7. Keep the name.

## 7. The sessions, if the answers are roughly the recommendations

Each one branch, one PR, one thing, in the order the dependencies run.

1. **Strip the portfolio.** Delete brokers, the portfolio engines, `seed`,
   `monitor/`, news, fundamentals, ETF ingest, KV, `daily_prices`, universe
   requests and the R2 publish. Tests green at the smaller count. README and
   `pyproject` re-described. Nothing new.
2. **Agency-direct re-plumb.** `bls.py`, `bea.py`, `census.py`, `treasury.py`,
   `fedboard.py`; re-point the 49 series; licence flips to `public_domain`;
   FRED becomes an opt-in `session`-cached adapter for what remains. This was
   already the plan in `docs/LICENSING.md`; it is now session 2 rather than
   "eventually".
3. **Source registry and protocol adapters.** `sources.csv`; the generic SDMX
   adapter; migrate `ecb`, `eurostat`, `oecd` onto it; add the SDMX family as
   rows; the `cache` and `caveats` columns.
4. **The MCP server.** `bedrok mcp`, the seven tools, lazy fetch, the SQL
   escape hatch. Replace the sandbox skill.
5. **Packaging.** `uvx`, the `.mcpb` manifest, a zero-key first run proven on a
   clean machine, docs and a demo transcript.
6. **Open it.** Fresh public repository, privacy re-audit, weekly smoke-fetch
   CI, `CONTRIBUTING.md` with the registry-row contract, a source-request issue
   template seeded from the register's tier-1 cut.

Later, and only if the first six land: vintages from the BLS and BEA archives
and the ONS revision triangles (the ALFRED replacement), the contested-number
caveats in full, a starter pack.

## Appendix — the register cut, by domain

Tier 1 and 2, personal use permitted, free, automatable, no or instant
registration: 595 sources. Tier-1 count per register domain, and the headline
sources the open tool would wire first.

| Domain | Tier-1 | Headline sources |
|---|---:|---|
| Central banks | 28 | ECB, BoE, Fed Board releases, NY Fed markets API, Atlanta/Cleveland/Chicago Fed files, Bundesbank, Riksbank, BCB, Banco de México |
| Europe and other advanced | 23 | Eurostat (three routes), AMECO, INE Portugal, KSH, OeNB, NBB, Statistics Canada |
| US federal statistical system | 20 | BLS API, BEA API, Census EITS and trade, Treasury XML, FHFA, DOL claims |
| External sector, trade and FX | 20 | ECB EXR, BIS EER, IMF BOP and COFER via SDMX, Treasury TIC and reporting rates, UN Comtrade |
| Credit, banking and conditions | 17 | ECB BSI/MIR/BLS/CISS, BoE Bankstats, Fed H.8/G.19/Z.1, SLOOS, FDIC, OFR |
| Labour markets | 17 | BLS CES/CPS/JOLTS/ECI/QCEW, Eurostat LFS, ILOSTAT, ONS, OECD |
| Prices and inflation | 16 | BLS CPI/PPI/MXP, BEA PCE, Eurostat HICP, ECB SPF, Destatis, INSEE, e-Stat, Dallas and NY Fed measures |
| Monetary policy and rates | 15 | Treasury par and real curves, NY Fed reference rates and ACM, BoE yield curves, ECB curves, Bank of Canada Valet, SNB, RBA |
| Housing | 15 | FHFA, Census construction and sales, Eurostat HPI, ECB RPP, UK HPI, Land Registry |
| Commodities and energy | 13 | EIA API, World Bank Pink Sheet, USDA, FAO, EC Weekly Oil Bulletin, USGS |
| IFIs and multilaterals | 13 | OECD SDMX, World Bank Indicators, IMF WEO/IFS via SDMX, QEDS, BIS Data Portal |
| Fiscal and public debt | 13 | Treasury Fiscal Data, ONS PSF, Eurostat GFS, OBR, World Bank IDS |
| National accounts | 12 | BEA NIPA, Eurostat, ONS, Destatis, e-Stat, OECD QNA, NY Fed Nowcast, Atlanta Fed GDPNow |
| National statistics offices | 14 | ONS, INE Spain, Istat, KOSIS, StatFin, Statbank Denmark and Norway, Statistics Sweden |
| Demographics and households | 12 | Census API, Eurostat demography, SCF, DFA, NY Fed household credit, World Bank PIP |
| Electricity and power | 9 | EIA-930, SMARD, Elexon, Eurostat energy prices, CAISO, ERCOT |
| Other EM, East Asia, Central Asia | 19 | INEGI, IBGE, CBRT, Bank of Korea ECOS, Japan Cabinet Office and MoF, National Bank of Kazakhstan |
| Everything else | 28 | Surveys (EC BCS, Tankan, INSEE, Istat), forecasts (SEP, SPF, BoE MPR), CFTC, Cboe, Fama–French, academic datasets |

Already in Bedrok: 122 of the 595. Carrying an AI clause: 21, all FRED-channel
or IMF-training-only. The full list, with access method and auth per row, is
reproducible from `register.csv` with the filter stated above.
