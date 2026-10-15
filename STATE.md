# Where Bedrok stands

**Updated 2026-10-15: session 13a is in, and 0.3.1 is prepared, not yet published.** Bedrok is a local MCP server and store of
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
**Bedrok is public**, at `github.com/KrustyKronk/bedrok` and on PyPI
(`uvx --python 3.12 bedrok serve`), with the Desktop bundle on the release.
**0.3.0 is released** (2026-10-14): sessions A, correct answers, B, the Fed
Board watch, C, breakdown questions, D1, chart and tool mechanics, and D2,
discovery and catalog, are on PyPI and in the Desktop bundle attached to the
GitHub release. Proven from PyPI: an empty home with no keys answered all
nine smoke calls in 56 seconds. **0.3.1 is prepared** (2026-10-15): the
version is bumped in `pyproject.toml` and the manifest, carrying session 13a
(release rules, the publishers' own dates, `release_calendar`); it is not on
PyPI or in a bundle until the owner publishes it (item 1). **Next:** publish
0.3.1, install its bundle in Claude Desktop and ask it a cold question,
ideally on a release morning (item 1).

Session 13 is split: **13a, the release calendar**, is in (three parts,
below; more feeds are item 2); **13b, revisions and vintages**, stays
deferred.

**Owner-side items** — the ones that need the owner's accounts or describe one
machine — live in `profile/STATE-private.md`, which is gitignored. This file
holds only what any reader may see.

**Session 13a, part 3, in one paragraph.** A tenth tool, `release_calendar`,
answers the two questions the calendar was for. With ids, each series' next
release ("when is the next CPI?"); without, every release Bedrok knows of
from now to `days` ahead ("what is out this week?"), overdue ones first,
series that come out together on one line (the seven Treasury yields, the
three H.4.1 series), a family as one line spoken for by its freshest member,
each time in the publisher's zone and on the reader's clock, `approximate`
when it is a rule. A list never fetches a series; asking for one does. A new
test fails when the bundle manifest and the server's tools differ (nothing
checked it before). Proven: 440 tests; on the live store the week's list
showed five releases due that nobody had asked for and the ONS's exact UK
dates; `scripts/mcp_smoke.py` from an empty home passed with ten tools in
14 seconds.

