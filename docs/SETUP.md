# Developer setup

This is for working on Bedrok from a checkout. **To use it, see
[INSTALL](INSTALL.md)** — no checkout, no keys, no setup.

Bedrok is one local store. Every install fetches each series from its
publisher — statistics offices, central banks, international organisations —
into a single DuckDB file in its home folder. Nothing is downloaded from a
shared bundle and nothing is hosted.

## 1. Install

```bash
git clone https://github.com/KrustyKronk/bedrok && cd bedrok
python3 -m venv .venv && ./.venv/bin/pip install -e .   # Python ≥3.11
./bin/fetch-binaries.sh                                  # optional: pinned DuckDB CLI (not in git)
```

Packaging: `uv build --wheel` builds the wheel (the seeds in `bedrok/seeds/`
ship inside it), `scripts/build-mcpb.sh` builds the Claude Desktop bundle, and
`python3 scripts/mcp_smoke.py -- <server command>` drives any build over stdio
the way a host does. `.github/workflows/package.yml` runs the wheel through
that on clean macOS and Linux runners. After editing a seed, run
`bedrok docs` to regenerate `docs/SOURCES.md` and `docs/LICENSES.md` (a test
fails if you forget).

## 2. Make a profile

A profile (Bedrok's home folder) is `profile/` when you run from the repo
(it is gitignored), otherwise `$BEDROK_PROFILE`, otherwise the per-user data
folder. Everything in it is optional.

```
profile/
├── profile.toml     # optional settings
├── .env             # optional keys (chmod 600)
└── data/            # created on first run; holds market.duckdb, the store
```

`profile.toml` — every key has a default:

```toml
[owner]
name = "Your Name"

[base]
currency = "EUR"          # EUR, USD or GBP; the FX crosses for all three are stored

[macro]
hicp_europe = true        # the broad-Europe HICP set (~44 geos)
commodities = true        # the World Bank Pink Sheet
positioning = true        # CFTC Commitments of Traders
```

`.env` — free keys, all optional:

- `BLS_API_KEY` — from data.bls.gov/registrationEngine. BLS answers without
  one, but only 25 requests a day per address; a first backfill of the BLS
  series costs about twelve of them. With a key: 500 a day. **The key expires
  after a year** and has to be re-registered.
- `BEA_API_KEY` — from apps.bea.gov/API/signup. For GDP and the PCE price
  indexes (once those rows move off FRED).
- `CENSUS_API_KEY` — from api.census.gov/data/key_signup.html. Census no
  longer answers without one. For retail sales, durable goods, housing and the
  trade balance (once those rows move off FRED).
- `FRED_API_KEY` — from fredaccount.stlouisfed.org. For the nine series
  Bedrok takes only from FRED (the ICE and Moody's credit spreads, Michigan
  sentiment, the St. Louis stress index, VIX, and weekly jobless claims). They
  are fetched live when asked for and never stored — FRED's terms forbid it.
  See [LICENSING](LICENSING.md).
- `DOL_API_KEY` — from data.dol.gov. Not used yet: the Labor Department's API
  matches FRED's claims but runs weeks behind, so claims come live from FRED.
- `EIA_API_KEY` — from eia.gov/opendata. Unlocks the eight daily US energy
  series.
- `DESTATIS_API_KEY` — a GENESIS-Online token, from
  destatis.de/EN/Service/OpenData/api-webservice.html. For Germany's national
  CPI and industrial production; the guest login no longer returns data.
- `ESTAT_API_KEY` — an e-Stat application ID, from e-stat.go.jp/api/en. For
  Japan's CPI and unemployment rate.

Without a key, the series that need it are skipped and reported as such; the
run does not fail. BLS, the Federal Reserve Board, the New York Fed and the
other regional Feds, FHFA, the ECB, Eurostat, ONS, the Bank of England, the
OECD, the World Bank and CFTC need no key at all.

### When a series moves to a new source

A series keeps its `series_id` when the catalog re-points it to a different
publisher. Before the refresh that picks up such a change, run

```bash
bedrok tieout          # new source vs what the store holds; writes nothing
```

and read anything marked CHECK. The next `refresh` then pulls the series in
full from its new source and replaces the old history outright, rather than
topping it up — two publishers' histories are never stitched together. If the
new source fails or its key is missing, the old history stays and the swap is
retried next time.

## 3. First run

```bash
bedrok backfill        # deep history from every publisher, ~10 minutes
bedrok doctor          # the store, its size, how fresh each stream is
bedrok validate        # coverage, licence and staleness audit
```

Then, whenever you want newer data:

```bash
bedrok refresh         # incremental: re-fetches a short overlap window per series
```

`backfill --only us_nfci,us_10y` re-pulls named series in full; `backfill
--stage eurostat` re-runs one bulk stage. Both are idempotent.

## Use it from Claude

`bedrok serve` is a local MCP server over stdio: seven tools (`list_sources`,
`search_series`, `describe_series`, `get_series`, `percentile`, `query`,
`refresh`) over your store. It fetches a stale series from its publisher
before answering, fetches session-only series live without storing them, and
runs a full refresh in the background when asked.

**Claude Code** (all projects):

```bash
claude mcp add --env BEDROK_PROFILE=/path/to/Bedrok/profile --scope user bedrok -- /path/to/Bedrok/.venv/bin/python -m bedrok serve
```

Keep another option between `--env` and the name, or the CLI reads the name
as one more `KEY=value`.

**Claude Desktop**: **quit the app completely first (⌘Q — closing the window
is not enough)**, then add to `~/Library/Application Support/Claude/claude_desktop_config.json`
under `mcpServers` (back the file up first; it holds your other connectors),
then open the app. A running Desktop keeps the config in memory and writes it
back over the file when it next saves its preferences, silently dropping an
entry added while it ran. Local servers appear under Settings → Developer, not
in the Connectors list (that list is claude.ai's cloud connectors).

```json
"bedrok": {
  "command": "/path/to/Bedrok/.venv/bin/python",
  "args": ["-m", "bedrok", "serve"],
  "env": {"BEDROK_PROFILE": "/path/to/Bedrok/profile"}
}
```

Desktop starts servers with an almost empty environment and no working
directory, so the command is an absolute path and the profile is named in
`env`. Check it before restarting, exactly as Desktop will run it:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | env -i BEDROK_PROFILE=/path/to/Bedrok/profile /path/to/Bedrok/.venv/bin/python -m bedrok serve
```

The server's log is `~/Library/Logs/Claude/mcp-server-bedrok.log`. A terminal
`bedrok refresh` and the server cannot write at the same moment (DuckDB allows
one writer); the server answers "the store is busy" until the terminal is done.

## Adding a publisher

How to test a new publisher and open the pull request is in
[CONTRIBUTING](../CONTRIBUTING.md).

Every publisher is one row of `bedrok/seeds/sources.csv`: how to reach it, its
key (if any), its licence class, whether its numbers may be stored, its terms
page, the line to credit it with, how to pace it, and the catalog series
`bedrok smoke` checks it by.

- **It speaks SDMX** (most central banks, statistics offices and international
  organisations): add a row with `protocol = sdmx`, its `base_url` and
  `format` (`accept` for SDMX-CSV via the Accept header, `csvdata` for the
  ECB's dialect, `xml` for SDMX-ML only), then add its series to
  `bedrok/seeds/macro_catalog.csv` with `source_code` set to the path after
  `/data/` — `FLOW/KEY` or `AGENCY,FLOW,VERSION/KEY`. Pin every dimension: a key
  that matches more than one series is refused rather than stored.
- **It speaks PxWeb** (the Nordic statistics offices, and many others that
  run the same software): add a row with `protocol = pxweb`, its `base_url` and
  `format` (`v2` for PxWebApi 2, `v1` for PxWeb 1, `statbank` for Statistics
  Denmark's dialect), then add series with `source_code` set to the table and
  a selection naming every dimension, `TABLE?Dim=value&Tid=*`. `*` is for the
  time dimension only: a selection that crosses two series is refused.
- **It has its own format**: add a row with `protocol = adapter` and write
  `bedrok/ingest/adapters/<adapter>.py` with `fetch(code, start=None)` (or
  `fetch(code, key, start=None)` when the row names a `key_env`), returning a
  DataFrame of `date` and `value`.

Then `bedrok smoke` checks the new publisher, `bedrok refresh` fills its
series, and `tests/test_sources.py` checks the row is complete. Read the
publisher's terms before choosing `license` and `cache`.

## Moving the store

`$BEDROK_DB` points Bedrok at a different store file, and `$BEDROK_PROFILE` (or
`--profile PATH`) at a different profile. A sandbox that mounts the repo
read-only can copy the store to `/tmp` and set `BEDROK_DB` to the copy;
`bedrok doctor` prints the exact recipe when it hits a read-only mount.
