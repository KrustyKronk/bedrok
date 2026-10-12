# Bedrok

**Public macroeconomic data, in your AI assistant.**

Bedrok is a local MCP server. Ask Claude — or any assistant that speaks MCP —
where euro-area inflation sits in its history, how far US real yields have
risen this year, or what the BIS credit gap says about China, and it answers
from more than 500 curated series with deep history: US rates and the Fed's
releases, US labour and prices, the euro area country by country, the UK, the
OECD, the BIS, the IMF, national statistics offices from Canada to Japan,
commodities, futures positioning and FX. Each series is fetched from the
publisher that produces it, into a store on your own machine, with its licence
and the line to credit it with. Percentiles always come with their sample, and
charts come with a recipe so they look the same in every assistant.

## Install

**Claude Desktop:** download `bedrok-<version>.mcpb` from the
[latest release](https://github.com/KrustyKronk/bedrok/releases/latest) and
open it.

**Claude Code** (needs [uv](https://docs.astral.sh/uv/getting-started/installation/)):

```bash
claude mcp add bedrok --scope user -- uvx --python 3.12 bedrok serve
```

**Any other MCP host:** a stdio server, `uvx --python 3.12 bedrok serve`.

**No keys needed to start.** Six free keys add US GDP and PCE, retail and
housing, energy, Germany's and Japan's national statistics, and the series
only FRED publishes. Details, keys and the data folder: [INSTALL](docs/INSTALL.md).

## The shape of it

- **Local, and nothing hosted.** Each install fetches its own data from the
  publishers; nobody redistributes anything.
- **The publisher's terms travel with the number.** Every series carries its
  licence class and attribution; series whose terms forbid storing them (FRED's)
  are fetched live and never written. See [SOURCES](docs/SOURCES.md),
  [LICENSES](docs/LICENSES.md) and the reasoning in [LICENSING](docs/LICENSING.md).
- **It says when a number is in doubt.** Documented cases where an official
  figure should not be taken at face value — a suspended release, a
  restatement, a break — ride along with the answer. See
  [WARNINGS](docs/WARNINGS.md) and what no open source provides, [GAPS](docs/GAPS.md).
- **Fetch on demand.** A series is fetched the first time it is asked for and
  refreshed when it goes stale.
- **Never advice.** Bedrok measures, contextualises and premise-checks. You
  decide. See [DISCLAIMER](docs/DISCLAIMER.md).

## What it is not

Not financial advice and never a buy/sell signal. No equity prices — official
rates, FX, energy and positioning are the markets layer. Not hosted: there is
no Bedrok server anywhere but your own machine.

## Contributing

A new publisher is mostly a row in a CSV. [CONTRIBUTING](CONTRIBUTING.md) says
how, and the [source request](https://github.com/KrustyKronk/bedrok/issues/new?template=source-request.yml)
form is the place to ask for one. Developer setup is [SETUP](docs/SETUP.md).
The project is built in sessions with an AI assistant; `CLAUDE.md` holds its
rules and `STATE.md` where it stands.

## Docs

[INSTALL](docs/INSTALL.md) · [SOURCES](docs/SOURCES.md) · [LICENSES](docs/LICENSES.md) ·
[WARNINGS](docs/WARNINGS.md) · [GAPS](docs/GAPS.md) · [CHARTING](docs/CHARTING.md) ·
[SETUP](docs/SETUP.md) · [LICENSING](docs/LICENSING.md) ·
[DISCLAIMER](docs/DISCLAIMER.md) · [Plan](docs/rebuild-plan.md) ·
[Engineering notes](docs/engineering/notes.md)

Python ≥3.11 · macOS and Linux · MIT licence for the code; the data keeps its
publishers' terms. By Andreu Paddack.
