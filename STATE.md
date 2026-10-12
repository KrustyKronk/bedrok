# Where Bedrok stands

**Updated 2026-10-12, end of session 12.** Bedrok is a local MCP server and store of
public macroeconomic data, each series fetched from its publisher. The plan is
`docs/rebuild-plan.md`; the reasoning behind it is
`docs/research/2026-10-09-open-bedrok-brainstorm.md`. Sessions 1 (strip the
portfolio), 2 (one local store), 3a and 3b (every series to its publisher, FRED
held only for the conversation), 4 (the source list and one SDMX client), 5
(the MCP server), 6 (packaging and the zero-key first run), 7a (the second
wave of publishers) and 7b (warnings, the gaps note, the menu review) built
it. The owner then used it as an analyst would (2026-10-11), and the feedback
became sessions 8 (lining up the data), 9 (charts and answers), 10 (the data
the questions needed) and 11 (tidying the catalog). Session 12 opened it:
**Bedrok 0.2.0 is public**, at `github.com/KrustyKronk/bedrok` and on PyPI
(`uvx --python 3.12 bedrok serve`), with the Desktop bundle on the release.
**Next: four sessions, in this order** (the owner, 2026-10-12):
- **A. Correct answers:** item 2, item 3 once the owner has read the terms,
  and item 9's instruction line. These change answers today.
- **B. The Fed Board migration:** item 10, before the week of 2026-11-09.
- **C. Breakdown questions:** item 1, already decided.
- **D. Charts and tool friction:** item 4, item 20, and outside rows in
  `chart_series` (decided below).

Session 13 (revisions and the release calendar) stays deferred.

**Owner-side items** — the ones that need the owner's accounts or describe one
machine — live in `profile/STATE-private.md`, which is gitignored. This file
holds only what any reader may see.

**Session 12 in one paragraph.** The tree was audited for anything personal and
cleaned: a lock file whose name carried private detail, one research document
and a maintainer note went; the engineering notes lost their portfolio-era
sections (1,248 lines to under 600); every working document now says "the
owner". `tests/test_publication.py` makes that permanent: it fails on home
paths, literal key values, token shapes and files that belong only in
`profile/`, in the tracked tree and in the built wheel, and checks an owner's
private term list when `profile/publication-terms.txt` exists. The first public
version is **0.2.0**, released to PyPI by trusted publishing (`publish.yml`:
OIDC, no token; it refuses a tag that disagrees with `pyproject.toml` and runs
the empty-home check on the very wheel it uploads). `bedrok smoke` now paces
its checks, prints WARN when a publisher answers with data older than the
series' allowance, and takes `--keyless`; `liveness.yml` runs it every Tuesday
with no secrets; `tests/test_smoke.py` pins its verdicts. `CONTRIBUTING.md`, a
source-request issue form and a README for a stranger complete the public
face. The public repository is `github.com/KrustyKronk/bedrok`, created from
one clean commit of this tree; the old one is archived and stays private.
Proven after release: `publish.yml` uploaded the wheel and sdist by trusted
publishing, and the package from PyPI, started from an empty home with no keys,
fetched live and answered nine calls in 41 seconds (`scripts/mcp_smoke.py`).

**Session 11 in one paragraph.** One id per concept: six family ids that
duplicated seed ids (`hicp_yoy_es` and `es_hicp_yoy`, …) are aliases in
`core/aliases.py`; they still answer, with `alias_of`, but are neither stored
nor listed. A ninth tool, `list_series`, walks the whole catalog in pages or
counts it by publisher, place and frequency in one call; `search_series` pages
with `offset`. Search treats a place as a filter (`core/geography.py`, one
table also used by the warnings and the names test), no longer matches two
letters inside other words ("us" in `wb_banana_us`), knows a few everyday
synonyms (inflation → CPI/HICP, growth → GDP), and returns no series, only
the warning, for a place Bedrok does not cover. Freshness counts from the last
observation not in the future; forecasts show as `future_to`. Each Eurostat
family names the euro-area codes its dataset fills (youth unemployment has
moved to EA21) and the countries it lacks, and the catalog load prunes rows no
family produces: zero empty stored rows, 531 catalog rows.

