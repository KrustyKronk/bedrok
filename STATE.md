# Where Bedrok stands

**Updated 2026-11-23.** 0.3.12 is the latest release (2026-11-23, on PyPI,
with the Desktop bundle on the GitHub release), and Claude Desktop runs it.
The work in hand is the public website, built in its own private repository
from this package (item 21).

Bedrok is a local MCP server and store of public macroeconomic data, each
series fetched from its publisher. The plan is `docs/rebuild-plan.md`; the
reasoning behind it is `docs/research/2026-10-09-open-bedrok-brainstorm.md`.
What each session and release built, and how it was proven, is
`docs/history.md`; read it only when a question sends you there.

**Next:** the public website (item 21). Then try in Claude Desktop what 0.3.4
to 0.3.12 added (item 25), a cold question on a release morning (item 1), and
whichever open item the owner picks.

**Owner-side items** — the ones that need the owner's accounts or describe one
machine — live in `profile/STATE-private.md`, which is gitignored. This file
holds only what any reader may see.

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
PyPI, then builds the Desktop bundle and attaches it to the release (from
0.3.2; before that it was built by hand). **Never change the
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

**34 publishers, plus `derived`** (`bedrok/seeds/sources.csv`), from the Fed
Board, the ECB, the BLS, the BIS, the regional Feds, the OECD, Eurostat, the
EIA, Census, the IMF, the World Bank, the CFTC, the ONS, the Bank of England,
the Bank of Canada, the New York Fed, INSEE, the BEA, Statistics Canada,
Japan's e-Stat and Ministry of Finance, the ABS, INE, the four Nordic offices,
Destatis, Istat, the Bundesbank, the National Bank of Kazakhstan, its Bureau
of National Statistics and JODI, with FRED for the nine session-only series.
3,124 catalog series on a fresh install (0.3.12), most of them Eurostat's
every-country families and the IMF, BIS and OECD world layer (items 22 to 24).

**Twelve commands:** `smoke [--keyless]`, `backfill [--only ids --stage S]`,
`refresh`, `validate`, `query`, `tieout [--only ids]`, `doctor`, `schema`,
`pctile`, `rates`, `serve`, `docs [--check]`.