**Session 13a, part 2, in one paragraph.** Where a publisher states the date,
it replaces the rule's window: a `release_calendar` table (the store's
fourth), `ingest/calendars.py`, and `bedrok/seeds/release_calendar.csv`
naming the feed and release per series. BEA's schedule (one JSON of every
release's dates into 2027) is read at most once a day, by the server before
it judges a BEA series and by a new last `refresh` stage; the ONS's
`nextRelease`, already inside the series JSON the adapter downloads, is
recorded after every fetch at no extra request. PCE, GDP, UK CPI and CPIH,
UK monthly GDP and the UK unemployment rate now say "scheduled for 29 Oct
2026, 08:30 EDT (BEA's release schedule)", not "usually". The next date is
the first after the period is over, which matters: BEA publishes August's PCE
on 30 September. A date far past the rule's window is not believed, a failed
fetch keeps the stored dates, and an exact date is late a day after it, not
two. The ONS dates also caught a wrong rule from part 1: an LFS window is
dated by its middle month, not its first (the notes have it). **The BLS is not in it:** its website answers any program with an
"Access Denied" page on automated retrieval, so its iCal file is off limits
and BLS series stay on their rules. `docs/RELEASES.md` (and
`bedrok://releases`) lists every rule and feed. Proven: 437 tests; a full
`bedrok refresh` against the real store with no failures, its calendar stage
storing 194 BEA dates and the ONS series ten next-release dates, after which
PCE and GDP read "scheduled for 29 Oct 2026, 08:30 EDT" and UK CPI "21 Oct
2026, 07:00 BST" from the live store.

**Session 13a in one paragraph.** On 2026-10-15 the September US CPI came
out at 08:30 ET and Bedrok served August as fresh: freshness was age alone,
and the August point (dated 08-01) was 75 days old against a 90-day
allowance, so it would have passed as current until 30 October. The owner
caught it only because the assistant searched the web. Every series a
publisher releases on a schedule now carries a **release rule**
(`bedrok/seeds/release_rules.csv`, 44 rows, `core/releases.py`): the next
period is expected so many days after it ends, at a time in the publisher's
zone (stdlib `zoneinfo`), rolled to a weekday when one is set. `freshness`
gains two statuses, `due` (inside the window) and `late` (past it and a
two-day grace), and every answer's freshness carries `next_release` with the
sentence to say ("September 2026 is usually out between 12 Oct and 19 Oct
2026, 08:30 EDT, and is not in the store yet; August 2026 is the latest
held"). The server fetches a due series before answering, tries again after
half an hour (six hours for a stale one), never lets an attempt from before
the release time hold the release back, says "arrived" only when a newer
period did (it used to say "refreshed now" either way), and refreshes a
derived series' inputs first. A late release is a chart footnote; `validate`
prints due and late lines without counting them; the instructions say to
give the message and never present the latest held period as the newest.
A seed test holds every rule inside its series' allowance, which raised
eleven: PCE 105, M2 100, H.8 credit 28, euro-area unemployment 100, euro-area
GDP 240, UK monthly GDP 115, Japan's CPIs 95, and the UK unemployment rate
155. (Part 1 first assumed the LFS window is dated by its first month and
set 180; part 2's ONS dates showed it is the middle month, so the rule and
allowance were corrected.) Rules are per series or per uniform
family (`cot_`, `wb_`) only: a BLS-wide default would call JOLTS late every
week. Eurostat's and Destatis's flashes come before the month ends, so a lag
may be negative. Proven: 422 tests; on the live store `validate` showed
fourteen series due that age had called fresh (US retail sales and industrial
production, every Treasury yield, UK CPI, the euro-area HICP final, Canada's
CPI); and on a copy of the store with September CPI deleted, the server in
process reported it due, fetched it from the BLS and turned fresh with the
November window. The research behind the next part (which publishers publish
a calendar a program can read) is in `docs/engineering/notes.md`. Not yet
released: the Desktop bundle runs 0.3.0 until 0.3.1 is published.

**Session D2 in one paragraph.** The self-test's discovery frictions, each
reproduced on the live server first. The six FX pairs answer as series in
every tool (`eurusd`, `gbpusd`, `usdeur`…, "USD per EUR"; capitals as
aliases): `sync_fx` mirrors `fx_rates` into the catalog, guarded out of the
macro stage, which the owner chose in planning over a branch in the server.
Search: the published headline wins a tie (`us_cpi_nsa` over `us_cpi`), the
Fed's balance sheet is named so that "central bank balance sheet" finds it,
a place Bedrok covers draws only the warnings its words are about ("us 10
year yield" no longer carries five BLS notices), and an empty answer for a
place says why (what Bedrok holds there, or which agency the warnings cite).
A search whose place left nothing crashed; fixed. Refresh answers for the id
asked, not for its family's other members, says how many observations
arrived and the last date before and after, follows aliases, and stamps its
timezone. New rows: the Bank of England's 5- and 10-year real yields and
10-year breakeven (RPI-based, said, with the 2030 alignment), the gilts
naming their parts, `validate` holding each yield to its parts; Japan's
headline CPI; the IMF's annual growth for Japan; quarterly Japanese GDP on
the gaps page. `uk_gdp_annual`'s allowance went to 800 days, which the
acceptance test showed it needed. The full refresh then showed the 10-year
identity exact on every day but two, both the Bank's own errors (1996-09-27,
all zeros; 2022-11-28, real yields 1.4 points high), kept as published and
named; the 5-year only holds within 0.1, since the Bank fits the curves
apart there, and is reported as info. Proven: 396 tests; a full `bedrok
refresh` against the real store with no failures at all (the OECD and
Destatis answered again); `validate` clean but for the known Eurostat debt
lag; every change through the server in-process against the real store.
The running servers keep their old code until a reconnect.

**Session D1 in one paragraph.** The chart and tool mechanics the self-test
found wanting, each verified on the self-test's own chart against the real
store. Units share a scale by kind (`units.dimension`: every percentage-like
unit is one, each index base its own), so Spain's two growth rates and a
breakdown's parts and total sit on one scale. A transform per series
(`transforms`) puts a price index charted as a rate beside a published rate
in one recipe. Outside data is drawn beside Bedrok's (`extra`: label, units,
publisher), named "fetched by Claude, not via Bedrok" on the source line and
in a footnote, never in a contribution stack. A daily calendar (the days the
series have, no weekend slot) is chosen by itself for daily data over three
years or less: a month of the VIX is twenty points, not four. A rate dated
at its decisions runs to the end of the chart with its last change in a
footnote. A week that ended on partial data says "holds data to Tue 6 Oct
only". Titles say the transform ("US CPI, annual rate"); `at` a date gives a
bar per series with a footnote naming values older than the date; a money
level's percentile carries a caveat and the instructions say to rank its
ratio or growth. Parts in one call (`with_related`): a yield with its real
yield, breakeven and term premium, `describe_series` saying which related
series are parts. House style updated.

**Session C in one paragraph.** The breakdown questions both feedback logs
took to Eurostat are answered from the store. Eurostat families:
`gdp_annual_<geo>` (real growth by year, 13 places, from 1996, France from
1975) and each expenditure part's contribution to growth, annual
(`gdp_contrib_<part>_<geo>`) and on a year earlier (`gdp_contrib_q_…`; no
Germany or Netherlands, which Eurostat does not publish, owner's choice),
with net trade derived as exports plus imports. The euro area is EA21. The
US parts are BEA's (`us_gdp_contrib_*`, annual rate, from 1947); UK annual
growth is the ONS's own (`uk_gdp_annual`, from 1949), its contributions on
the gaps page. `gdp_yoy_uk` and `gdp_annual_uk` answer as aliases. Search
shows contribution rows only when the words ask for parts, names the
nearest series when nothing matches, and a total and its parts point at
each other under `related`; `list_series` gives first dates. Charts: a
breakdown comes back as `form: stacked_bars` with its total on one scale, a
title naming the total and a footnote on whether the parts add up; seven ids
per chart; the palette's hex values are in the house style; an unfinished
period's footnote says how many observations it holds ("6 observations, 1 to
8 Oct 2026"). `validate` checks the parts add up: Spain ties to the logged
answer exactly (2025: 2.59 against 2.6); 20 of 23 pairs add up within 0.1,
and Greece, Portugal and Finland quarterly do not, because Eurostat adjusts
each part alone (Greece by 1.3 points in 2011, said in the note). Proven: a
full `bedrok refresh` (187/199; the 12 failures are the OECD bot check and
Destatis) and every new path through the server in-process. **The acceptance
test passed on 2026-10-14** (session D2): a cold session with only the
server, asked Spain's growth by expenditure, the US 10-year by quarter and
annual growth across the big European economies, answered all three from
Bedrok alone, nine calls, nothing fetched outside. Its friction notes are in
D2 and item 1.

**Session B in one paragraph.** The Fed Board's retirement of its Data
Download Program turned out not to touch Bedrok: the adapter reads the
release-page zips, and the Board's notice says those stay (the interactive
tool goes the week of 2026-11-09, the pre-built packages later). So nothing
moved source and no history was lost. What changed: `smoke` now HEADs every
one of the eight release zips and prints its Last-Modified date, so a file
that goes shows in the Tuesday liveness run; SLOOS, the one release with no
release page, falls back to the survey's chart-data HTML table when its zip
is gone (`parse_sloos_html`, tied out against every stored observation of
the four series: all equal); the download cache holds eight zips instead of
four, so a run no longer fetches H.10 twice. The re-point recipes for the
day a zip does go (Treasury's par and real curves for the yields, the ECB
cross for GBPUSD, and which series have no non-FRED source at all) are in
`docs/engineering/notes.md`. Proven live: the forced fallback against the
current survey page matches the store exactly, `validate` is clean but for
the known Eurostat debt lag, and `smoke` passed 36 of 38 with all eight zip lines green (the two failures are the OECD bot check and the Destatis outage, item 3).

**Session A in one paragraph.** The cold-session self-test's bugs, each
pinned by a test of what a user hit. Search matches a warning's country as a
name, so "trade balance", "government debt" and "central bank" find their
series again; countries Bedrok carries only inside the broad HICP set
(`geography.FAMILY_CODES`, Türkiye among them) and the ECB's rates have a
place, so "turkey inflation" and "euro area deposit rate" find them. The
October 2025 warnings reach the unadjusted CPI, the 3-month rate,
participation and U-6; Turkey's warning reaches `hicp_yoy_tr`. Dates are read
one way everywhere (`core/dates.py`: YYYY, YYYY-MM, YYYY-MM-DD), and a bad
date, a start after the end or a `since` past the data is refused in words.
`chart_series` marks a series no scale can take `axis: null, drawn: false`
(`not_drawn`) instead of putting it on the left. The euro-area surveys moved
to EA21 (their EA20 stopped at 2025-12; EA21 carries the history from 1980),
the EA20 ids now aliases. Eurostat's grant is EU, EFTA and candidate
countries: the broad HICP set keeps only `pipeline.REUSE_GEOS`, Kosovo is
retired, and the attribution says so. The server instructions now tell the
assistant to label numbers fetched outside Bedrok and check them against a
stored headline. Proven against the real store: a full `bedrok refresh`
(177/189; the 12 failures are the OECD bot check and Destatis, item 3), then
every failing self-test call repeated through the server in-process.

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

The ask from the same log, a series with its parts in one call, was built in session D1.

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
  (see "Bedrok answers what it can" under the decisions); the fix was
  session C for what Bedrok should carry, and a labelling line in the server
  instructions for the rest (added in session A).
- **Some of those trips were Bedrok's own fault:** a search that returned
  nothing for "trade balance" or "government debt", and a stored Turkish
  series that search could not find. Session A fixed them.

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
official `mcp` SDK v2) exposes ten tools: `list_sources`, `search_series`,
`list_series`, `describe_series`, `get_series` (yoy / 3-month annualised /
rebased / aligned to one calendar), `chart_series`, `percentile` (always with
its sample), `release_calendar` (a series' next release, or the week's),
`query` (one read-only statement, no file or network access) and `refresh`. A stored series whose next period is due by its release rule, or
that is stale or empty, is re-fetched from its publisher before it is
answered; session-only series are fetched live and never written;
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
series. 732 catalog rows (session C added 191 for GDP breakdowns; D2 the six
FX pairs and five rows).