**Session 10 in one paragraph.** Six Eurostat families on the curated 14
geographies: HICP core, energy, food, non-energy goods and services (annual
rates; `hicp_core_` …) and GDP quarter-on-quarter (`gdp_qoq_`), 84 series.
Spain's own headline and core annual rates from INE (`es_cpi_yoy_ine` from
1962, `es_cpi_core_ine`); the ONS's official quarterly UK GDP (`uk_gdp_qoq`,
`uk_gdp_yoy`, `uk_gdp_level`, from 1955); US CPI unadjusted (`us_cpi_nsa`,
whose yoy is the published headline); the Fed's daily effective rate and
target range from the New York Fed (`us_effr`, `us_fed_target_lower`,
`us_fed_target_upper`; a decision is a day the target changes); a new
publisher, Japan's Ministry of Finance, for the 10-year JGB daily from 1986
(`jp_10y_yield`; 30 publishers now). Both Brent series say they are Dated
Brent, not the futures price the news quotes, and the gaps page names exchange
futures prices.

**Session 9 in one paragraph.** An eighth tool, `chart_series`, returns the
aligned data and a chart recipe: a suggested title (never with units), a
subtitle with the units, how the data was measured and the span; one scale or
two by units (left solid, right dashed; a third unit is refused with a
suggestion); a colour per series in a fixed colour-blind-safe order; x labels
by frequency with about ten ticks; the source line; footnotes for warnings,
steps, unfinished or incomplete periods and cuts; and the house-style layout.
The house style is `bedrok/seeds/charting.md`, served as `bedrok://charting`
and generated into `docs/CHARTING.md`. The server instructions carry the chart
rule, the short-answer rule (chart and at most three lines, warnings always
said, analysis on request) and a nudge to use `percentile`.

**Session 8 in one paragraph.** `get_series` takes a `frequency` (weekly,
monthly, quarterly, annual) and an optional `how` (last, mean, sum, first), and
then returns one shared `table`: dates and a column per series, with `stepped`,
`partial_last` and `incomplete` flags. Weeks end on Friday and take the last
value; longer periods take the average; slower series repeat as a step and
stop at their last period; nothing is interpolated (conventions in
`docs/engineering/notes.md`). `max_rows` is per series, capped at 1,000, and a
cut says which end and how to get the rest.

## From the feedback session (2026-10-11)

The owner's log of a real working session stays outside the repo: it quotes
ICE high-yield spread values, which are personal-use only. What it found: the
trust layer worked (caveats, warnings and the `fetched` provenance changed
answers for the better; 9 of 10 questions came from the store). What failed was
shaping and presenting, which sessions 8 to 11 fixed. The owner's decisions
that came out of it, all now in force:
- two vertical scales are the standard when units differ;
- units go in the subtitle and on the vertical axis, not the title;
- answers are short by default (the chart and a couple of lines, warnings
  always spoken), more on request;
- Fed decisions come from data: the New York Fed's EFFR feed carries the
  target range daily from 2000.

Two findings that are not Bedrok faults but are said in the catalog:
- **Brent is right, and it is a different Brent.** The store matches the EIA
  exactly and the World Bank agrees; both publish Dated (physical) Brent. The
  news quotes ICE futures, about $20 lower in early October 2026. ICE futures
  are on the gaps page.
- **Charts must be specified by Bedrok because the host draws them.** Only
  what the server returns and says reaches every host (Claude on the web
  included), which is why the house style is served from the server.

One ask from the same log is still open: item 20 below.

## From the cold-session self-test (2026-10-12)

Claude played the user: a fresh session, given no briefing about Bedrok and
free to use the web or a shell, answered 30 questions with follow-ups (37
exchanges) against the owner's keyed store, and a second session checked each
answer against Bedrok afterwards. The log stays outside the repo. What it
found:
- **The trust layer works when the data is there.** Bedrok supplied every
  number in 33 of 37 exchanges; percentiles came with their samples, loud
  warnings were said, every loaded premise was checked against data first,
  and the chart recipe was followed for one or two units.
