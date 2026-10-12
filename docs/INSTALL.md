# Install Bedrok

Bedrok is a local MCP server: your AI assistant asks it for economic data, and
it fetches each series from the publisher that produces it into a store on
your own machine. **No keys are needed to start.** Nothing is hosted and
nothing is shared; your copy fetches for your own use.

## Claude Desktop

1. Download `bedrok-<version>.mcpb` from the
   [latest release](https://github.com/KrustyKronk/bedrok/releases/latest)
   and open it. Claude Desktop shows an
   "Install extension" dialog.
2. Leave every field blank to start, or fill in any of the optional keys
   below. They are kept in your system keychain.
3. Ask Claude something: *"Where does euro-area inflation sit in its history?"*

The first start takes 20–30 seconds while Claude Desktop downloads Python and
Bedrok's libraries; after that it starts in about a second. To change keys or
the data folder later: Settings → Extensions → Bedrok.

## Claude Code

```bash
claude mcp add bedrok --scope user -- uvx --python 3.12 bedrok serve
```

Add a key with `--env`, keeping another option between it and the name
(`claude mcp add --env FRED_API_KEY=… --scope user bedrok -- uvx …`), or put
keys in the `.env` file of Bedrok's folder (below). Needs
[uv](https://docs.astral.sh/uv/getting-started/installation/).

## Any other MCP host

A stdio server:

```json
{
  "command": "uvx",
  "args": ["--python", "3.12", "bedrok", "serve"],
  "env": {"FRED_API_KEY": "optional", "BEA_API_KEY": "optional"}
}
```

`--python 3.12` matters: left to itself, uv may pick a Python so new that
DuckDB has no ready-built wheel for it, and then compiles DuckDB from source
for many minutes.

## Keys: all optional

Everything below works without any key: US rates and the Federal Reserve's
releases, US labour and prices, the euro area country by country, the UK, the
OECD, the BIS, the IMF, national statistics offices, commodities, futures
positioning and FX. Free keys add:

| key | unlocks | get it |
|---|---|---|
| `BEA_API_KEY` | US GDP, PCE prices | <https://apps.bea.gov/API/signup/> |
| `CENSUS_API_KEY` | US retail sales, durable goods, housing, trade | <https://api.census.gov/data/key_signup.html> |
| `EIA_API_KEY` | US energy prices | <https://www.eia.gov/opendata/register.php> |
| `FRED_API_KEY` | series only FRED publishes (credit spreads, VIX, jobless claims), fetched live and never stored | <https://fredaccount.stlouisfed.org/apikeys> |
| `DESTATIS_API_KEY` | Germany's national CPI and industrial production | <https://www.destatis.de/EN/Service/OpenData/api-webservice.html> |
| `ESTAT_API_KEY` | Japan's CPI and unemployment rate | <https://www.e-stat.go.jp/api/en> |
| `BLS_API_KEY` | lifts the BLS limit from 25 to 500 requests a day; expires yearly | <https://data.bls.gov/registrationEngine/> |

Ask Bedrok for a series whose key is missing and it says which key and where
to get it. The full list of publishers, with their terms, is
[SOURCES](SOURCES.md); what each licence asks of you is [LICENSES](LICENSES.md).

## Where your data lives

One folder, created on first use:

- macOS: `~/Library/Application Support/bedrok/`
- Linux: `~/.local/share/bedrok/`

It holds `data/market.duckdb` (the store) and, optionally, `.env` (keys, one
`NAME=value` per line) and `profile.toml`. To keep it somewhere else, set the
folder in Claude Desktop's Bedrok settings, or `BEDROK_PROFILE=/path` for any
other host. To move it, quit your assistant, move the folder, and point the
setting at the new place.

The store fills as you ask: each series is fetched the first time it is
needed and refreshed when a newer release should exist. Ask Claude to
"refresh Bedrok" to bring everything up to date at once; it runs in the
background.

## Uninstall

Remove the extension (Claude Desktop: Settings → Extensions) or the server
(`claude mcp remove bedrok -s user`), then delete the folder above.

Bedrok measures and contextualises. It never gives investment advice — see
[DISCLAIMER](DISCLAIMER.md).
