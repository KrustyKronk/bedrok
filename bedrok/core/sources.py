"""The source list: one row per publisher, in bedrok/seeds/sources.csv.

Everything Bedrok knows about a publisher — how to reach it, whether it needs a
key and which, its default licence and whether its numbers may be stored, where
its terms live, the line to credit it with, how politely to pace it, and which
catalog series `bedrok smoke` checks it by — lives in that one row. Adding a
publisher that speaks SDMX is a row here plus catalog rows; a publisher with
its own format is a row plus an adapter module named in `adapter`.

  protocol  sdmx     the generic client in ingest/adapters/sdmx.py, configured
                     by base_url, format, user_agent and start_period
            pxweb    the generic client in ingest/adapters/pxweb.py for the
                     Nordic offices' table APIs, configured the same way
            adapter  a bespoke module, ingest/adapters/<adapter>.py
            bulk     a bulk stage of its own (World Bank, CFTC, derived)

Keys are read from the environment at call time, never cached here: the
profile's .env is loaded into the environment by config.set_profile, and a test
can set or clear one with monkeypatch.setenv.

Pure data, in core: nothing here imports a layer above.
"""
from __future__ import annotations

import csv
import functools
import importlib.resources
import os
from pathlib import Path

from bedrok.core import config

SOURCES_CSV = Path(str(importlib.resources.files("bedrok") / "seeds" / "sources.csv"))


@functools.lru_cache(maxsize=1)
def load() -> dict[str, dict]:
    """{source: row} from the repo's source list."""
    with open(SOURCES_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["pacing_s"] = float(r["pacing_s"] or 0)
    return {r["source"]: r for r in rows}


def get(source: str) -> dict:
    try:
        return load()[source]
    except KeyError:
        raise KeyError(f"{source!r} is not in {SOURCES_CSV.name}") from None


def key(source: str) -> str:
    """The source's API key from the environment, or "" when it has none.
    A blank or unfilled-placeholder value (a Claude Desktop bundle's empty
    optional field may arrive as `${user_config.x}`) counts as none."""
    env = get(source)["key_env"]
    value = os.getenv(env, "") if env else ""
    return "" if config.unset(value) else value.strip()


def needs_key(source: str) -> bool:
    return get(source)["key_required"] == "yes"


def attribution(source: str) -> str:
    return get(source)["attribution"]


def default_license(source: str) -> str:
    return get(source)["license"]


def redact(text: str) -> str:
    """Remove every configured key value from text before it is printed.

    A requests HTTPError names the URL it failed on, and several publishers take
    their key as a URL parameter (FRED, EIA, BEA, Census, DOL) — so an error
    message is a way for a key to reach a terminal, a log or a PR."""
    for row in load().values():
        value = os.getenv(row["key_env"], "") if row["key_env"] else ""
        if len(value) >= 8:
            text = text.replace(value, "<redacted>")
    return text