- **When the catalog lacks something, the session goes around Bedrok.**
  The first time it stopped honestly at the gap; pushed once, it fetched
  Eurostat with a general web tool, and after that it went to the web on the
  first ask (Japan's GDP, Argentina and Turkey, China's youth unemployment).
  That tool passes numbers through a summarising model, and twice nothing in
  Bedrok could check them. Going to the web for what Bedrok lacks is fine
  (see "Bedrok answers what it can" under the decisions); the fix is item 1
  for what Bedrok should carry, and item 9's labelling line for the rest.
- **Some of those trips were Bedrok's own fault:** a search that returns
  nothing for "trade balance" or "government debt", and a stored Turkish
  series that search cannot find. Item 2.

## Read this first if you are picking the project up

**Bedrok installs without the repo.** The seeds live in `bedrok/seeds/` and
ship in the wheel; the home folder resolves to `--profile`, `$BEDROK_PROFILE`,
`./profile`, or the per-user data folder (`~/Library/Application
Support/bedrok` on macOS), so a fresh install needs no setup and fills its
store on demand. Three install paths, in `docs/INSTALL.md`: a Claude Desktop
bundle (`scripts/build-mcpb.sh` → `dist/bedrok-<version>.mcpb`, attached to
each GitHub release; `server.type: "uv"`, key fields in the keychain),
`uvx --python 3.12 bedrok serve` for Claude Code, and a JSON snippet for other
hosts. `package.yml` proves the wheel from an empty home with no keys on clean
macOS and Linux runners for every PR (`scripts/mcp_smoke.py`), and runs the
publication gate against it. `bedrok docs` generates `docs/SOURCES.md`,
`LICENSES.md`, `WARNINGS.md`, `GAPS.md` and `CHARTING.md`; a test keeps them
current, and the server serves them as `bedrok://…` resources.

**Releasing.** Bump `version` in `pyproject.toml` and
`packaging/mcpb/manifest.json` together (`tests/test_install.py` fails on
drift), merge, then `gh release create v<version>`: `publish.yml` puts it on
PyPI. Build the bundle Mac-side with `scripts/build-mcpb.sh` and
`gh release upload v<version> dist/bedrok-<version>.mcpb`. **Never change the
manifest's `author` or `name`:** Claude Desktop files an extension as
`local.mcpb.<author>.<name>`, so a new author installs a second extension with
no settings instead of upgrading (it happened at 0.2.0; `docs/engineering/notes.md`).

**Bedrok is an MCP server.** `bedrok serve` (`bedrok/surfaces/mcp_server.py`,
official `mcp` SDK v2) exposes nine tools: `list_sources`, `search_series`,
`list_series`, `describe_series`, `get_series` (yoy / 3-month annualised /
rebased / aligned to one calendar), `chart_series`, `percentile` (always with
its sample), `query` (one read-only statement, no file or network access) and
`refresh`. A stale or empty stored series is re-fetched from its publisher
before it is answered; session-only series are fetched live and never written;
a full refresh runs as a background job (Claude Desktop allows a tool call
about a minute). It holds no store connection between calls, so a terminal
`bedrok refresh` and the server take turns rather than collide.

**A publisher is one row of `bedrok/seeds/sources.csv`.** It says how to reach
the publisher (`protocol`: one SDMX client, one PxWeb client, a bespoke
adapter, or a bulk stage), its key, licence class and storage rule, terms page,
attribution line, pacing, and the catalog series `bedrok smoke` checks it by.
The pipeline's fetcher map, the pacing, the key checks, the licence defaults
and attribution lines, and `smoke` are all read from it; `core/sources.py`
loads it. Keys are read from the environment when needed and `sources.redact()`
keeps them out of every printed error. How to add one: `CONTRIBUTING.md` and
`docs/SETUP.md`.

