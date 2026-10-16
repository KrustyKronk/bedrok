# Engineering notes

Things that cost somebody hours to find out. Not state — state is in `STATE.md`.
Not rules — rules are in `CLAUDE.md`. This is the file that stops the same tax
being paid twice.

Add to it whenever a session burns time on something a sentence would have
prevented. Instance-specific detail belongs in `profile/`, not here.

---

## The sandbox

Bedrok is normally driven from a Cowork session: a macOS host with a sandboxed
Linux shell, the repo mounted at `/sessions/<id>/mnt/Bedrok`.

**The mount permits write and truncate but not unlink.** This is the single most
expensive fact about the environment. `rm` on a file under the mount returns
`Operation not permitted`; `: > file` succeeds. DuckDB tries to *remove* the
`.wal` on connect, so every `config.connect()` after the first write in a session
dies with `IOException: Could not remove file ".../market.duckdb.wal"`. The
first write of a session works and the second does not, which makes it read like
an intermittent fault rather than a permission model.

The survivable route is `BEDROK_DB` pointed at a
copy in `/tmp`, then an explicit `CHECKPOINT` to fold the WAL in, then
`shutil.copyfile` back to the mount, then `: >` the stale mount-side `.wal`.
Writes made this way die with the session unless they are copied back — one
session lost a day of writes that way.

**A leaked `.wal` is usually the *previous* command's unhandled exception.** The
two blocking refresh bugs of August 2026 were one bug: a parse
failure raised past the driver, which skipped `con.close()`, which left a `.wal`,
which made the next `connect()` fail on the un-unlinkable mount. It presented as
two unrelated faults because the symptom appeared one command later than the
cause. When a write path can raise, it needs `try/finally` more for the next
command's sake than for its own — `store.close_checkpointed()` is the close to
use.

**The repo `.venv` is macOS-only.** Its shebang targets a Mac python3.12; the
sandbox is python3.10. Run `python3 -m bedrok`, never `.venv/bin/bedrok`.
`pyproject.toml` already says this.

**The DuckDB binary is arch-split on Linux and universal on macOS.** `uname -m`
says `aarch64` in the sandbox and `arm64` on a Mac, so a mapping that only knows
the Linux names silently builds a path to a binary that is not there. The
selection line that works:

```bash
BIN=bin/duckdb-linux-$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
```

Picking `duckdb-linux-amd64` on aarch64 gives `Exec format error`, which reads
like a corrupt download rather than the wrong file.

**A binary staged into `/tmp` can vanish mid-session.** Observed working at the
first turn and `No such file or directory` several turns later. Staging must be
idempotent — re-check and re-stage rather than assuming.

**Reproducing CI locally needs a clean clone, not just a different directory.**
`config._resolve_default_dir()` falls back to the repo-root `profile/` when cwd
is elsewhere, so a developer machine always finds a profile and CI never does —
`profile/` is gitignored, so a fresh checkout has none. A test that reads the
ambient profile therefore passes for a reason that is not under test. To run the
suite as CI does:

```bash
git clone --branch <branch> /path/to/Bedrok /tmp/ci-check && cd /tmp/ci-check
env -u BEDROK_PROFILE /path/to/.venv/bin/python -m pytest -q
```

**A green badge on the repo does not mean the tests pass.** When several
workflows run, a red one hides behind green ones: `main` once sat red for
eighteen days because the only runs after the failure were another workflow's
successes, and a list of green runs reads as green CI. Check the workflow name,
not the colour.

**The bash tool has a 45s cap** and a cold `pip install duckdb pandas numpy`
exceeds it. Background it and poll with an import check. Roughly 40s of every
session goes to this; it is the longest-standing unfixed friction in the project.

**Embedding Bedrok in a script** is three lines, and the obvious guess is wrong —
`config.load_profile()` does not exist:

```python
sys.path.insert(0, str(repo_root))
from bedrok import config; config.set_profile(profile_dir)
con = config.connect(read_only=True)
```

## Structure

**Derive the repo root from the package, never by counting `.parent`.**
`Path(__file__).resolve().parents[2]` is the parent of the `bedrok` package and
survives a module moving between layers. `.parent.parent` silently became
`bedrok/` the moment `config.py` moved into `core/`, and every seed path went
missing at once — eleven tests, one cause, and the error (`FileNotFoundError` on
a seed CSV) pointed nowhere near it.

**A worktree borrows the main checkout's `.venv`, and its package with it
unless the worktree is the current folder.** Claude Code sessions run in
`.claude/worktrees/<name>/`, which has no `.venv` of its own; the main
checkout's has an editable install of the main checkout. `python -m bedrok`
and `python -c` put the current folder first on `sys.path`, ahead of
`PYTHONPATH`, so started from the main checkout they run its code, and a
script file puts its own folder first. Run from the worktree folder, set
`BEDROK_PROFILE` to the real `profile/` for the real store, and print
`bedrok.__file__` first. Session E ran a full refresh of the wrong code for
several minutes before the printed path gave it away.

**Untracked scripts under `profile/` break silently and there is no history to
recover them from.** Two have bitten now: `build_artifact.py` crashed on every
run for an unknown stretch because only a skill invoked it, and
`fetch_legacy_prices.py` invoked `bedrok/fetch.py` by file path and broke the
moment the package was layered. Nothing tests them, CI never sees them, and
`git log` has nothing to say about them. When one of them turns out to be
load-bearing, move it into the package. Until then, have scripts call
`python3 -m bedrok <command>` rather than a file path, because a module path
follows the module and a file path does not.

**`$?` after a pipeline is the last command's status, not your program's.**
`cmd | tail -8; echo $?` reports on `tail`. Every "exit code 0" that contradicts
a visible failure message is this. Check with `cmd >/dev/null 2>&1; echo $?`.

**A Bedrok command's exit code separates findings from faults.** `validate`
returns 1 whenever it has findings, which is the audit working; `refresh`
returns **3** when the store was not fully updated (2 is bad arguments), because
it collects per-series failures rather than raising them and so leaves no
traceback behind. A supervisor that classifies steps by exit code needs each
step to distinguish "I found something" from "I did not do my job"; graceful
error handling throws away the traceback a supervisor might otherwise sniff for.

**Capture a step's output to a file rather than letting it stream.** stderr is
unbuffered and stdout is block-buffered once redirected into a file, so a
traceback lands ABOVE the summary line it invalidates and the log reads as
though the run succeeded after failing. `cmd >"$out" 2>&1; rc=$?; cat "$out"`
fixes the ordering and lets you inspect the output before deciding what the
status meant. `refresh.py` already carried a comment about this exact trap.

