# The rethink: Bedrok as an open macro-data MCP

**Agreed 2026-10-09.** The reasoning — what was read, the premise checks, the
peers, the consequences — is in
`docs/research/2026-10-09-open-bedrok-brainstorm.md`. That document is dated
evidence and is not rewritten. This one is the plan, and it changes as the plan
changes. It replaces the 2026-08-22 plan to make Bedrok a portfolio monitor,
which is in git history; the portfolio itself left for its own folder on
2026-10-08.

Open items and current position live in `STATE.md`, not here.

---

## What we are building and why

An open-source tool that connects anyone's AI assistant to the free, public
sources of economic and financial data — statistics offices, central banks,
international organisations — through one local MCP server. Each user runs
their own copy on their own machine with their own free keys, so each user is
fetching the data for their own use and nobody is redistributing it.

The hard part is not credentials. Of the 595 sources in the source
register that a personal, automated tool can use, 499 need no key at all. The
hard part is knowing which series exists where, under which code, in which
units, how stale it is allowed to be, under which licence, and whether the
official number can be trusted. That catalog, and the trust layer around it,
is the product. Bedrok already has a small version of both.

## Decisions

Taken by the owner on 2026-10-09.

- **A local MCP server.** Runs on the user's machine and is started by their AI
  app. Nothing hosted: a hosted Bedrok would make its operator the
  redistributor, and a key-holding website would make its operator the custodian of
  other people's accounts.
- **FRED is an optional extra**, used under the user's own key and held only
  for the conversation, never saved. Its terms forbid storing its content in a
  database and using it in connection with building AI systems. The same US
  numbers come from the producing agencies directly, in the public domain.
- **Equity prices are out of the core** in the first version. The open
  financial layer is official: Treasury curves, central bank rates and FX,
  energy and commodity prices, futures positioning.
- **Python stays**, installed with `uv`.
- **A fresh public repository** with a clean history, not this one rewritten.
- **About thirty publishers for v1**, curated well, with live search for the
  long tail.
- **No pack.** Each install fetches a series from its publisher the first time
  it is asked for and re-fetches only when a newer release should exist.
  Nobody runs a schedule.
- **The name stays: Bedrok.** Free on the Python package index on 2026-10-09.

## The sessions

Each is one session, one branch, one PR, one thing.

**1 — Strip the portfolio.** *(2026-10-09)* Delete the broker importers, the
portfolio engines, the monitor page and its seeder, news, fundamentals, ETF
holdings, the daily held-ticker sync, the KV layer, and the scripts for jobs
that no longer run. Tests green at the smaller count; the real store still
refreshes.

**2 — One local store, no bundle.** *(2026-10-09)* Collapse the three running modes (owner,
subscriber, solo) into one local store. Delete the Friday pack build, the
download step and the storage bucket's role. Drop the price tables and the
stock-price fetchers. The owner's copy is rebuilt from the publishers directly
with the owner's own keys.

**3 — Go to the US agencies instead of FRED.** Split in two on 2026-10-09,
when checking the live endpoints showed the Fed Board retiring its Data
Download Program (it points users at FRED) and Census requiring a key.

**3a — The keyed agencies and the tie-out.** *(2026-10-09)* Connections to the
Bureau of Labor Statistics, the Bureau of Economic Analysis, the Census Bureau
and the New York Fed's markets data. A tie-out command that compares a
re-pointed series with what the store holds before the switch, and a store
that replaces a re-pointed series' history outright instead of stitching two
sources together. `restricted_no_pack` renamed `personal_use`. The BLS and NY
Fed series moved; BEA and Census rows move once tied out with a key.

**3b — The Fed Board files, the rest, and FRED demoted.** *(2026-10-10)* The
Fed Board's per-release files (Treasury yields from 1962, fed funds, the dollar
index and GBPUSD, industrial production, M2, bank credit, the balance sheet,
loan surveys, delinquencies); the Chicago, Cleveland, Dallas, Atlanta and
Philadelphia Fed files and FHFA; spreads, breakevens, the 5y5y forward and the
NBER recession flag computed rather than fetched; GDP, PCE, retail, orders,
housing and trade from BEA and Census once the owner's keys arrived. A `cache`
column — store or session — and a `noncommercial` licence class. The nine
series with no timely source but FRED (the four personal-use ones, the St.
Louis stress index, VIX, weekly jobless claims) are session-only: fetched
live, never stored. The store holds no FRED content.

**4 — The source list and the shared-format connection.** *(2026-10-10)* One
list of publishers, `bedrok/seeds/sources.csv`: how to reach each, its key,
licence, storage rule, terms page, attribution, pacing and the series `smoke`
checks it by — read by the pipeline, the licence layer and `smoke`, so a new
publisher is a row. One SDMX client carries the ECB and OECD (tied out
identical) and five new publishers: the BIS, the IMF (stored, with its
LLM-training clause on every row), Istat, INSEE and the ABS, 29 starter series.
Barley and sorghum retired. The ILO and the Bundesbank wait.

**5 — The MCP server.** *(2026-10-10)* Seven tools: which sources are wired, search, describe,
fetch, where a value sits in its history, free-form queries over what is saved,
refresh. Fetch on demand; re-fetch only when stale. The owner's own Claude uses it
in place of the sandbox skill. Built on the official `mcp` SDK v2; a full
refresh runs as a background job because Claude Desktop gives a tool call about
a minute; registered in Claude Desktop and Claude Code, and the `bedrok` skill
became a pointer to the tools.