**Warnings.** `bedrok/seeds/warnings.csv` holds 43 documented cases where an
official number should not be taken at face value, written as facts with a
source, no motives. `core/warnings.py` loads them. Loudness follows severity:
disqualifying and material cases ride on `get_series` and `percentile` when
their period overlaps the rows answered; caveats show only in
`describe_series` and search. `search_series` returns warnings by place even
when Bedrok carries nothing there ("argentina inflation").

**The store holds no FRED content.** Every series with a timely source of its
own moved to its publisher, each tied out against the FRED copy first. **Nine
series are FRED-only and `cache = session`**, fetched live with the user's FRED
key and never stored: the four personal-use series, the St. Louis stress
index, VIX and the three weekly jobless-claims series. `test_macro_seed.py`
fails if any FRED row is ever stored again.

**How a series changes source.** The seed re-points the row; `bedrok tieout`
compares the new source with the stored history and writes nothing; the next
`refresh` pulls the series in full from the new source and replaces its history
in one transaction (`macro_catalog.filled_from`). A failure, an empty answer or
a missing key keeps the old history. Derived series are recomputed in full and
replaced the same way. A row that becomes `session` has its history deleted.

Claude may merge Bedrok pull requests once GitHub's checks pass.

## What is live

**30 publishers, plus `derived`**, from the Fed Board, the ECB, the BLS, the
BIS, the regional Feds, the OECD, Eurostat, the EIA, Census, the IMF, the ONS,
the Bank of England, the Bank of Canada, the New York Fed, INSEE, the BEA,
Statistics Canada, Japan's e-Stat and Ministry of Finance, the ABS, INE, the
four Nordic offices, Destatis and Istat, with FRED for the nine session-only
series. 531 catalog rows.

**Twelve commands:** `smoke [--keyless]`, `backfill [--only ids --stage S]`,
`refresh`, `validate`, `query`, `tieout [--only ids]`, `doctor`, `schema`,
`pctile`, `rates`, `serve`, `docs [--check]`.

**Tests:** about 340, network-free, green on 2026-10-12, including the MCP
server in memory, the layering rule and the publication gate. **Workflows:**
`tests` (push and PR), `package` (PR: the empty-home install and the gate on
the wheel), `publish` (a published release), `liveness` (Tuesdays, keyless).
None holds a secret.

**No clock beyond liveness.** The store is current when someone runs
`bedrok refresh` or the server re-fetches a stale series on demand.

## Decisions in force

- **Bedrok answers what it can, and does not stand in the way of the rest**
  (the owner, 2026-10-12). A question Bedrok can answer should be answered
  from Bedrok: it is purpose-built for it, and a bug that sends Claude
  elsewhere when the data is in the store is a bug. What Bedrok does not
  carry, Claude may fetch natively (search, a web fetch, another tool);
  Bedrok never forces itself onto an answer and never tries to stop that.
  Whenever outside data sits beside Bedrok's, in a chart or a sentence, the
  answer says which part did not come through Bedrok.
- **The rethink decisions of 2026-10-09** are in `docs/rebuild-plan.md`: local
  MCP, FRED as an optional extra held only for the conversation, equity prices
  out of the core, Python with `uv`, a fresh public repository, about thirty
  publishers for v1, no pack, the name Bedrok.
- **Fresh build, not a fork of World Monitor.** It is AGPL-3.0-only; Bedrok is MIT.
- **One store, catalog from the repo.** Unknown sections in `profile.toml` are
  passed through and ignored, so old profiles keep loading; tests pin that.
- **Two columns carry a publisher's terms**: `license` (five classes: public
  domain, attribution, noncommercial, FRED channel, personal use) and `cache`
  (store or session). See `docs/LICENSING.md`.
- **Session series are never stored**: the four personal-use FRED series,
  STLFSI4 and VIX (2026-10-09), and jobless claims (2026-10-10). `validate`
  counts a stored one as a finding.
- **The regional Feds are `noncommercial`; Philadelphia is `personal_use`**
  (2026-10-10). Both are stored.
- **The IMF is stored** despite its "no training of large language models"
  clause: Bedrok retrieves, it does not train. The clause rides on every IMF
  row and its attribution (2026-10-10).
- **One source list is the truth about publishers** (`sources.csv`); a thin
  SDMX client of our own rather than the `sdmx1` library (session 4).