**zsh MULTIOS tees output.** `cmd 2>&1 1>/dev/null` does not isolate stderr the
way it does in bash — zsh duplicates to both. Use `2>/dev/null` to see stdout
alone and trust nothing inferred from the other form.

**A CLI dispatch table is a pile of strings.** `_COMMANDS` in `__main__.py` maps
a command name to a module path, so a rename breaks that command and nothing
else — no import error, no failing test, until someone runs it.
`tests/test_layering.py::test_every_cli_command_resolves` imports all of them.

## Pandas and SQL traps

**`reindex(cal).ffill()` silently drops pre-window history.** A plain reindex onto
a window calendar discards earlier rows, so the forward-fill has nothing to carry
and day one is NaN whenever the window opens on a market holiday. This produced a
350% return for one window because opening value collapsed to a fraction of its
true figure. The correct form keeps the old index alive through the fill:

```python
s.reindex(s.index.union(cal)).ffill().reindex(cal)
```

**`float(x or 0)` returns `nan` when `x` is `nan`.** NaN is truthy. It propagated
into a cost basis and turned every point of six curves into `None`. Guard with an
explicit `pd.isna` check, never with `or`.

**A stride like `[::step]` does not guarantee the final observation.** A tie-out
check that reads the last element of a sampled array can silently be reading a
mid-series value. Force `len(idx)-1` into the keep list.

**`first` and `last` are reserved words in DuckDB.** `min(date) first` is a
parser error. So are `month`, `year` and `value`. Quote or rename.

**pandas 2.x idioms only** — `resample('ME')`, no `groupby(axis=1)`. Clip W-FRI
resample labels to the end of the data or you invent a future week.

## GitHub

**A public repo means anyone can fork and open a PR, so no workflow that touches
secrets may run on `pull_request`.** Here no workflow holds a secret: `liveness`
checks keyless publishers only, `publish` authenticates to PyPI by OIDC from a
release event, and `tests` and `package` reference no secret at all. Check this every time a
workflow gains a trigger.

**`runner.temp` is not available in job-level `env`,** only inside steps, and
GitHub rejects the entire workflow file rather than the one expression.

## Ingest and vendors

**Some series are dated in the future, and an incremental start must not be.**
The ECB's Survey of Professional Forecasters stores forecasts against the
period they forecast, so `ea_spf_infl_1y` carries observations into the next
year. An incremental start of "last date minus a week" is then a future date,
and the ECB answers `400 Bad Request`. In the old pack build this failed every
Friday and nobody saw it, because the workflow's failure-count gate never
matched the refresh's actual wording — the series simply froze. It surfaced on
2026-10-09 only because `refresh` now exits 3 on any failed item.
`pipeline._incremental_start` caps the start at today minus the overlap.

**An incremental store freezes history at the vintage it was first fetched.**
`refresh` re-fetches only the last week of each series, so every older point is
whatever the publisher said on the day of the first backfill. Seasonally
adjusted surveys, national accounts and every model-based index (financial
conditions, supply-chain pressure, term premia, leading indicators) are
re-estimated across their whole history at each release, so an old store drifts
from what the publisher shows today without any error anywhere. A full
`backfill` is the only thing that brings history back to the current vintage.

**Judge a rebuild by correlation, not by equality.** On 2026-10-09 a fresh
backfill was compared with the old pack: 88 series differed on older dates by
more than a 0.5% relative tolerance, which read as failure. Relative difference
is the wrong yardstick for survey balances and growth rates that sit near zero,
where 0.1 against 0.2 is "100%". The test that separates a revision from a bug
is whether it is still the same series: all 88 correlated at 0.991 or better
with their old values (80 at 0.999 or better), and 61 moved by less than a tenth
of a standard deviation. A wrong mapping would show a weak correlation or
jumps of a full standard deviation. The same comparison also caught a genuine
publisher change: the World Bank now shows "…" for barley and sorghum after
August 2020, though an earlier Pink Sheet carried values to 2026.

**A re-pointed series is replaced, never topped up.** When the catalog moves a
series to a new publisher, an incremental refresh would fetch only the last
week from the new source and leave decades of the old one underneath — a level
splice nobody chose. `macro_catalog.filled_from` records which source the
stored history came from; `sync_macro` treats any mismatch as a re-point, pulls
the full history and swaps it in one transaction. A failure, an empty answer or
a missing key leaves the old history and `filled_from` untouched, so the swap
is retried on the next run. Run `bedrok tieout` *before* that refresh: it is
the only moment the old history is still there to compare against.

**BLS without a key is 25 requests a day per address, and it is easy to spend.**
Session 3a's probes, one tie-out (twelve ten-year windows from 1913) and one
`smoke` used the whole allowance, and the refresh that should have swapped the
BLS series in failed on every one with `REQUEST_NOT_PROCESSED` — returned with
HTTP 200, so it has to be read from the body. The adapter batches every BLS
series into one request per window (`bls.prime`) precisely because the unit of
cost is the request, not the series. With `BLS_API_KEY`: 500 a day, 50 series
and 20 years per request. The key expires yearly.

**Check seasonal adjustment in the series id, not the title.** The research
note mapped FRED's ECIALLCIV to `CIU1010000000000I`; the tie-out showed 177.244
against the store's 177.178. `CIU` is not seasonally adjusted, `CIS` is. BLS
encodes adjustment in the prefix letter across its surveys (`CUSR`/`CUUR` for
CPI, `LNS`/`LNU` for the household survey).

**DOL's r539 claims report runs three weeks behind the weekly news release.**
Its values match FRED's ICSA/CCSA exactly, but on 2026-10-09 it ended at the
week of 2026-09-12 while FRED, fed by the Thursday release, had 2026-10-03. For
the highest-frequency recession gauge that lag is the whole point lost, so the
claims rows stayed on FRED. The candidate is the data.dol.gov API (launched
February 2026); the r539 endpoint is a form POST at
`oui.doleta.gov/unemploy/wkclaims/report.asp` (`level=us`, `filetype=xml`),
with thousands separators and non-breaking spaces for weeks not yet reported.