**Twelve commands:** `smoke [--keyless]`, `backfill [--only ids --stage S]`,
`refresh`, `validate`, `query`, `tieout [--only ids]`, `doctor`, `schema`,
`pctile`, `rates`, `serve`, `docs [--check]`.

**Tests:** 440, network-free, green on 2026-10-15, including the MCP
server in memory, the layering rule and the publication gate. **Workflows:**
`tests` (push and PR), `package` (PR: the empty-home install and the gate on
the wheel), `publish` (a published release), `liveness` (Tuesdays, keyless).
None holds a secret.

**No clock beyond liveness.** The store is current when someone runs
`bedrok refresh` or the server re-fetches a due or stale series on demand.

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
- **Freshness is age and the next release** (2026-10-15). Statuses: `fresh`,
  `due`, `late`, `stale`, `empty`, `session`. A series without a release rule
  is judged by age exactly as before; staleness overrides due and late, so a
  wrong rule cannot hide a broken feed. Rule timings are the publisher's usual
  practice (`approximate: true`, "usually" in every message), not its calendar,
  until the exact calendars land (item 2).
- **Never advice, never timing signals.** See `docs/DISCLAIMER.md`.

## Open, in the order I would take them

Completed items are removed rather than ticked — git history is the record.
Owner-side items are in `profile/STATE-private.md`.