- **A re-pointed series is replaced, never stitched** (`filled_from`, 3a).
- **Derived series are arithmetic anyone would agree with**, computed only from
  stored inputs (a test pins it), labelled with their inputs, recomputed in full.
- **The zero-key promise** (2026-10-10): most things work without keys; free
  keys add US GDP/PCE (BEA), retail/housing/trade (Census), energy (EIA), the
  FRED-only live series, Germany's national CPI and production (Destatis) and
  Japan's CPI and unemployment (e-Stat). The BLS key only lifts a rate limit. A
  missing-key answer names the key and its signup page.
- **Tests carry no recorded responses.** Adapters are tested on inline
  payloads; recorded cassettes were judged not worth a dependency (session 12).
- **The liveness run is keyless** until keys are worth putting on GitHub as
  repository secrets (session 12).
- **Never advice, never timing signals.** See `docs/DISCLAIMER.md`.

## Open, in the order I would take them

Completed items are removed rather than ticked — git history is the record.
Owner-side items are in `profile/STATE-private.md`.

1. **Breakdown questions, a session of its own.** The owner's two feedback
   logs of 2026-10-12 (kept outside the repo) asked for Spain's GDP growth by
   expenditure and by sector, the US 10-year quarterly, and annual growth of the
   big European economies. Only the US 10-year came wholly from the store; for
   the rest the session called Eurostat's API directly with curl and a
   hand-written parser, losing caveats, attribution, freshness and any stored
   copy, and two of three charts were hand-styled. What worked: `chart_series`
   on `us_10y` in one call, `list_series` by place, `get_series` annual sums
   with `incomplete`. The owner's decisions are taken; the session builds:
   - **Annual real GDP growth:** a Eurostat family `gdp_annual_<geo>` from
     `nama_10_gdp` (`unit=CLV_PCH_PRE`, `na_item=B1GQ`) on the 14 curated
     geographies, one more entry in `EUROSTAT_BULK_SETS`
     (`ingest/pipeline.py`). The UK as a derived series from `uk_gdp_level`
     (annual sum over the previous year's). The `gdp_yoy_` note says its
     annual mean is close to annual growth but is not it.
   - **Contributions to growth:** `gdp_contrib_<part>_<geo>`, parts `hh`,
     `gov`, `gfcf`, `inv`, `exp`, `imp`; annual from `nama_10_gdp`
     `CON_PPCH_PRE`, quarterly from `namq_10_gdp` `CON_PPCH_SM` (on a year
     earlier, like `gdp_yoy_`); about 168 rows. Net trade is a derived series
     (`exp` + `imp`). A test checks the parts add to GDP growth within
     ±0.05 pp, and a gap becomes a footnote, not a silent pass. They appear
     under `related` on `gdp_yoy_<geo>` (the groundwork for item 20). They show
     in `search_series` only when the search names "contribution" or a
     component, so ordinary searches stay short.
   - **The US and the UK too:** BEA table T10102 through the BEA adapter (keyed;
     quarter-on-quarter at an annual rate, not the European basis, said in
     each note); the ONS's contributions if it publishes them, otherwise an
     entry on the gaps page.
   - **How partial the last period is:** `get_series` and `chart_series`
     report the observation count and dates of an unfinished last period, and
     the footnote says it ("Q4 2026: 6 observations, 1–8 Oct"). Footnoted,
     never dropped. Pin the `us_10y` case from the log (six trading days that
     read as a jump from 4.76% to 5.27%).
   - **Charts:** `bedrok/seeds/charting.md` allows stacked contribution bars
     with a dot for the total; it lists the seven `PALETTE` hex values so a
     hand-built chart can match; the `chart_series` cap rises from five to
     seven; `docs/CHARTING.md` is regenerated.
   - **Finding series:** alias `gdp_yoy_uk` → `uk_gdp_yoy` (`core/aliases.py`);
     `list_series` takes a `prefix` and returns `first_date`; an empty
     `search_series` names the nearest series it carries; the `query`
     description warns that DuckDB rejects `first` and `last` as bare aliases.
   - **Server instructions:** a figure from outside Bedrok is checked against
     the stored headline; background claims not drawn from data are labelled.
   - **Done when** both logged questions, put through the MCP server again,
     need no call outside Bedrok.
   - **Left out on purpose:** output by sector (A10; it doubles the catalog
     and its contributions are a method choice) and a `decompose` tool.
     `chart_series` taking rows from outside Bedrok was excluded here at
     first; the owner reversed that on 2026-10-12, and it moved to item 4.

2. **Bugs the cold-session self-test found (2026-10-12).** Each was hit by
   a real question, not only read in the code; repro calls are in the owner's
   log. In the order they change answers:
   - **Ordinary words empty `search_series`.** "government debt" and "trade
     balance" return no series, only one unrelated warning, although
     `govdebt_gdp_*` and `us_trade_balance` exist. `core/warnings.py`
     tokenises a warning's whole `country` field ("Global (debt statistics
     vintages)", "Caucasus and Central Asia (mirror trade)"), and
     `search_series` drops every series when a warning matches a place and
     no covered place is named. Words that trigger it include debt, trade,
     from, central, statistics, country, producers, global.
   - **Stored series outside the geography table can't be found by place.**
     `hicp_yoy_tr` is stored and fresh, but "turkey inflation" returns only
     the Turkey warning; the session told the user Bedrok does not carry
     Turkey and went to the web. The same holds for the other non-EU
     broad-HICP countries, and "euro area … rate" filters out the ECB rates,
     which carry no geography.
   - **With a third unit the chart recipe contradicts itself.**
     `axes.problem` says a series "is not placed" while `chart.series` puts
     it on the left axis, solid; adding a series re-assigns the axes of the
     chart the user already had. `mcp_server.chart_series`.
   - **Warnings miss sibling series.** `us_cpi_oct2025_gap` is not linked to
     `us_cpi_nsa` (the headline-matching index) or `us_cpi_3m_annualized`;
     `us_cps_oct2025_gap` not to `us_participation` or `us_u6`;
     `tr_cpi_item_prices_2022` not to `hicp_yoy_tr`. The hole shows without
     its reason.
   - **The euro-area surveys stopped at 2025-12.** `esi_`, `ind_conf_` and
     `cons_conf_` use `ea_codes: ["EA20"]`, which ended when the euro area
     became EA21; every refresh succeeds and finds nothing. HICP and GDP use
     the rolling `EA` code.
   - **`percentile` `since` needs a full date.** `since="2010"` fails with an
     opaque "Error executing tool percentile"; `get_series` accepts "2010".
     A start after the end returns empty with no message.
   - **Destatis GENESIS error 7000** on `de_cpi_destatis` (table 61111-0002)
     in the full refresh; the stored history stands, and the failure detail
     is cut at 80 characters, which loses the useful part of the message.

