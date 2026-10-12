"""Bedrok CLI — one entry point over the store and the engines.

  bedrok [--profile PATH] <command> [command args...]

Commands:
  smoke        connectivity check, one call per data source
  backfill     full deep historical pull (idempotent)  [--only ids --stage S]
  refresh      incremental sync of the local store
  validate     coverage + trust audit (staleness, licence)
  query        read-only SQL over the store           "SELECT ..." [--csv]
  tieout       compare re-pointed series with the store [--only ids]
  doctor       store + freshness report               (offline; run this first)
  schema       the store's table/column layout
  pctile       percentile of a series' latest value    SERIES_ID [--value V --since D]
  rates        EUR-investor rates/inflation toolkit   [--csv]
  serve        MCP server over stdio, for Claude and other assistants
  docs         regenerate docs/SOURCES.md and docs/LICENSES.md  [--check]

The profile (user config + data) resolves from --profile, $BEDROK_PROFILE, or
./profile. See docs/SETUP.md.
"""
from __future__ import annotations

import sys

_COMMANDS = {
    "smoke": "bedrok.ops.smoke",
    "backfill": "bedrok.ingest.backfill",
    "refresh": "bedrok.ingest.refresh",
    "validate": "bedrok.ops.validate",
    "query": "bedrok.core.query",
    "tieout": "bedrok.ops.tieout",
    "doctor": "bedrok.ops.doctor",
    "schema": "bedrok.core.schema",
    "pctile": "bedrok.engines.pctile",
    "rates": "bedrok.engines.rates_infl",
    "serve": "bedrok.surfaces.mcp_server",
    "docs": "bedrok.ops.docs",
}


def main() -> int:
    argv = sys.argv[1:]

    profile = None
    if "--profile" in argv:
        i = argv.index("--profile")
        try:
            profile = argv[i + 1]
        except IndexError:
            print("--profile requires a path", file=sys.stderr)
            return 2
        argv = argv[:i] + argv[i + 2:]

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in _COMMANDS:
        print(f"unknown command {cmd!r}\n", file=sys.stderr)
        print(__doc__)
        return 2

    from bedrok.core import config
    if profile:
        config.set_profile(profile)

    import importlib
    mod = importlib.import_module(_COMMANDS[cmd])
    sys.argv = [f"bedrok {cmd}"] + rest
    return mod.main()


if __name__ == "__main__":
    sys.exit(main())