1. **Publish 0.3.1, install it in Claude Desktop, then the acceptance test's
   smaller frictions.** The version is bumped on `main`; publishing is the
   owner's step: `gh release create v0.3.1` (`publish.yml` refuses a tag that
   disagrees with `pyproject.toml`, runs the empty-home check on the wheel and
   uploads to PyPI by trusted publishing), then Mac-side
   `scripts/build-mcpb.sh` and `gh release upload v0.3.1
   dist/bedrok-0.3.1.mcpb`. The manifest's author and name are unchanged, so
   the bundle upgrades 0.3.0 in place. The owner installs it and asks a cold
   question there, the first time any of A to 13a runs outside this
   machine's development server; on a release morning, "what is out today?"
   is the test of 13a. Smaller, from the D2 acceptance test:
   - `get_series` takes five ids and `chart_series` seven; a breakdown's
     quarterly parts needed two calls.
   - A chart of five places is titled "Germany and 4 others".
   - An annual series reports `age_days` from 1 January (651 days for
     2025's value in October 2026): right by the rule, odd to read.
   - "By quarter" means quarterly averages unless `how: last` is asked;
     the cold session said so, which is the right behaviour, but nothing
     in the recipe states it beyond the subtitle.
   - A place-only search that finds nothing now says why; the broader
     "who publishes what is missing" map was not built (the warnings carry
     it where it matters).

2. **More release calendars** (session 13a's tail; research in
   `docs/engineering/notes.md`, "Where release dates come from").
   - **More exact feeds:** StatCan's JSON (Canada's CPI), the Fed Board's
     monthly calendar pages, INE's and Istat's iCal, the live Eurostat iCal
     (its documented URL is 404), the ECB's calendar; BLS `latest=true` and
     ECB `updatedAfter` as cheap "is anything new?" checks before a full
     fetch. Never the BLS website: it refuses programs by policy.
   - **Rules not yet written:** families stay on age (the broad HICP set has
     candidate countries that publish later), as do JOLTS, PPI, the regional
     Feds, the OECD, the IMF and the World Bank's annual data.