3. **The Eurostat licence line contradicts what the broad-HICP family
   stores.** The attribution on `hicp_yoy_tr` says non-EU data inside
   Eurostat datasets is excluded from commercial reuse, "so Bedrok fetches EU
   geographies only", while the family stores Türkiye and other non-EU
   geographies. Either stop storing them or correct the line; the owner reads
   the terms and decides.

4. **Friction from the self-test, smaller each, worth a pass together:**
   - **`chart_series` accepts outside rows** (decided 2026-10-12, reversing
     item 1's first exclusion): rows Claude fetched elsewhere, each with a
     label and its source, drawn in the house style, with the source line
     naming the outside part ("World Bank (fetched by Claude, not via
     Bedrok)"). Today a mixed chart falls back to hand styling and labels
     nothing.
   - FX pairs live only in `fx_rates`, so EURUSD needs SQL: no recipe, no
     percentile. A catalog row per pair (or an alias layer) fixes it.
   - Search ranks the seasonally adjusted `us_cpi` above `us_cpi_nsa`, whose
     yoy is the published headline; there is no US CPI rate series.
   - `transform` applies to every series in a call, so published rates and
     index levels cannot share one recipe.
   - Units are compared as exact strings: "percent" and "percent change on
     previous quarter" get two scales.
   - `chart_series` has no daily calendar: a month of daily data is four
     weekly points.
   - Event-dated rates (the ECB's) stop at their last change on a chart,
     although the rate is in force.
   - A weekly point built from part of a week (EIA's lag, the Fed Board's
     one-day lag) is not flagged; with item 1's partial-period counts.
   - `uk_breakeven_5y` does not say it is RPI-based and has no `related`;
     no UK 10-year real yield or implied inflation; no Japanese GDP or
     headline CPI (none of these is on the gaps page).
   - Every "us …" search carries the same five BLS/CPI warnings, whatever it
     is about.
   - `refresh` "ok" does not say whether anything new arrived; refresh
     timestamps carry no timezone.
   - The suggested chart title ignores the transform; the `index_to` label
     names a date on a resampled calendar; no bar form for many places at
     one date.
   - The "percentile with every level" instruction should exclude nominal
     levels that grow with the economy (a trade deficit).
   - When only a warning matches, search could say what is missing and who
     publishes it.

5. **Publishers that refused on 2026-10-11, and what liveness says now.** The
   OECD and the CFTC answered every request with a Cloudflare bot check (403,
   "Just a moment…"); never try to get past a bot check. On 2026-10-12 the
   CFTC answered again; the OECD still refused a home connection in the
   morning but passed from GitHub's runner an hour later, in the first
   liveness run (25 of 25, no stale data). Bot checks come and go: read a
   single red liveness run as "look", and a publisher red for weeks as
   session 14's "retire or re-point". The Bundesbank dropped the TLS handshake
   on its data service, so session 10's daily Bund series waits (it is
   `BBSIS/D.I.ZAR.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A` on the SDMX client,
   with a `bundesbank` source row to add). BEA answered 503 through 2026-10-11;
   it is keyed, so liveness does not see it.

6. **Jobless claims: revisit the Labor Department's API** when convenient. It
   matched FRED exactly but ended four weeks behind on 2026-10-10. If it
   catches up to the Thursday release, move the three claims rows to it (the
   fields are in `docs/engineering/notes.md`).

7. **Catalog gaps a real question exposed** ("how have 10-year government
   bonds done over two years" for the US, Germany, France, the UK, China and
   Japan). Japan's 10-year is in (session 10). Still open:
   - **Daily German yields:** the Bundesbank's BBSIS flow through the existing
     SDMX client, waiting on item 5. Without it Germany and France are only
     Eurostat's monthly averages, about six weeks behind.
   - **France daily:** Banque de France (Webstat) — not yet checked.
   - **China's 10-year:** probably no openly licensed source (ChinaBond is
     commercial). A candidate for `bedrok/seeds/gaps.md`, not the catalog.

   The same answer showed an assistant putting Bedrok's numbers and web
   numbers in one chart without saying the measures differ (Fed
   constant-maturity, BoE zero-coupon, Eurostat monthly averages, web point
   quotes).

