"""Drive `bedrok serve` over stdio the way an MCP host does, and check it answers.

  python scripts/mcp_smoke.py [--any-home] -- <command that starts the server...>

e.g.  python scripts/mcp_smoke.py -- uvx --from dist/bedrok-0.1.8-py3-none-any.whl bedrok serve

Sends initialize, tools/list, list_sources and nine keyless questions (euro-area inflation,
the US 10-year, the 10-year breakeven, the German 10-year, Sweden's CPIF, and
a daily and a monthly series lined up weekly, the same as a chart recipe, a summary of the catalog, and the week's releases), prints what came
back and how long each step took, and exits 1 if any step failed. Standard
library only, so it runs under any Python — a clean machine, or CI.

It also fails when the server answers from a home outside $HOME (or outside
$BEDROK_PROFILE when that is set): started from the repo folder, the server
finds ./profile and answers from the developer's own store, and the "empty
home" run passes for the wrong reason (session 12). Run it from an empty
folder; --any-home turns the check off.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

TOOLS = ["list_sources", "search_series", "list_series", "describe_series", "get_series",
         "chart_series", "percentile", "release_calendar", "query", "refresh"]

CALLS = [
    # first: `home` says which store answers everything after it
    ("list_sources", {}),
    ("percentile", {"series_id": "ea_hicp_yoy"}),
    ("get_series", {"series_ids": ["us_10y"], "start": "2026-01-01", "max_rows": 3}),
    # a computed series (its inputs fetched on demand) and a Eurostat country
    # series: both must be known to a store that has never been refreshed
    ("percentile", {"series_id": "us_breakeven_10y"}),
    ("get_series", {"series_ids": ["ltgov_yield_de"], "start": "2026-01-01", "max_rows": 2}),
    # a PxWeb publisher, so the Nordic client is exercised from a clean machine
    ("get_series", {"series_ids": ["se_cpif"], "start": "2026-01-01", "max_rows": 2}),
    # two frequencies on one calendar (session 8), the way a chart asks for them
    ("get_series", {"series_ids": ["us_10y", "us_fed_funds"], "start": "2026-06-01",
                    "frequency": "weekly", "max_rows": 4}),
    # the chart recipe (session 9): two units, so two scales
    ("chart_series", {"series_ids": ["us_10y", "ea_hicp_index"], "start": "2026-01-01"}),
    # the catalog in one call (session 11)
    ("list_series", {"summary": True}),
    # what is out this week (13a): reads BEA's schedule from a clean machine
    ("release_calendar", {"days": 7}),
]


def home_check(home: str | None, env, any_home: bool = False) -> str | None:
    """None when the server's home is under the folder it was given, else why.
    Both sides resolved: macOS `mktemp -d` says /var/..., the server /private/var/..."""
    if any_home:
        return None
    root = env.get("BEDROK_PROFILE") or env.get("HOME")
    if not root:
        return "HOME is not set, so the home the server answers from cannot be checked"
    if not home:
        return "list_sources did not say which home it answers from"
    if Path(os.path.realpath(home)).is_relative_to(Path(os.path.realpath(root))):
        return None
    return (f"the server answers from {home}, which is not under {root}; "
            "run from an empty folder, or pass --any-home")


def main(argv: list[str]) -> int:
    own = argv[:argv.index("--")] if "--" in argv else []
    any_home = "--any-home" in own
    cmd = argv[argv.index("--") + 1:] if "--" in argv else argv
    if not cmd:
        print(__doc__)
        return 2
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=sys.stderr, text=True, bufsize=1)
    next_id = iter(range(1, 100))

    def request(method, params=None):
        msg = {"jsonrpc": "2.0", "id": next(next_id), "method": method}
        if params is not None:
            msg["params"] = params
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        while True:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"server closed the stream during {method}")
            reply = json.loads(line)
            if reply.get("id") == msg["id"]:
                return reply

    failures = 0
    try:
        init = request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "bedrok-smoke", "version": "0"}})
        info = init["result"]["serverInfo"]
        print(f"[{time.time() - t0:5.1f}s] initialize: {info['name']} {info.get('version')}")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        tools = [t["name"] for t in request("tools/list")["result"]["tools"]]
        print(f"[{time.time() - t0:5.1f}s] tools: {', '.join(tools)}")
        # Every tool a host is told about must be there (by name, so a new
        # tool is a line here rather than a silently stale count).
        missing = set(TOOLS) - set(tools)
        if missing:
            print(f"FAILED: tools missing: {', '.join(sorted(missing))}")
            failures += 1
        for name, args in CALLS:
            t = time.time()
            res = request("tools/call", {"name": name, "arguments": args})["result"]
            body = res.get("structuredContent") or {}
            # get_series reports a per-series failure inside a successful call
            ok = not res.get("isError") and not any(
                "error" in x for x in body.get("series", []))
            failures += not ok
            if ok and name == "list_sources":
                wrong = home_check(body.get("home"), os.environ, any_home)
                if wrong:
                    print(f"FAILED: {wrong}")
                    failures += 1
            summary = (json.dumps(body)[:300] if ok
                       else res["content"][0]["text"][:300])
            print(f"[{time.time() - t0:5.1f}s] {name} {'ok' if ok else 'FAILED'} "
                  f"({time.time() - t:.1f}s): {summary}")
    except Exception as e:  # noqa: BLE001
        failures += 1
        print(f"FAILED: {type(e).__name__}: {e}")
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    print(f"{'PASS' if not failures else 'FAIL'} in {time.time() - t0:.1f}s")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