3. **Publishers that refused on 2026-10-11, and what liveness says now.** The
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
   it is keyed, so liveness does not see it. **Destatis** stopped serving its
   CPI tables on 2026-10-12 (GENESIS code 7000, "no data cube", for every
   start year) while industrial production answered; `de_cpi_destatis` holds
   to 2026-08. **On 2026-10-14 both were back:** a full refresh from home had
   no failures, the OECD answered every series and `de_cpi_destatis` reached
   2026-09. Keep the item until the Bundesbank and BEA are checked too.

4. **Jobless claims: revisit the Labor Department's API** when convenient. It
   matched FRED exactly but ended four weeks behind on 2026-10-10. If it
   catches up to the Thursday release, move the three claims rows to it (the
   fields are in `docs/engineering/notes.md`).

5. **Catalog gaps a real question exposed** ("how have 10-year government
   bonds done over two years" for the US, Germany, France, the UK, China and
   Japan). Japan's 10-year is in (session 10). Still open:
   - **Daily German yields:** the Bundesbank's BBSIS flow through the existing
     SDMX client, waiting on item 3. Without it Germany and France are only
     Eurostat's monthly averages, about six weeks behind.
   - **France daily:** Banque de France (Webstat) — not yet checked.
   - **China's 10-year:** probably no openly licensed source (ChinaBond is
     commercial). A candidate for `bedrok/seeds/gaps.md`, not the catalog.

   The same answer showed an assistant putting Bedrok's numbers and web
   numbers in one chart without saying the measures differ (Fed
   constant-maturity, BoE zero-coupon, Eurostat monthly averages, web point
   quotes).

6. **Review the warnings' weaker sources.** 11 of 43 cite a news outlet,
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

7. **The IMF WEO rows' freshness is their last non-forecast year** (session
   11), right for "is this stale" but not for "is this the latest vintage": a
   WEO update shows only as revised values. Freshness by vintage needs the
   publishers' vintages (session 13b). Also still open from session 5: live SDMX
   discovery beyond the catalog, and SQL over session-only series. A
   session-only `fetch_dataset` was proposed on 2026-10-12 to keep uncatalogued
   questions inside Bedrok; under "Bedrok answers what it can" that is no
   longer needed, and it is shelved; session A added the one line it left: the
   server instructions tell the assistant to label numbers fetched outside
   Bedrok and check them against a stored headline.