**The Fed Board is retiring its Data Download Program; Bedrok never used
it (session B, 2026-10-12).** The adapter reads the release-page zips
(`federalreserve.gov/releases/{rel}/data/FRB_{rel}_xml.zip`, eight of them:
H15, H10, G17, H6, H8, H41, SLOOS, CHGDEL), and the Board's notice
(`federalreserve.gov/data/data-download-fred-information.htm`, 2026-07-16)
says "Historical data will remain available for download as XML files on
statistical release pages". What goes: the interactive "Build Your Package"
the week of 2026-11-09, the pre-built DDP packages "later this year and in
2027", the program itself after that, undated. All eight zips answered on
2026-10-12 (H.15 is 4.3 MB, 263 series from 1954; H.4.1 is 9.1 MB and its
series names are not FRED's). What was done, and what to do when a zip goes:
- *`smoke` now HEADs every zip* (`fedboard.release_status`), beyond the SLOOS
  download the smoke series makes, and prints each file's Last-Modified date;
  a 404, or an HTML page answering in the zip's place, FAILs the Tuesday run.
- *SLOOS is the exposed one:* its page (`/data/sloos.htm`) offers no zip, only
  the survey's chart-data HTML (`/data/sloos/sloos-YYYYMM-chart-data.htm`,
  every survey since 1990:2, headers spanning columns, "n.a." for gaps). If
  the zip goes, `fetch` reads that table (`parse_sloos_html`, `SLOOS_HTML`
  maps the four series to a panel heading and a column by words). Tied out
  on 2026-10-12: all 146, 146, 140 and 123 stored observations equal.
- *The yields:* Treasury's par curve, one CSV per year (the all-years URL is
  refused by Akamai): `home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{YYYY}/all?type=daily_treasury_yield_curve&field_tdr_date_value={YYYY}&page&_format=csv`,
  real yields with `type=daily_treasury_real_yield_curve`. US dates, newest
  first, columns keyed by header because tenors were added over the years
  (1990 has 3 Mo to 30 Yr with no 1 Mo, 2 Mo or 20 Yr; the real curve starts
  2003 with 5, 7 and 10 YR). A US government work, public domain by default;
  no terms page states it. A re-point costs 1962–1989 of H.15 history
  (re-pointed series are replaced, never stitched), so only if the zip goes.
- *GBPUSD:* `("GBPUSD", "derived", "EURUSD/EURGBP")` in `config.fx_pairs()`,
  placed before USDGBP; `_derived_fx` already divides. ECB reference rates
  fix at 14:15 CET against H.10's noon New York, and start in 1999 against
  1971. The Bank of England's XUDLUSS is excluded from the OGL (third-party
  FX under licence), so it is not an option.
- *No non-FRED source exists* for the dollar index (H.10), G.17, H.6, H.8,
  H.4.1 or the charge-off rates, and FRED can never be stored. If one of
  those zips goes, the series freezes with its history and joins the gaps
  page; the Board's crosswalk to FRED ids is at
  `federalreserve.gov/data/documents/DDP-FRED%20Data%20Series%20Crosswalk.csv`
  for a session-only read.
- The download cache held four zips for eight releases, so one run could
  fetch H.10 twice (GBPUSD in `sync_fx` is never primed); it holds eight now.

**Census rejects keyless calls by redirecting, not by erroring.** A request
without a key is answered with a 302 to `missing_key.html`; `requests` follows
it and gets a 200 HTML page. The adapter checks the final URL. The path is
`/data/timeseries/eits/<dataset>`; the old `/data/eits/` is gone. Variable
metadata (`…/variables.json`) still answers without a key.

**The Fed Board's release zips are one format, but not one convention.** Every
`FRB_{REL}_xml.zip` is SDMX 1.0 compact XML: `<Series SERIES_NAME FREQ
UNIT_MULT>` holding `<Obs TIME_PERIOD OBS_VALUE OBS_STATUS>`, so one streaming
parser (`fedboard.py`) reads them all. What differs: monthly (FREQ 129) and
quarterly (162) observations are dated to the period END; `ND` marks holidays
with a placeholder value; G.17 repeats `IP.B50001.S` three times; H.8 is in
thousands of dollars where H.4.1 is in millions; H.4.1 unzips to ~127 MB. The
Board's own crosswalk (`DDP-FRED Data Series Crosswalk.csv`) maps every FRED id
we used. A loose name match picks the wrong series silently — `V0.JRXWTFB_N.B`
is a discontinued dollar index that correlates 0.996 with the live one — so
match names exactly. The zip can run a day behind FRED: on 2026-10-10 FRED had
the 9 October yields and the H.15 zip, built that afternoon, ended on the 8th.

**A publisher can move a file and leave the old one answering.** The Chicago
Fed's `chicagofed.org/-/media/…/nfci-data-series-csv.csv` still returned 200 on
2026-10-10 with data frozen at April 2026; the live file is on
`api.data.chicagofed.org`. A status code proves nothing — only the last date
does, which is why `smoke` checks NFCI's date and `validate` checks staleness.

**The regional Feds' terms are not the Board's.** The Board's statistics are
federal works; the Reserve Banks are not federal agencies, and their files carry
their own terms. Chicago, Cleveland, Dallas and Atlanta allow reuse only with
credit and not for gain (`noncommercial`); Philadelphia says "all rights
reserved" (`personal_use`). Read the terms page, not the URL's domain.

**FRED rejects a browser-shaped User-Agent that has no browser behind it.**
Requests to fred.stlouisfed.org with Bedrok's Chrome-like string timed out
where plain curl worked, and dol.gov answered 403. `_util.BROWSER_UA` is right
for the NY Fed and the Bank of England and wrong for these two.

**A series that becomes session-only must be deleted, not just skipped.**
Skipping it in sync would leave years of FRED's copy in the store. `sync_macro`
deletes the history of any `cache = session` row it still finds stored, and
`validate` counts any survivor as a finding. The same move also removes a
derived id's FRED history: `sync_derived` replaces each series in full rather
than upserting over it.

**Census's EITS API wants things it does not document.** Every dataset but
`marts` answers 400 without `time_slot_id` in `get` and without `for=us:*`;
without the geography, `resconst` and `ressales` also return each census region
under the same codes as the national total. A predicate (`category_code=…`) is
echoed back as a second column of the same name. The codes are not the
guessable ones (housing starts are category `ASTARTS`, data type `TOTAL`), and
`advm3` is the *advance* durable goods report — FRED follows the full `m3`
report, which revises it weeks later. The trade balance (`ftd`) starts in 1995
where FRED's copy starts in 1992. And never `raise_for_status()` on a keyed
Census or DOL call: the error text carries the URL, and the URL carries the key.