**Tests:** 577, network-free, green on 2026-11-23 (a few skip without a built
wheel or bundle, or the owner's term list), including the MCP server in memory, the layering rule and the publication gate. **Workflows:**
`tests` (push and PR), `package` (PR: the empty-home install and the gate on
the wheel), `publish` (a published release), `liveness` (Tuesdays, keyless).
None holds a secret.

**No clock beyond liveness.** The store is current when someone runs
`bedrok refresh` or the server re-fetches a due or stale series on demand.

## Decisions in force

- **Bedrok gets a public website** (the owner, 2026-11-19). Free to browse,
  chart and download every series; it is the hook for a paid API later
  (pricing deferred). Hosting is fine, which reverses "nothing hosted" for the
  website; the running cost is accepted. **FRED data goes on the site**, the
  owner accepting the risk under FRED's terms for now; the local MCP and its
  package keep FRED session-only, still guarded by `test_macro_seed.py`. **The
  hosted store refreshes on a schedule** from the release rules and calendars;
  the local install keeps no clock. Detail in `docs/rebuild-plan.md`,
  "Decisions of 2026-11-19". **It is built in a separate private repository**
  (same day); this one stays public and stays the MCP. Item 21.
- **Bedrok answers what it can, and does not stand in the way of the rest**
  (the owner, 2026-10-12). A question Bedrok can answer should be answered
  from Bedrok: it is purpose-built for it, and a bug that sends Claude
  elsewhere when the data is in the store is a bug. What Bedrok does not
  carry, Claude may fetch natively (search, a web fetch, another tool);
  Bedrok never forces itself onto an answer and never tries to stop that.
  Whenever outside data sits beside Bedrok's, in a chart or a sentence, the
  answer says which part did not come through Bedrok.
- **The rethink decisions of 2026-10-09** are in `docs/rebuild-plan.md`: local
  MCP (still the product; the website is added beside it on 2026-11-19), FRED as an optional extra held only for the conversation, equity prices
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
- **A policy rate is judged by its bank's decisions** (session I,
  2026-11-05), not by a rule: due after a decision the store does not show,
  fresh until the next. Meeting dates come from each bank's own page, never a
  list kept by hand (the owner's rule of 2026-10-11 for Fed decisions).
- **Freshness is age and the next release** (2026-10-15). Statuses: `fresh`,
  `due`, `late`, `stale`, `empty`, `session`; a family member its publisher's
  release left out carries `next_release.behind` (2026-11-23), the
  publisher's delay, not a late release. A series without a release rule
  is judged by age exactly as before; staleness overrides due and late, so a
  wrong rule cannot hide a broken feed. Rule timings are the publisher's usual
  practice (`approximate: true`, "usually" in every message) wherever no
  publisher calendar is read (item 2).
- **Never advice, never timing signals.** See `docs/DISCLAIMER.md`.

## Open, in the order I would take them

Completed items are removed rather than ticked — git history and
`docs/history.md` are the record. The numbers are stable labels (bedrok-web,
code comments and other documents cite them), so a closed item keeps a
one-line placeholder. Owner-side items are in `profile/STATE-private.md`.

1. **A cold question on a release morning.** What has not run outside this
   machine's development server is the fetch on a due series. In Claude
   Desktop (0.3.12), ask "what is out today?" on a release morning, then ask
   for the figure.

2. **More release calendars** (notes, "Where release dates come from" and
   "What changed on 2026-11-23"). Still open:
   - **Istat**: its press calendar is a public Google Calendar (URL in the
     notes). Wiring it needs `it_unemployment`'s allowance raised from 90 to
     100 days in `macro_catalog.csv` (Istat publishes about 32 days after the
     month).
   - **The broad HICP and unemployment families** (`hicp_yoy_`,
     `unemp_rate_`) could now have rules: Eurostat's calendar file dates
     their releases, and late countries read as behind, not late.
   - **Cheap "is anything new?" checks** (the BLS API's `latest=true`, the
     ECB's SDMX `updatedAfter`), in the adapters; not done.
   - **`us_gscpi`** stays on age: the NY Fed states no update day.
   Never the BLS website: it refuses programs by policy.

3. *Closed 2026-10-16.*

4. **Jobless claims: revisit the Labor Department's API** when convenient.
   It matches FRED but ran a steady four weeks behind (2026-10-16: newest
   week 2026-09-12 against FRED's 2026-10-10). If it catches up, move the
   three claims rows to it (fields in the notes).

5. *Closed 2026-11-23:* France's daily 10-year is not carried (no reuse
   licence; `gaps.md`); China's monthly is `oecd_10y_cn`, its daily curve
   in `gaps.md`.

6. **Five warnings still rest on press or analysis**, each saying so in
   `source_name`: Russia's customs trade, Russia's oil and gas output,
   Russia's statistics generally, Venezuela, and Kazakhstan's crude and
   condensate split. Retry from a network that reaches the Russian sites or
   web.archive.org.

7. **The IMF WEO rows' freshness is their last non-forecast year**, right for
   "is this stale" but not for "is this the latest vintage": a WEO update
   shows only as revised values. Freshness by vintage needs session 13b.
   Also open from session 5: live SDMX discovery beyond the catalog, and SQL
   over session-only series.

8. **Watch the Fed Board's release zips.** `smoke` checks all eight; read a
   red Tuesday run on a `<REL> release zip` line as that file having moved
   or gone. The re-point recipes are in the notes, "The Fed Board is
   retiring…".

9. **Starter-set gaps:** Italy's NIC CPI (Eurostat's HICP covers Italy),
   Istat's confidence indices, the ABS monthly CPI (only from 2024). The ILO;
   the Bundesbank's other flows (the rest of the Bund curve, German banking
   statistics) are a row each.

10. *Closed 2026-10-16.*

11. **Two series are not what one might assume:** Japan's unemployment rate
    is not seasonally adjusted (e-Stat's API lacks the adjusted one), and
    USD/CAD from the Bank of Canada starts only in 2017. Both are in their
    notes; a better source for either is welcome.

12. *Closed 2026-11-23.*

13. **The liveness run checks keyless publishers only.** The keyed ones
    (BEA, Census, EIA, FRED, Destatis, e-Stat) would need their keys as
    repository secrets, and a secret-holding workflow must never run on
    `pull_request`. The owner's call.

14. *Closed 2026-11-23.*

15. **Unplanned sessions in `docs/rebuild-plan.md`:** 13b (revisions and
    vintages, deferred) and 14 (the maintenance loop: triage source requests,
    retire stale publishers, a second pass over the register).

16. **Kazakhstan** (37 series; scope in
    `docs/research/2026-10-28-kazakhstan-scope.md`). Open:
    - Armenia and the Kyrgyz Republic have no mirror-trade rows (Eurostat has
      no values; Comext was unreachable).
    - JODI and the ministry disagree on crude and condensate (March 2025);
      cause unknown, both in the catalog notes.
    - Watch Taldau's house prices (wide rule, lag 8 to 50 days) and GDP (no
      consistent calendar); narrow the rules once a pattern shows.
    - Not carried, with reasons in the history: the activity gauge's growth
      rate, real wages before 2021, annual history before 2010, real retail.

