# Handover: pricing rethink and the public website (2026-11-19)

Written at the end of a chat session (Claude on claude.ai, working on the
owner's folder) for the next Claude Code session. It is dated evidence, like
the other files in `docs/research/`: do not rewrite it later. Open items are
in `STATE.md` (item 19), not here.

## Read first

1. `STATE.md`: "Decisions in force" (the new first bullet) and item 19.
2. `docs/rebuild-plan.md`: "Decisions of 2026-11-19: a public website" and
   session 15.
3. This file, for why.

## What the session asked

Could Bedrok copy GetLeads' play? GetLeads is Ryane Tully's B2B contact
database, used through MCP. It beat Apollo and ZoomInfo by offering
*unlimited* data while they charge per credit. Incumbents can't copy that:
switching to unlimited would destroy their revenue. The owner asked whether
Trading Economics and similar macro APIs for individuals could be attacked
the same way.

## What was found

Prices as of 2026-11-19, from each provider's own page unless noted:

- **Trading Economics:** about $149/month Standard and $299/month
  Professional, billed yearly (a third-party comparison from June 2026; the
  real page needs a login). The API is priced by "features, volume of
  requests and the distribution you make". 1 request per second, at most
  10,000 rows per call. 20 million indicators, 196 countries. Its free
  website tier is very generous.
- **EODHD:** macro data starts only on the $59.99/month plan, with 39 global
  indicators. 100,000 calls/day.
- **FXMacroData:** $50/month for an individual, $250/month for a team.
  1,000,000 requests/month. Redistribution costs from $10/month per 100
  measured users. Already has an MCP server.
- **QVeris:** credit-based, built for agents. Pro is $19 for 10,000 credits,
  with overage at $0.002 per credit.
- **Haver and Macrobond:** enterprise, no public prices. Macrobond sells
  300M+ series, 150M+ point-in-time series and revision history.

Conclusions:

- **Call caps don't bind in macro.** The data is small and fetched once. A
  million calls a month is effectively unlimited, so "unlimited" isn't a
  weapon. What incumbents really charge for is the seat, coverage tiers and
  redistribution rights.
- **No cost gap to exploit.** GetLeads' edge was a scarce, licensed input
  bought cheaply. Macro inputs are free to everyone, so unlimited is a
  baseline, not a moat. Bedrok is already unlimited and free locally.
- **The analogy that does hold is the redistribution meter.** Incumbents
  charge per downstream user for data that is mostly public domain or
  attribution-licensed at source (34 of Bedrok's 35 publishers). That points
  at people who *publish* macro (newsletters, research houses, think tanks)
  as a later paid market.
- **Breadth is Bedrok's real gap against Trading Economics:** 777 series
  against 20M indicators.

The owner's direction: copy Trading Economics' funnel. A free public website
that people use every day is the hook, and the paid product later is the
API. Pricing the API is deferred.

## The nearest existing site: Econ Toolkit (econtoolkit.com)

Checked in Chrome on 2026-11-19 (it refuses automated fetches):

- 42 economies (G7, all of the EU and the euro area, China, India, Brazil,
  Mexico, Türkiye, South Africa, Korea).
- The same seven topic pages per country: growth, inflation, labour market,
  monetary policy, banking, fiscal and balance of payments.
- A compare page (up to 10 countries) and a custom chart builder.
- Refreshed every weekday, with a visible "data refreshed" time.
- Every chart downloads as PNG or Excel and has share buttons (X, LinkedIn,
  WhatsApp, email).
- No API, no MCP, no pricing; only an `llms.txt` for AI. No warnings, no
  release dates, no licence per series.
- Its sources include "the Federal Reserve via FRED", and its terms page
  leaves source terms to the reader.

Bedrok's edge on a site like this is the trust layer (warnings, publisher
release dates, licence per series) and serving a site, an API and an MCP
from one store. Bedrok's weakness is that its catalog is keyed by series id,
not by country then topic. That layer is new curation work.

## Decisions taken (now in STATE.md and the plan)

- **Bedrok gets a public website.** Free to browse, chart and download. It
  is the hook for a paid API later.
- **Hosting is fine.** This reverses "nothing hosted" for the website. The
  owner accepts being the redistributor, and the running cost.
- **FRED data goes on the website, at the owner's risk.** The local MCP and
  its package keep FRED session-only, and `test_macro_seed.py` still guards
  that. So the hosted store must be separate from the shipped package's
  storage rules, not a change to them.
- **The hosted store runs on a clock,** driven by the release rules and
  publisher calendars from session 13a. The local install keeps no clock.

The local MCP is unchanged and stays the product. The website sits beside
it.

## The mockup

The owner has a Claude Design canvas, "Bedrok website — basic layout"
(private to the owner: https://claude.ai/artifact/6aBquGoWPwVUqry5cFaF69).
It has two artboards, both deliberately simple:

- **Home:** nav with topics, Compare, Release calendar, and "API · coming
  soon". A hero line, a country picker and an "updated" time. Five headline
  cards for the US, each with its publisher and next release, and a "Read
  with care" chip on inflation. A "Coming up" release list, three "why trust
  these numbers" points, and a footer with countries plus a reuse line.
- **United States · Inflation:** breadcrumb, title and a one-sentence
  summary. A headline and core CPI chart, Jan 2021 – Sep 2026, with October
  2025 shaded as missing, a range toggle and a source line. Buttons for PNG,
  CSV, copy link and share on LinkedIn. A side panel with the warning, an
  "About this data" box (publisher, licence, latest period, next release,
  history from 1913) and an API teaser
  (`GET /v1/series/us_cpi_nsa?transform=yoy`, waitlist). A list of related
  series below.
- **Look:** white ground, Space Grotesk headings, Inter body text, the logo
  orange (#FF6B1A) as the accent with black text on it. Headline line
  #E8590C, core dashed #1F4E79.

Every figure came from Bedrok's own tools on 2026-11-19:

- GDP growth 3.4% (Q3 2026, q/q annualised, BEA; next release 28 Jan 2027
  on BEA's schedule).
- CPI 3.7% and core 2.6% (Sep 2026).
- Unemployment 4.1% (Oct 2026).
- Fed target upper bound 4.00% (moved on 17 Sep 2026).
- 10-year yield 5.10% (17 Nov).

The owner wants richer versions next. The options offered were the Compare
page, the release-calendar page, a mobile layout, and chart hover and range
controls. He has not picked one.

## Found along the way

- **October 2026's CPI annual rate can't be computed.** Bedrok holds the
  October 2026 index, but `get_series(transform="yoy")` returns no October
  2026 value, because October 2025 does not exist (the shutdown gap). The
  latest annual rate the site can show is September 2026. Check what the BLS
  itself published for October 2026, and decide what the site and the MCP
  should say. A related oddity: in that call, the monthly `table` ended at
  2026-09 while `freshness.last_date` said 2026-10-01. That is consistent
  with the missing base month, but worth a look.
- **The handover branch is not pushed.** The decisions commit and this file
  are on the local branch `decisions-public-website`. The chat could not
  push: the Linux machine on the Mac has no GitHub login, and the session
  had no push access. Push it and open the PR from the Mac
  (`git push -u origin decisions-public-website && gh pr create --fill`).
  The test suite was not run (the chat could not run the macOS environment;
  the change is documents only); CI runs it on the PR.
- **Leftover files:** an untracked `.claude/` folder was in the tree before
  this session and was left alone. Four `__init__.py` files are attached to
  the claude.ai Project as documents, two under `build/lib/`. The Project
  instructions say to ignore Project documents.

## Questions the owner has not answered yet

- Which rows the site shows beyond FRED. Philadelphia Fed rows are
  personal-use and the regional Feds are noncommercial; a free site that
  leads to a paid API may count as commercial.
- Where the hosted store runs, and how the site is built from it (static
  pages regenerated on release, or a live app).
- The country-then-topic structure: which topics, and which series headline
  each one.
- The API's shape and price (deferred by the owner).