**6 — Packaging and the zero-key first run.** *(2026-10-10)* A one-line install for Claude
Code, a double-click bundle for Claude Desktop with optional boxes for each
key, a settings snippet for other assistants. Proven on a clean machine: install,
ask one question with no keys, get an answer. An install guide, a generated page
of every wired source with its licence and key requirement, and the generated
licence notices.

**7 — The second wave, to thirty publishers, plus the warnings.** Split in
two on 2026-10-10. **7a** *(2026-10-10)*: nine publishers with their own
formats — the Bank of Canada, Statistics Canada, INE, Destatis and e-Stat
(the last two with free keys) on adapters of their own, and the four Nordic
offices on one PxWeb client — 22 series, 29 publishers in all. **7b** *(2026-10-10)*:
43 warnings in a seed of their own, so that a case about a country Bedrok does
not carry still has somewhere to live, loud or quiet by severity and period;
the gaps note; and the menu review (explicit staleness on every row, one units
vocabulary, geography-first names). As first planned: The
publishers with their own formats (the Nordic offices, Statistics Canada, the
Bank of Canada, Destatis, Spain's INE, Japan's e-Stat). A review of the whole
menu for names, units and staleness limits. A warnings column seeded from the
register's documented cases of official numbers that should not be taken at
face value, and a short note on what no open source provides.

**Sessions 8 to 11 come from using it.** On 2026-10-11 the owner ran a real macro
session through Claude on the web (credit spreads, Treasuries, the Fed, Brent,
European growth and inflation) and logged it. The trust layer held: caveats,
warnings and provenance changed the answers for the better, and nine questions
in ten were answered from the store. What failed was everything around the
numbers: every chart needed the assistant to line up daily, monthly and
quarterly data by hand, the charts looked different every time, answers carried
analysis nobody asked for, and a few questions hit data Bedrok does not hold.
These four sessions come before opening it, because a stranger's first
impression should be the fixed version. The owner's decisions (2026-10-11): two
vertical scales are the standard when units differ; units go in the subtitle
and on the vertical axis; answers are short by default, and more comes on
request; Fed decisions come from data, not a hand-kept list.

**8 — Lining up the data.** Bedrok puts series of different frequencies on one
calendar itself: weekly closes with a holiday rule, monthly and quarterly
averages or sums, missing periods left visibly empty rather than filled. Long
histories stop being cut at 500 rows, the limit applies per series, and any cut
says which end was cut. The engine the charts stand on; the hardest of the four
to size, so it may split.

**9 — Charts and answers.** Every chart request returns a chart recipe: title,
subtitle with units, the aligned data, which series goes on which side, and the
source line already written. The house style is written down and served by
Bedrok itself, so it reaches every host, Claude on the web included, where
nothing on the owner's Mac travels: a card, a title, units in the subtitle and on
the vertical axis, two scales when units differ (main solid on the left, second
dashed on the right), the legend at the bottom with series that can be hidden,
the source line under it, a chart/table switch, and a chart by default. The
server's instructions carry the short-answer rule (the chart and a couple of
lines; warnings always spoken; analysis on request) and a nudge to use
`percentile`, which the logged session never called. Checked by the owner
re-running the logged questions on the desktop and on the web.

**10 — The data the questions needed.** Six additions, each from a publisher
Bedrok already reaches: inflation by component (energy, food, goods, services,
core) for about thirty European countries from Eurostat, plus INE's own core
rate for Spain; quarter-on-quarter GDP for every European country; the ONS's
official quarterly UK GDP; US CPI unadjusted, as the headline is published; the
Fed's target range, daily from 2000, from the New York Fed (decisions are the
days it changes); and a note on both Brent series that they are the physical
Dated Brent price, not the futures price the news quotes, with the futures
price added to the gaps page.

**11 — Tidying the catalog.** One id per concept where the same series exists
twice; a proper way to list the whole catalog, without the 100-item cut-off,
and a one-call summary by publisher, country and topic; search that treats
country names and codes as places, not letters; no future "latest" dates for
forecast series; the empty euro-area rows filled or removed.

**12 — Open it.** *(2026-10-12)* A fresh public repository with one clean first
commit, after an audit of every file for anything personal, kept honest by a
publication test over the tree and the wheel. A contributor guide built around
the source list: a new publisher is a row plus an inline-payload test (recorded
cassettes were judged not worth a dependency). A weekly check that fetches one
series from every keyless publisher, so a moved website is a failure and a
frozen one a warning rather than a silent gap. Version 0.2.0 on the Python
package index by trusted publishing. The old repository archived with a
pointer. A source-request issue form, taken early from 14.

**13a — The release calendar** (brought forward on 2026-10-15, when the
September CPI came out and Bedrok served August as fresh). When each series'
next period is due, so a due one is fetched before it is answered and a late
one is said: release rules first, then the publishers' own calendars, then a
tool for "when is the next CPI?". Where it stands is in `STATE.md`.

**Later, 13b — Revisions.** What a number was when first published, from the
agencies' own archives, so the assistant can say "this was revised" rather
than quietly using today's figure.

**Later, 14 — The maintenance loop.** Source requests triaged from the issue
form (added in 12), stale publishers retired, and a second pass over the register for the
next thirty.

## What changes for the owner along the way

- **After 2:** the owner's copy no longer depends on the Friday job and refreshes with
  one command.
- **After 5:** the owner talks to Bedrok through Claude Desktop, and the
  sandbox skill retires.
- **After 9:** charts look the same in every host, and "show me"
  gets a chart, not an essay.
- **After 12:** the old repository is archived, and the Friday job, the storage
  bucket and its token can be deleted.

## Deliberately deferred

A downloadable starter bundle; equity prices as an optional add-on; anything
hosted, including a remote MCP endpoint; any paid data.