**Every free Labor Department claims feed lags the Thursday release.** The r539
report and the data.dol.gov API (`apiprod.dol.gov/v4/get/ETA/
ui_national_weekly_claims/json?X-API-KEY=…`; the key only works as a query
parameter, not a header; fields `c5` SA initial, `c8` its 4-week average, `c7`
SA continued) both matched FRED exactly back to 1967, and both ended at the
week of 2026-09-05 on 2026-10-10 while FRED had 2026-10-03. They also list
future weeks carrying only seasonal factors. Claims are therefore session-only
from FRED; revisit if the API catches up. Rechecked 2026-10-16, the day after a
Thursday release: the API's newest filled week was 2026-09-12, FRED's initial
claims 2026-10-10, so the gap is a steady four weeks, not a backlog clearing.
Sort with `sort=desc&sort_by=rptdate` and skip rows whose `c5` is null; the
first rows are next year's seasonal factors.

**Every SDMX publisher has a dialect, and the dialect is data.** One client
(`adapters/sdmx.py`) serves the ECB, OECD, BIS, IMF, Istat, INSEE and ABS; what
differs between them is a column of `sources.csv`, not code. Seen live on
2026-10-10: the ECB answers the standard SDMX-CSV Accept header with 406 and
needs `format=csvdata` (its CSV has a `KEY` column instead of `DATAFLOW`);
INSEE serves only SDMX-ML; the Bundesbank and Norges Bank use `;` and a BOM;
the IMF's 3.0 path ignores `startPeriod` and answers in SDMX-CSV 2.0, so use
2.1, where its months are spelt `2026-M06`; OECD and INSEE send the newest
observation first; ABS answers "no data" with a 404 and Eurostat a future
start with a 400. `sdmx1` (Apache-2.0) handles all of this too; a thin client
was chosen so pacing, retries, the User-Agent and error text (which must never
carry a key) stay ours, and so the one-line install of session 6 does not pull
a heavy dependency.