8. **Review the warnings' weaker sources.** 11 of 43 cite a news outlet,
   trade press, a think tank or a research brief because no primary document
   could be reached: Iran, Russia (oil output, trade, statistics generally),
   Venezuela, Turkey, Zimbabwe, Kazakhstan, the Gulf producers, Zambia and
   Tanzania's 2018 act. Two more cite a primary page that carries only part of
   their facts: Lebanon's exchange rates and the BLS leadership change.
   Replace with a primary source when one turns up. Three severities were set
   against the register and are the owner's to confirm: Ethiopia's GDP and the
   2025 payrolls benchmark down to caveat, Tanzania's statistics act down to
   caveat. The night-lights study's 1992–2013 period is unverified against the
   paper.

9. **The IMF WEO rows' freshness is their last non-forecast year** (session
   11), right for "is this stale" but not for "is this the latest vintage": a
   WEO update shows only as revised values. Freshness by vintage needs the
   release dates (session 13). Also still open from session 5: live SDMX
   discovery beyond the catalog, and SQL over session-only series. A
   session-only `fetch_dataset` was proposed on 2026-10-12 to keep uncatalogued
   questions inside Bedrok; under "Bedrok answers what it can" that is no
   longer needed, and it is shelved. What remains is one line in the server
   instructions (session A): numbers fetched outside Bedrok are labelled as
   such, and, where Bedrok stores the headline, checked against it, because a
   general web fetch can pass numbers through a summarising model.