8. **Watch the Fed Board's release zips.** Bedrok reads the eight
   release-page zips, which the Board says outlive its Data Download Program
   (session B). `smoke` now checks every one; read a red Tuesday run on a
   `<REL> release zip` line as that file having moved or gone. SLOOS falls
   back to the survey's HTML table by itself. For the yields the recipe is
   Treasury's par and real curves (from 1990 and 2003; a re-point costs
   1962–1989 of history), for GBPUSD the ECB cross; the dollar index, G.17,
   H.6, H.8, H.4.1 and the charge-off rates have no non-FRED source and would
   freeze with their history and go on the gaps page. All in
   `docs/engineering/notes.md`, "The Fed Board is retiring…".

9. **Shrimp has stopped too, probably.** `wb_shrimp` ends 2023-10 and, as the
   oldest last date among the Pink Sheet series, sets every incremental Pink
   Sheet download back to 2023. Barley and sorghum were retired for the same
   reason (`pipeline.RETIRED`); shrimp is the owner's call.

10. **Starter-set gaps from session 4:** Italy's NIC CPI (rebased in 2026, its
   history sits in a differently shaped flow — Eurostat's HICP covers Italy),
   Istat's headline confidence indices, the ABS monthly CPI (only from 2024;
   the quarterly index from 1948 is carried). The ILO and the Bundesbank were
   left for later.

11. **Eurostat's government debt/GDP family is late:** freshest 2026-01-01,
    past its 260-day allowance. The only `validate` finding. Data, not a fault;
    check whether the release moved before widening the allowance.

12. **Two 7a series are not what one might assume.** Japan's unemployment rate
    is not seasonally adjusted: the seasonally adjusted series are not in
    e-Stat's API database. USD/CAD from the Bank of Canada starts only in 2017.
    Both are said in their notes; a better source for either is welcome.

13. **`backfill` exits 1 on failures while `refresh` exits 3.** Align them when
    a supervisor needs to read `backfill`.

14. **Test coverage gaps:** nothing tests `rates_infl`, `backfill`, or the sync
    stages end to end. `smoke`'s verdicts are tested (session 12); whether the
    live publishers answer is the liveness run's job, not the suite's.

15. **The `.mcpb` bundle is built by hand** for each release. Building and
    attaching it in `publish.yml` is one job (setup-node, setup-uv,
    `scripts/build-mcpb.sh`, `gh release upload`); do it once a manual release
    has proved the artifact.

16. **The liveness run checks keyless publishers only.** The six keyed ones
    (BEA, Census, EIA, FRED, Destatis, e-Stat) would need their keys as
    repository secrets, and a secret-holding workflow must never run on
    `pull_request`.

17. **A fresh store lists 436 series, not 531.** `list_series` on a new install
    counts only what the catalog load knows before any bulk stage has run: the
    World Bank's 50 Pink Sheet series (and a few Eurostat family members)
    appear after their first download. Each answers when asked by id, so
    nothing is lost, but the README's "more than 500" is only true of a store
    that has refreshed once. Either load the bulk families' rows at catalog
    load, as session 11 did for Eurostat, or count them in `list_series`.

18. **`scripts/mcp_smoke.py` can pass for the wrong reason.** Started from the
    repo folder it finds `./profile` and answers from the developer's own
    store, not the empty home it was given (it happened in session 12). It
    should fail when the profile it reports is not under the `HOME` it was
    started with, unless told otherwise. CI is safe today only because a fresh
    checkout has no `profile/`.

19. **The later sessions in `docs/rebuild-plan.md` are unplanned:** 13
    (13a, the release calendar, in progress as item 2; 13b, revisions, deferred
    2026-10-12) and 14 (the
    maintenance loop: triage source requests from the issue form, retire stale
    publishers, a second pass over the register for the next thirty).