**A key that matches two series is an error, not data.** SDMX happily returns
every series a partial key matches, stacked in one CSV. The OECD once
interleaved three CLI definitions that way; Istat versions every release as a
new EDITION, so an open edition position returns the same series once per
release, with different values. The client refuses any answer whose non-time
dimensions take more than one value — except a dimension the source list names
in `latest_of` (Istat's `EDITION`), where it keeps the newest. Editions sort by
their numbers: as text, `2026M7G30` sorts after `2026M10G1`.

**A publisher rebase can leave the history in another dataflow.** Istat moved
its NIC CPI to base 2025 in 2026; the new flow holds 2026 only, the history
1996–2025 sits in a differently shaped flow. Joining them is a stitch, so the
Italian NIC is not carried; Eurostat's HICP covers Italy.

**One dead series sets the incremental start for its whole bulk file.** The
Pink Sheet stage starts its download at the OLDEST last date among `wb_*`
series. Barley and sorghum ended in 2020, so every refresh re-read six years;
retiring them (`pipeline.RETIRED`) fixed it, and shrimp, which ends 2023-10,
now plays the same part.

**An error message can carry a key.** A `requests` error names its URL, and
FRED, EIA, BEA, Census and DOL take the key in the URL. `sources.redact()` strips
every configured key from failure text, and `pipeline.error_detail` redacts
BEFORE truncating — a key cut in half by `[:70]` would otherwise survive.

**Age cannot see a release: the next period lands two periods after the last
label.** A monthly point is dated the 1st, so August CPI is 08-01 and
September's release (mid-October) finds it ~75 days old; a quarterly point is
~210 days old at the next advance estimate. Any allowance short enough to fire
on release day fires for days before it in `validate`, and still cannot say
"due, not yet published". That is why 13a added release rules
(`core/releases.py`) instead of tightening `staleness_days`. The seed test that
holds each rule inside its allowance counts the gap from the last label to the
end of the next period (61 days monthly, 183 quarterly, 7 weekly, 3 daily),
then the lag, a roll and the grace.

**A retry hold must not start before the release.** `_ensure_fresh` used to
hold any series for six hours after an attempt, so a question at 07:00 ET that
found nothing kept the 08:30 CPI out until 13:00. An attempt older than the
series' `due_at` now never holds it back, and a due series is retried after
half an hour, a stale one after six.

**"Refreshed" said nothing about what arrived.** `pipeline.refresh_series`
reports `ok, N rows` where N counts the overlap it re-wrote, so `_ensure_fresh`
said "refreshed now from the publisher" whether or not a newer period came.
It now compares `freshness.assess` before and after, as the `refresh` tool
already did, and says "arrived" only when the last date moved.

**Flash estimates come before their month ends, and some datasets carry them.**
`ea_hicp_yoy` and `hicp_core_ea` (Eurostat `prc_hicp_minr`) and
`de_cpi_destatis` held September on 15 October, so they carry the flash; the
euro-area HICP index (`ea_hicp_index`) held only August, so it waits for the
final around the 17th. A flash rule therefore has a negative `lag_min`
(-2 for Eurostat's last-working-day flash, -4 for Destatis).

**The ONS dates an LFS three-month window by its middle month.** The series
JSON says so in each observation: `"date": "2026 JUN"` carries `"label": "2026
MAY-JUL"`. Session 13a's first part assumed the first month (an older note in
the owner's files said so) and set a rule of 100-112 days and an allowance of
180 on the back of it; the ONS's own `nextRelease` (20 October for Jun-Aug,
after 15 September for May-Jul) showed the gap is 77-81 days after the label
month ends. The rule is 74-88 and the allowance 155. Read the observation's
`label` before trusting any note about how a rolling window is dated.

**The BLS website refuses programs by policy, so its calendar is off limits.**
Every `www.bls.gov` page, the iCal file included, answers a program (any
User-Agent, browser-shaped or honest) with 403 and an "Access Denied" page
saying automated retrieval programs are not allowed. That is a stated policy,
not a bot check to get past; the sanctioned route is the API (`api.bls.gov`),
which has no release dates. BLS series stay on their rules.

**BEA publishes the previous month on the last day of the next one.** August's
PCE came out on 30 September, so the release for September is the first date
on or after 1 October, never "the first after 30 September": that one is
August's. `freshness._calendar` starts the search the day after the period
ends.

**Where release dates come from (researched 2026-10-15).** For the exact half of
13a. A calendar a program can read: BLS iCal
`https://www.bls.gov/schedule/news_release/bls.ics` (about two years ahead,
`SUMMARY` is the release name, `DTSTART;TZID=US-Eastern`, which is not an IANA
name; it answered 403 to curl even with a User-Agent, so test it through the
adapter's session before relying on it); BEA JSON
`https://apps.bea.gov/API/signup/release_dates.json` (release name → ISO UTC
times, into 2027); Statistics Canada JSON
`https://www150.statcan.gc.ca/n1/dai-quo/ssi/homepage/schedule-key_indicators-eng.json`
(title plus the reference period; the time in `date` is a sort key, releases
are 08:30 ET; curl works, some TLS clients fail its chain); INE iCal
`https://www.ine.es/dynt3/Calendario/en/calendario.ics` (`TZID=Europe/Madrid`);
Istat's press calendar is a public Google Calendar iCal. Inside a response
Bedrok already fetches: the ONS series JSON carries `description.nextRelease`
("21 October 2026"); StatCan WDS points carry `releaseTime`, e-Stat
`TABLE_INF.OPEN_DATE` and JSON-stat (Eurostat, the PxWeb four) `updated`, but
those say when the last release was, not the next. Cheap "anything new?"
checks: the BLS API's `?latest=true`, and the ECB's SDMX `updatedAfter=`
(404 means nothing new). HTML only: the Fed Board's monthly calendar pages
(`/newsevents/2026-november.htm`), Census, BoE, INSEE, Destatis, e-Stat, ABS,
the Nordic offices. Eurostat's documented `cache/RELEASE_CALENDAR/calendar_EN.ics`
is 404; its euro-indicators page is a calendar widget with an iCal export
behind it still to be found. The OECD's release-dates page sits behind the
same bot check as its data.

**The MCP SDK renamed its server in v2.** `mcp` 2.x (July 2026) calls it
`MCPServer` (`from mcp.server import MCPServer`); `mcp.server.fastmcp` no longer
exists, so most examples on the web fail on a fresh install. Tests run the
server in memory with `mcp.Client(server)`. `pyproject.toml` pins `mcp>=2,<3`.

**Claude Desktop gives a tool call about a minute.** It is not documented and
`MCP_TOOL_TIMEOUT` does not change it (Claude Code's limit is far longer). So
`refresh()` without ids starts a background thread and returns at once; the job
writes one series per connection, so a read waits seconds, not minutes — on
2026-10-10 a full refresh took 3.5 minutes and reads during it answered in
under 8 s.

**An MCP stdio server owns stdout.** One stray `print` corrupts the protocol.
The library paths the server calls never print (`print_report` is CLI-only;
`pipeline.refresh_series` and `REFRESH_STAGES` return report rows), logs go to
stderr, and `tests/test_mcp_server.py` asserts that a call writes nothing to
stdout.

**The server holds no connection between calls.** DuckDB allows one writer
process or many readers, never both: a held read-only connection would block
`bedrok refresh` in a terminal, and the reverse. Each tool call opens a short
connection under an in-process lock; another process's lock surfaces as "the
store is busy", never a crash.

**An lru_cache is forever in a long-lived process.** `fedboard._download`,
`fedfiles._download` and `bea._table` cache whole files for a CLI run's
lifetime, which is right for a CLI and wrong for a server: a refresh would
re-read yesterday's file. `pipeline.clear_download_caches()` runs at the start
of every refresh and every single-series refresh.

**A running Claude Desktop overwrites its own config.** The `bedrok` entry added
at 12:49 on 2026-10-10 was gone at 13:05: Desktop holds
`claude_desktop_config.json` in memory and writes it back when it saves its
preferences, so an entry added while the app runs is silently dropped and the
server is never launched (no `mcp-server-bedrok.log` appears at all — that
absence is the tell). Quit with ⌘Q, edit, then open. Closing the window does
not quit it.

**Desktop starts a server with an empty environment and no working directory.**
Absolute command path, `BEDROK_PROFILE` in `env`, and an `env -i` smoke test
before restarting the app (`docs/SETUP.md`).

**On a pandas row, `.name` is the index label, not the `name` column.** The
first catalog search matched nothing on series names because `row.name` was the
row number. Use `row["name"]`.

**Inside a project, `uv run` picks the newest Python it can get.** Building the
Desktop bundle on 2026-10-10, uv chose CPython 3.15.0, for which DuckDB had no
wheel (wheels stop at 3.14), and spent over ten minutes compiling DuckDB from
source. `uvx` from the same wheel had happened to pick 3.11. The bundle ships a
`.python-version` (3.12) and every documented `uvx` line says `--python 3.12`.

**Claude Desktop's `uv` bundle type installs Python and dependencies itself.**
`server.type: "uv"` (manifest 0.4) carries the source, `pyproject.toml` and a
`uv.lock`; Desktop downloads its own uv, runs `uv sync`, and starts
`uv run --directory ${__dirname} bedrok serve`. The bundle is ~200 kB; a cold
first start from an empty home took 19–22 s, and later starts about a second.
The `python` type would need every compiled wheel (duckdb, numpy, pandas)
vendored per platform — not workable.

**A blank optional bundle field may arrive as the literal `${user_config.x}`.**
`config.unset()` treats an empty value and an unfilled placeholder alike, for
the home folder and for every key. The data folder must sit outside the
bundle's own folder: an update deletes that folder.

**A fresh store must already know every id a question can name.** The first
install of the bundle answered `us_breakeven_10y` with "not in the catalog":
derived series and the Eurostat country families were registered only by their
bulk stages, which run on a full refresh, never on a first question. Now
`pipeline.static_catalog_rows()` registers them on every catalog load (insert
if missing, so a download's better names win); a derived series fetches its
empty inputs before computing; and a World Bank, CFTC or broad-HICP id, which
only a download names, runs that family's stage when first asked for.
`scripts/mcp_smoke.py` asks for a derived and a Eurostat series from an empty
home, so this stays proven.

**A blank bundle field hides the same key in `.env`.** Claude Desktop sets
every env var a bundle declares, so a key field left blank arrives as `''` or
`${user_config.x}`; `load_dotenv` never overrides an existing variable, so the
home folder's `.env` key was ignored and every keyed source read as unset
(found on a real install, 2026-10-10). `config.set_profile` now drops blank
and placeholder `*_API_KEY` variables before loading `.env`; a filled field
still wins over `.env`.

**Changing an extension's settings in Claude Desktop starts a new server
without stopping the old one.** After the folder field was set, two Bedrok
processes ran side by side, the first still on the default folder it started
with, answering some calls from a near-empty store. Quit Desktop with ⌘Q after
changing an extension's settings.

**Seeds outside the package do not reach a wheel.** `market/seeds/` sat beside
the package, so an installed copy died reading the catalog. The seeds are now
`bedrok/seeds/` (package data), found through `importlib.resources`, never by
counting parents from a file.

**The second-wave publishers (session 7a, 2026-10-10).** Four things cost time
and will again:

- *Base-year rebases replace tables and codes; they do not revise them.* At the
  turn of 2026, four of the seven keyless publishers rebased, and every old
  handle froze at 2025-12 while still answering 200 with good-looking data:
  - Statistics Sweden: CPIF 1987=100, `TAB5919`
  - Statistics Denmark: `PRIS111`
  - INE: CPI `IPC251852`; its unemployment code `EPA815` had stopped at 2023-Q4
  - Statistics Finland: its current base starts only in 2025

  Search the publisher's table list for the newest table, check its last
  period, and never trust that a code answering means a code being updated.
  `smoke` and `validate` catch it afterwards by date.
- *PxWeb selections need every dimension named.* PxWebApi 2 gives an unnamed
  dimension the table's default selection, which is often a short slice of
  history, not all of it. The client refuses any answer where a non-time
  dimension has more than one value.
- *`parse_qsl` reads `+` as a space.* Statistics Sweden's code for "both sexes"
  is `1+2`, so the selection is split by hand. Non-ASCII value codes (`ALÖSP`,
  `OMRÅDE`) must be percent-encoded, which `requests` does from the split
  pairs; a URL built by hand gets a 400.
- *INE's `Fecha` is local midnight in Madrid*, a day early in UTC. Date
  observations from `Anyo` plus `FK_Periodo` instead: 1–12 are months, 19–22
  quarters, 28 the year.

Smaller ones:
- Statistics Finland's v1 API answers the short table path (`khi/11xs.px`)
  and returns 400 on the long `statfin_khi_pxt_11xs.px` form its own site
  links to.
- Norway's headline unemployment is a three-month moving average dated at its
  middle month, so it ends a month before the single-month series.
- The Bank of Canada's Valet carries the policy-rate target only from 2009
  and USD/CAD only from 2017. The BIS keeps the longer policy-rate history.
- Destatis' GENESIS refuses the old `GAST` guest login for data, so a key is
  required. The token goes in a `username` header, never in a URL. Its
  `helloworld/logincheck` answer **echoes the token back** in plain text, so
  never print or log that response.
- GENESIS can stop serving one statistic while the rest of the service
  answers. On 2026-10-12 both CPI tables (61111-0001 annual, 61111-0002
  monthly) returned Status Code 7000, in German whatever `language` says:
  "Für die Teiltabelle mit der Metadatenkombination … wurde kein Datenquader
  gefunden! (Keine Datenquader oder Zeitscheibe nach PTable)", i.e. no data
  cube for that combination. It failed for every `startyear` back to 1900,
  the tables were still listed in `catalogue/tables` under the same codes,
  and industrial production (42153-0001) answered normally. The request had
  not changed and had worked days earlier, so the fault was Destatis's
  (a CPI republication or rebase in progress is the likely reason). The
  stored history stands; recheck before changing anything. The refresh
  report used to cut this message to 82 characters, which kept only its
  first words; the per-series detail now keeps 200 on one line.
- GENESIS answers a table too big to cut directly with HTTP 200 and Status Code
  98 ("use job=true"). Narrowing the request with `classifyingvariable1` and
  `classifyingkey1` avoids the job machinery entirely. Its flat CSV puts the
  index and its percent changes side by side, so pin `value_unit`.
- e-Stat rebases the CPI into a **new table** every five years: 2020 base is
  `0003427113`, 2025 base is `0004052037`. Each new table carries the linked
  history back to 1970. Time codes are ten digits: `YYYY00MMMM` is a month,
  `YYYY000000` a year, `YYYY100000` a fiscal year. The seasonally adjusted
  Labour Force Survey series are not in the API database, so Bedrok carries
  the unadjusted rate and says so.

**The menu review (session 7b, 2026-10-10).** A frequency default can hide a
wrong label: the Bank Rate sat in the catalog as `event`, which gave a daily
series 450 days before it counted as stale. Every row now states its allowance
and a test requires it. Index bases were recovered from the stored values
(the base year averages exactly 100) where a publisher's metadata did not say;
ABS states its own (`BASE_PERIOD`, "Sep 2025 = 100") in SDMX-CSV with
`labels=both`.

**The register disagrees with itself on the BIS.** Its older entries read the
general BIS terms ("non-commercial purposes" only); a newer one reads the
statistics terms at data.bis.org/help/legal (use is unrestricted with citation
and no added charge). Bedrok follows the statistics terms, so the coverage map's
"BIS bars commercial use" line was not carried into `docs/GAPS.md`. When a
register entry and a licence page disagree, the licence page wins and the date
read goes in the row.

**Lining up frequencies (session 8, 2026-10-11).** `get_series(frequency=…)`
puts series on one calendar (`transforms.to_frequency` then `align`). The
conventions, each chosen so nothing is invented:
- weeks end on Friday and are dated by it; months, quarters and years are
  dated at their start, like the store;
- a faster series takes the last value of a week and the average of longer
  periods (decided 2026-10-11), from the observations inside the period, so a
  holiday Friday uses the day before without a special case;
- a same-speed series is only relabelled: weekly series in the store are
  dated Tuesday (CFTC) or Saturday (claims), and without relabelling they miss
  every Friday slot;
- a slower series is stepped (each slot repeats its own period's value) and
  stops at its last period;
- a change-dated (`event`) series is carried forward, because a policy rate
  holds until it changes;
- empty periods stay null, `incomplete` names a period missing a part (a
  quarter without one of its months), `partial_last` a period not yet over;
- resampling comes before a transform: quarterly average, then yoy.

**The Bundesbank's 10-year is a curve, not a bond (session F, 2026-10-16).**
`BBSIS/D.I.ZAR.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A` is the yield on Federal
securities with annual coupons and ten years residual maturity, read off the
Bundesbank's fitted term structure ("aus der Zinsstruktur abgeleitet"). Its
monthly averages run about 0.08 point above Eurostat's `ltgov_yield_de` (a
benchmark-bond yield), 0.35 at most (2009-03), correlation 0.9996 over 350
months: the same rate measured two ways, so the two are never spliced and
the monthly spreads keep Eurostat's German leg. The service answers SDMX-CSV
with `;` and a BOM, carries weekends and holidays as `.` with OBS_STATUS K,
and adds per-row columns after TIME_PERIOD (`BBK_DIFF_A`), none of which the
client counts as a dimension. **It refuses a month-form `startPeriod`**
(`2026-10`, and `2026`) with 400 and takes only `2026-10-01`. The client sent
YYYY-MM to every publisher, and on an incremental fetch a 400 means "nothing
since then", so the series would have frozen after its first full pull with
no error, no failure count and a green refresh; only `validate`'s ten-day
staleness would have shown it. The row's `start_period = day` sends the
date. Probe the incremental request, not only the full one. Reuse is
the ESCB statistics policy (free, commercial included, cite the source, do
not modify), on the "Terms of use" page under User information, not the
imprint. The TLS handshake that failed on 2026-10-11 was the service's; on
2026-10-16 it answered in a quarter of a second.

**OECD and CFTC behind Cloudflare (2026-10-11).** Both answered 403 with a
Cloudflare challenge page ("Just a moment…", "Attention Required") to every
request from the Mac, home pages included, while other publishers answered.
Do not try to get past a bot check; wait, retry from a different day, and if
it holds, ask the publisher or find another route (the OECD's data also flows
through other SDMX hubs).

**What a chart recipe can and cannot standardise (session 9, 2026-10-11).**
Bedrok does not draw: the host's assistant does, with the host's chart tool,
and in Claude on the web nothing on the Mac (skills, Desktop settings) is
present. So the standard lives in what the server returns and says:
`chart_series` returns the recipe (title, subtitle with units, axes, colours,
labels, source line, footnotes, layout) and the server instructions carry the
short-answer rule. What it cannot fix: the drawing tool itself (whether it can
draw two scales, gaps or a download button), and an assistant that ignores the
recipe. Two traps found while building it: the subtitle must name only an
aggregation Bedrok actually did (a monthly series charted monthly is not
"monthly averages"), and footnotes carry a warning's whole effect text,
because cutting at the first ". " breaks on "incl.".

**Session 10's sources (2026-10-11).**
- The New York Fed's EFFR feed carries `targetRateFrom` and `targetRateTo` on
  every day from 2000. Before 2008-12-16 the Fed set one target, and only
  `targetRateFrom` is filled: Bedrok reports it as the lower bound and leaves
  the upper empty rather than inventing a range.
- Japan's Ministry of Finance publishes the JGB curve as two Shift-JIS CSVs,
  history to last month plus the current month; the note lines at the end
  carry their own commas, so the parser keeps only the header and dated rows.
- INE's core rate is `IPC292510`, in the special-groups table 76130, and
  excludes only unprocessed food and energy; Eurostat's core
  (`TOT_X_NRG_FOOD`) also excludes processed food, alcohol and tobacco. They
  are different numbers on purpose.
- Bedrok's query CLI truncates long text in its display: check a note with a
  Python read when it looks empty.

**Tidying the catalog (session 11, 2026-10-11).**
- Eurostat datasets disagree on the euro area's code and move between them:
  `irt_lt_mcby_m` uses EA, the STS and survey sets EA20, national accounts and
  HICP both, and `une_rt_m` moved to EA21 when Bulgaria joined in 2026. Each
  family now says which it fills (`ea_codes`); registering a code a dataset
  does not fill leaves a row that search finds and describe calls empty.
- An alias must be skipped everywhere a family id is produced (static rows,
  `sync_eurostat_multi`, `sync_hicp_europe`) and deleted at catalog load, or
  the next refresh brings the duplicate back.
- Search synonyms are deliberately few: only where the everyday word never
  appears in names. With "inflation → CPI" added, "argentina inflation" found
  US CPI, so a place only the warnings know now returns no series at all.

**Correct answers (session A, 2026-10-12).** What a cold-session self-test
found, and why each fix has the shape it has:
- *A fixed euro-area code goes stale silently.* The surveys sat on EA20,
  which Eurostat stopped at 2025-12; every refresh "succeeded" with nothing
  new, for nine months, and only a question about PMIs showed it. EA21 carries
  the whole history there, so the EA20 ids are aliases. Before aliasing,
  check the new code's depth: catalog load deletes an alias's stored rows.
  Industrial production, retail and government debt still publish EA20.
- *A warning's country is a name, not a bag of words.* Tokenising
  "Caucasus and Central Asia (mirror trade)" made "trade" a place, and the
  rule that answers an uncovered place with its warning alone then emptied
  "trade balance". Match countries as phrases; a bracket describes the case.
- *Every stored geography needs a place, or search cannot reach it.* The
  broad HICP set stores 40-odd countries; those outside `CODES` now sit in
  `geography.FAMILY_CODES`. Never add a code that is a word ("is", "me") to
  `NAMES`, and leave Georgia out: it is a US state first.
- *Only a ToolError's text reaches the assistant.* Any other exception reads
  "Error executing tool X", which it cannot act on; parse what a person
  types (`core/dates.py`) and refuse in words.
- *Eurostat's grant is EU, EFTA and candidate countries,* not "EU only" as
  the old attribution said. The broad set loops over the API response, so it
  filters by `pipeline.REUSE_GEOS`; Kosovo is outside and retired.

**Discovery and catalog (session D2, 2026-10-14).**
- *The FX pairs are mirrored, not branched.* `sync_fx` writes each pair to
  `fx_rates` (what the engines and `bedrok pctile EURUSD` read) and copies
  it to `macro_series` under the lower-case id, with a static catalog row.
  The macro stage must never fetch those rows: `_NOT_FX` guards
  `sync_macro` and `per_series_catalog`. The capitals are aliases, and
  `all_series` shows a pair twice, once per kind. An inverse is credited to
  the publisher of the rate it inverts, so `_terms` needs no FX case.
- *A pair id has no underscore,* so the notes regex `_related` uses never
  saw one; the six ids are matched by name.
- *Stage reports name rows three ways.* A series id (`sync_macro`, the
  per-member rows of every bulk stage, every derived series), a family
  label when the one download fails (`EUROSTAT_BULK_SETS` labels,
  `hicp_europe_bulk`, `worldbank_pink_sheet`, `cftc_cot`), or the pair in
  capitals (`sync_fx`). Filtering a report on "not a catalog id" would let
  another geography's dead-geo skip rows through; `_FAMILY_ROWS` lists the
  labels instead.
- *An empty frame has no `score` column.* A search whose place or filter
  left nothing raised KeyError, read as "Error executing tool".
- *A covered place needs a concept word for its warnings;* an uncovered one
  does not, because there the warning is the whole answer.
- *Two Bedrok servers can be registered at once* (the cold-session test saw
  `mcp__bedrok__*` and `mcp__Bedrok__*` with different schemas): a session
  may pick the older one. Which is which is an owner-side question.

**Chart mechanics (session D1, 2026-10-12).**
- *A scale is a dimension, not a string.* `units.dimension` maps every
  percentage-like unit to "rate" and each index base to its own; `axes`
  groups by it. The vocabulary's "percent change on a year earlier" (what
  `yoy` returns) is outside `units.GRAMMAR`, which only pins catalog rows.
- *A daily calendar must be the days the series have.* `pd.date_range(freq="D")`
  puts a slot on every weekend, and "never join across a null" would then
  break every line on Saturday; `align` takes the union of the non-stepped
  series' dates instead.
- *An event series needs three treatments.* On a daily calendar it is
  reindexed to every day to its last change; on a resampled one the bins are
  extended to the period holding today and forward-filled; in `align` the
  value is the last on or before each slot, so it reaches the chart's end.
  It is never a partial period.
- *The server's `transforms` module is shadowed by the tool parameter of the
  same name,* so it is imported as `transforms_mod`.
- *Pydantic models for nested tool input* (`Outside`, `Obs`) are the first
  in the server; the MCP SDK renders them as JSON schema without help.

**GDP by expenditure (session C, 2026-10-12).**
- *An annual Eurostat family needs room the monthly ones don't.* A year is
  dated to its first day, so 2025's value is ~650 days old in late 2026, and
  the 400-day dead-geo rule would have dropped every geography on the first
  sync; annual families get 800 days, and an 800-day staleness allowance.
  Eurostat accepts a month-form `sinceTimePeriod` ("2023-01") on annual
  datasets, so the incremental start needs no annual case.
- *Pick EA21, not EA20, for new families.* Eurostat labels EA20 "2023-2025";
  annually EA20 and EA21 are identical, quarterly EA20 drifts by up to 0.03
  points from EA21 (which equals the changing-composition EA).
- *Contributions add up annually, not always quarterly.* Imports are
  published negative, so the six parts sum to growth: within 0.06 points
  annually everywhere. The seasonally adjusted quarterly parts are adjusted
  one by one: Greece misses by up to 1.3 points (2011), Portugal and Finland
  by ~0.3. `validate` reports it; the family note says it.
- *DE and NL have no seasonally adjusted yoy quarterly contributions* in
  `namq_10_gdp` (only q/q, or yoy unadjusted).
- *BEA's table 1.1.2 tags contributions "Level"* with UNIT_MULT 0: no scale
  in the code. Its basis is quarter on quarter at an annual rate, not
  Eurostat's year on year, so the US parts stay their own ids.
- *The ONS publishes no contribution series* (only the spreadsheet "GDP –
  contributions summary of records"); `api.ons.gov.uk` is gone (2024-11),
  the site path with `/data` is the route; household consumption ABJR lives
  under `economy/nationalaccounts/satelliteaccounts`, not the GDP path.
- *A running MCP server keeps the code it started with.* Changes to the
  server need a reconnect (`/mcp`) or a new session before a cold-session
  test means anything; in-process checks (`Client(mcp_server.server)`) use
  the working tree.

**Opening the repository (session 12, 2026-10-12).**

- *A filename is content.* A tracked LibreOffice lock file (`.~lock.<name>#`)
  had harmless bytes and a private filename, and survived months of review
  because nobody reads filenames. `tests/test_publication.py` checks names as
  well as contents, and `.gitignore` carries `.~lock.*#`.
- *A shipped seed is published to every install.* `bedrok/seeds/*.md` go in
  the wheel, so the gate scans the built wheel too (`package.yml` runs it after
  `uv build`).
- *A URL is not a home path.* `/home/` matches `ebrd.com/home/news/`; the gate's
  pattern needs a lookbehind that refuses a hostname or path character before
  the slash.
- *PEP 639 forbids licence classifiers.* With `license = "MIT"` and
  setuptools ≥77, a `License ::` classifier is an error, not a duplicate.
- *A fresh public history is an orphan commit in place, not a new clone.* The
  Claude Code registration and the Desktop bundle's folder both name this
  path, and `git checkout --orphan` touches only the index and refs, so the
  untracked `profile/` and `.venv/` stay put. Expire the reflog and `gc
  --prune=now` before adding the new remote, and check
  `git rev-list --all | wc -l` is 1: an unreachable old object cannot be
  pushed if it no longer exists. GitHub treats `Bedrok` and `bedrok` as one
  name, so the old repository is renamed before the new one is created.
- *A bot check is not an outage, and not stable either.* Cloudflare's "Just a
  moment…" 403 comes and goes: on 2026-10-12 the OECD refused a home
  connection and, an hour later, answered GitHub's runner. One red run is a
  prompt to look, not a verdict.
- *`--keyless` means "requires no key", not "has no key column".* The BLS has
  a `key_env` but works without it (25 calls a day per address); the first cut
  of the flag dropped it from the weekly run.
- *An "empty home" check must not run from the repo folder.* The profile
  resolves `./profile` before the per-user folder, so `scripts/mcp_smoke.py`
  started from a checkout answers from the developer's own store and passes for
  the wrong reason. Run it with the empty home as the working directory too.
  CI is safe only because a fresh checkout has no `profile/`.
- *A bundle's identity is its author and its name, so never change either.*
  Claude Desktop files an extension as `local.mcpb.<author>.<name>`. 0.2.0
  changed the manifest's author, so Desktop installed it as a second extension
  beside 0.1.8 instead of upgrading it: the new one started with no settings
  (no folder, so an empty store in the per-user folder and no keys) while the
  old one kept running with the real store, and the assistant saw both sets of
  tools. The author is now fixed for good; the fix for an affected install is
  to set the folder on the new extension and uninstall the old one.