10. **The Fed Board zips are the fragile link.** The Board is retiring its Data
    Download Program ("Build Your Package" goes the week of 2026-11-09) and
    points users at FRED. If the release zips go too, 22 series and GBPUSD have
    no other source but Treasury's par-curve feed for the yields (from 1990).
    `smoke` reads the SLOOS zip, so a move shows in the liveness run first.

11. **Shrimp has stopped too, probably.** `wb_shrimp` ends 2023-10 and, as the
    oldest last date among the Pink Sheet series, sets every incremental Pink
    Sheet download back to 2023. Barley and sorghum were retired for the same
    reason (`pipeline.RETIRED`); shrimp is the owner's call.

12. **Starter-set gaps from session 4:** Italy's NIC CPI (rebased in 2026, its
    history sits in a differently shaped flow — Eurostat's HICP covers Italy),
    Istat's headline confidence indices, the ABS monthly CPI (only from 2024;
    the quarterly index from 1948 is carried). The ILO and the Bundesbank were
    left for later.

13. **Eurostat's government debt/GDP family is late:** freshest 2026-01-01,
    past its 260-day allowance. The only `validate` finding. Data, not a fault;
    check whether the release moved before widening the allowance.

14. **Two 7a series are not what one might assume.** Japan's unemployment rate
    is not seasonally adjusted: the seasonally adjusted series are not in
    e-Stat's API database. USD/CAD from the Bank of Canada starts only in 2017.
    Both are said in their notes; a better source for either is welcome.

15. **`backfill` exits 1 on failures while `refresh` exits 3.** Align them when
    a supervisor needs to read `backfill`.

16. **Test coverage gaps:** nothing tests `rates_infl`, `backfill`, or the sync
    stages end to end. `smoke`'s verdicts are tested (session 12); whether the
    live publishers answer is the liveness run's job, not the suite's.

17. **The `.mcpb` bundle is built by hand** for each release. Building and
    attaching it in `publish.yml` is one job (setup-node, setup-uv,
    `scripts/build-mcpb.sh`, `gh release upload`); do it once a manual release
    has proved the artifact.

18. **The liveness run checks keyless publishers only.** The six keyed ones
    (BEA, Census, EIA, FRED, Destatis, e-Stat) would need their keys as
    repository secrets, and a secret-holding workflow must never run on
    `pull_request`.

19. **A fresh store lists 436 series, not 531.** `list_series` on a new install
    counts only what the catalog load knows before any bulk stage has run: the
    World Bank's 50 Pink Sheet series (and a few Eurostat family members)
    appear after their first download. Each answers when asked by id, so
    nothing is lost, but the README's "more than 500" is only true of a store
    that has refreshed once. Either load the bulk families' rows at catalog
    load, as session 11 did for Eurostat, or count them in `list_series`.

20. **A series with its parts in one call.** The feedback log offered twice,
    and never ran, `us_10y` together with its real yield, breakeven and term
    premium. `describe_series` already lists `related` series; a `get_series`
    or `chart_series` option that fetches them alongside would make the
    decomposition one call instead of four.

21. **`scripts/mcp_smoke.py` can pass for the wrong reason.** Started from the
    repo folder it finds `./profile` and answers from the developer's own
    store, not the empty home it was given (it happened in session 12). It
    should fail when the profile it reports is not under the `HOME` it was
    started with, unless told otherwise. CI is safe today only because a fresh
    checkout has no `profile/`.

22. **The later sessions in `docs/rebuild-plan.md` are unplanned:** 13
    (revisions and the release calendar; deferred 2026-10-12) and 14 (the
    maintenance loop: triage source requests from the issue form, retire stale
    publishers, a second pass over the register for the next thirty).