17. **Z.1 asked on demand costs about 13 s a series** (the 37.6 MB zip is
    parsed again for each; a refresh fetches it once). Worth it only if a
    cold session asking several Z.1 series feels slow.

18. **The logo, unseen in hosts:** whether Desktop's list draws it well at
    small sizes, and which other hosts draw `serverInfo.icons`.

19. **Central banks' meeting dates.** Open:
    - **Kazakhstan's 2027 schedule page is not up yet**; a refresh in January
      fails, keeping the stored dates, until it is.
    - **Banks not read:** the SNB (no future dates found) and the People's
      Bank of China (no fixed schedule). The Bank of Canada's page lists only
      the next announcement.
    - **The ECB's hold rule rests on an assumption** (a hold counts once the
      rates were fetched on or after the effective Wednesday). Watch the
      17 Dec 2026 decision: when does the new row appear?
    - **Not carried:** which FOMC meetings come with projections, minutes'
      dates, press conference times.

20. **Destatis's `de_cpi_destatis` refused on 2026-11-23** (Status 7000, "no
    data cube" for `61111-0002`, as on 2026-10-12; notes, GENESIS). The stored
    history to October stands. Recheck before changing anything; if it
    persists past the November flash, look for a CPI rebase. Bot checks come
    and go (OECD, StatCan): read one red run as "look", weeks of red as
    "retire or re-point", and never try to get past one.

21. **The public website** is built in the private repository
    `KrustyKronk/bedrok-web`, whose `STATE.md` holds its open questions. It
    depends on this package (`bedrok>=0.3.12`) and relies on the internals
    `tests/test_web_contract.py` pins: a change that fails that test breaks
    the site, so change bedrok-web in step. Open on this side:
    - **October 2026's US CPI annual rate cannot be computed** (October 2025
      was never published), so `yoy` stops at September 2026 while
      `freshness.last_date` says 2026-10-01. Check what the BLS published for
      October 2026's annual rate and decide what the MCP says.

22. **Eurostat's every-country families.** Open:
    - **Ten series are behind:** Estonia and Malta's sentiment (to April
      2026), Albania's production and retail, and youth unemployment for
      Bosnia, Switzerland and Romania (to June). Any still frozen around
      2027-02-19: drop it from the seed.
    - **Re-run `scripts/eurostat_geos.py` when Eurostat adds or drops a
      country**; a seeded country that stops publishing makes its family
      download the whole history on every refresh.
    - **Unscoped searches are long** (36 countries for "industrial
      production"); not yet tried in Claude Desktop (item 25).

23. **The world layer (IMF, BIS, OECD).** Open:
    - **37 series are stale, truthfully** (the WEO has no 2026 figures for a
      few economies; some BIS and OECD series lag).
    - **The OECD families stay on age**: its release pages refuse programs,
      so its timing is unread.
    - **The BIS condition binds the paid API:** BIS statistics must add
      nothing to a commercial product's price (`docs/LICENSING.md`).
    - **Re-run `scripts/world_geos.py`** when a publisher adds or drops a
      country.

24. **The standard-indicator families** (`unemp_rate_`, `oecd_gdp_yoy_`,
    `bis_usd_rate_`). Behind, truthfully: 11 BIS exchange rates not updated
    since early 2026 or before, and Switzerland's and Bosnia's unemployment.

25. **Try 0.3.12 in Claude Desktop.** Installed on 2026-11-23 over 0.3.3, so
    0.3.4 to 0.3.12 reach Desktop for the first time. Not yet asked there:
    - something about Kazakhstan and the Z.1 financing gap;
    - "when does the FOMC meet next?", and the day after a decision, the rate;
    - an unscoped Eurostat question and a world one (Brazil's inflation or
      exchange rate), to see how the longer searches read;
    - whether the extension list draws the logo well.
