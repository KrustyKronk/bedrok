"""Bedrok configuration: profile resolution, profile.toml parsing, DB connection.

A *profile* (the Bedrok home folder) holds everything user-specific:

    <home>/
    ├── profile.toml          # the config parsed here (all keys optional)
    ├── data/market.duckdb    # the store — one file; $BEDROK_DB overrides the path
    └── .env                  # API keys, all optional (chmod 600); which source
                              # reads which: bedrok/seeds/sources.csv

The catalog and the source list are not in the profile: they ship inside the
package, in bedrok/seeds/ (SEEDS_DIR), so an installed wheel has them too.

Resolution order for the home folder: explicit `set_profile()` (CLI --profile)
→ $BEDROK_PROFILE (what the Claude Desktop bundle's folder field sets) →
legacy $FDP_ROOT → ./profile under the current directory → the per-user data
folder (~/Library/Application Support/bedrok on macOS, ~/.local/share/bedrok
on Linux). There is always a home, so a fresh install needs no setup: the
first `bedrok serve` creates the folder and the store. The DB path may be
overridden independently via $BEDROK_DB (legacy $FDP_DB).

An environment value that is an unfilled placeholder — `${user_config.x}`,
which a Claude Desktop bundle may pass for a blank optional field — counts as
unset, here and for API keys (core/sources.py).
"""
from __future__ import annotations

import importlib.resources
import os
from pathlib import Path

try:
    import tomllib  # stdlib from 3.11
except ModuleNotFoundError:  # 3.10 sandboxes (Cowork's system python) need the backport
    import tomli as tomllib

import duckdb

try:
    from dotenv import load_dotenv
except ImportError:  # read-only runs don't need python-dotenv
    def load_dotenv(*_a, **_k):
        return False


# ---- defaults (a missing/empty profile.toml gets exactly these) -------------
# Sections a profile.toml carries that are not listed here pass through _merge
# untouched and are simply never read — a [market] block from the days of the
# pack, or the portfolio-era [broker]/[sleeves]/[strategy], load without error.
_DEFAULTS = {
    "owner": {"name": "Investor"},
    "base": {"currency": "EUR", "symbol": "€"},
    "macro": {"hicp_europe": True},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


# The seeds ship inside the package and are found through the package itself,
# never by counting ".parent" off a file: that once repointed every seed path
# when config.py moved into core/, and an installed wheel has no repo root at all.
SEEDS_DIR = Path(str(importlib.resources.files("bedrok") / "seeds"))


def unset(value: str | None) -> bool:
    """True for a missing, blank, or unfilled-placeholder (`${...}`) value."""
    v = (value or "").strip()
    return not v or (v.startswith("${") and v.endswith("}"))


def default_home() -> Path:
    """The per-user data folder a fresh install uses."""
    import platformdirs
    return Path(platformdirs.user_data_dir("bedrok", appauthor=False))


def _resolve_default_dir() -> Path:
    for env in ("BEDROK_PROFILE", "FDP_ROOT"):
        if not unset(os.getenv(env)):
            return Path(os.getenv(env).strip()).expanduser()
    cwd_profile = Path.cwd() / "profile"
    if cwd_profile.is_dir():
        return cwd_profile
    return default_home()


# ---- module state (recomputed by set_profile) -------------------------------
PROFILE_DIR: Path | None = None
PROFILE: dict = dict(_DEFAULTS)
ROOT: Path | None = None
DATA_DIR: Path | None = None
DB_PATH: Path | None = None
BASE_CCY = "EUR"
BASE_SYMBOL = "€"



def fx_pairs() -> list[tuple[str, str, str]]:
    """(pair, source, code) list for sync_fx, derived from the base currency.

    ECB reference rates are EUR-based; non-EUR bases get 'derived' cross pairs
    computed from them at sync time (best-effort — EUR is the fully-tested path).
    """
    # The engines look up f"{BASE_CCY}{ccy}", so the store carries the three
    # fetchable pairs plus their reciprocals; EUR, GBP and USD bases all resolve.
    fetchable = [
        ("EURUSD", "ecb", "EXR/D.USD.EUR.SP00.A"),
        ("EURGBP", "ecb", "EXR/D.GBP.EUR.SP00.A"),
        ("GBPUSD", "fedboard", "H10:RXI$US_N.B.UK"),  # FRED's DEXUSUK is this H.10 series
        # The tenge (session K1): the National Bank of Kazakhstan's own official
        # rates, both of them, so EURKZT needs no cross through the dollar.
        # No reciprocals: nothing asks for tenge-based rates.
        ("USDKZT", "nbk", "fx:5"),
        ("EURKZT", "nbk", "fx:6"),
    ]
    derived = [
        ("USDEUR", "derived", "1/EURUSD"),
        ("GBPEUR", "derived", "1/EURGBP"),
        ("USDGBP", "derived", "1/GBPUSD"),
    ]
    if BASE_CCY in ("EUR", "USD", "GBP"):
        return fetchable + derived
    raise SystemExit(
        f"base currency {BASE_CCY!r} not supported yet "
        "(EUR/USD/GBP ship as a full cross set; others need new pairs)")


def set_profile(path: str | Path | None = None) -> None:
    """Load (or reload) the profile and recompute all module paths/settings."""
    global PROFILE_DIR, PROFILE, ROOT, DATA_DIR, DB_PATH
    global BASE_CCY, BASE_SYMBOL

    PROFILE_DIR = Path(path) if path else _resolve_default_dir()
    toml_data = {}
    if PROFILE_DIR and (PROFILE_DIR / "profile.toml").exists():
        toml_data = tomllib.loads((PROFILE_DIR / "profile.toml").read_text())
    PROFILE = _merge(_DEFAULTS, toml_data)

    ROOT = PROFILE_DIR
    DATA_DIR = PROFILE_DIR / "data" if PROFILE_DIR else None
    db_env = next((os.getenv(e) for e in ("BEDROK_DB", "FDP_DB") if not unset(os.getenv(e))), None)
    DB_PATH = Path(db_env) if db_env else (DATA_DIR / "market.duckdb" if DATA_DIR else None)

    BASE_CCY = PROFILE["base"]["currency"].upper()
    BASE_SYMBOL = PROFILE["base"]["symbol"]

    # A Claude Desktop bundle sets every key it declares, blank or as the
    # unfilled `${user_config.x}`, and load_dotenv never overrides a variable
    # that exists — so a blank field would hide the same key in the home
    # folder's .env. Clear blank and placeholder keys first, then load .env.
    for name in [k for k, v in os.environ.items() if k.endswith("_API_KEY") and unset(v)]:
        del os.environ[name]
    if PROFILE_DIR:
        load_dotenv(PROFILE_DIR / ".env")
    # Keys stay in the environment: core/sources.py reads the one a source
    # names (`key_env`) at the moment it is needed.


def require_profile() -> Path:
    if PROFILE_DIR is None:
        raise SystemExit(
            "No Bedrok home folder resolved. Pass --profile PATH or set "
            "$BEDROK_PROFILE. See docs/INSTALL.md.")
    return PROFILE_DIR


def require_key(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"{name} missing. Add it to "
            f"{PROFILE_DIR / '.env' if PROFILE_DIR else 'your profile .env'}.")
    return val


def sandbox_copy_recipe() -> str:
    """The copy-store workaround, spelled out with this profile's real path."""
    db = Path(DB_PATH) if DB_PATH else Path("profile/data/market.duckdb")
    return (
        "  Writes are impossible against a read-only mount. To work anyway:\n"
        "    mkdir -p /tmp/bedrok/data\n"
        f"    cp '{db}'* /tmp/bedrok/data/\n"
        f"    export BEDROK_DB=/tmp/bedrok/data/{db.name}\n"
        "  then re-run. NOTE: everything written to /tmp DIES WITH THE SESSION —\n"
        "  replay the refresh Mac-side afterwards."
    )


def _open(path: Path, read_only: bool) -> duckdb.DuckDBPyConnection:
    """duckdb.connect, with the read-only-mount WAL failure made actionable.

    DuckDB removes the `.wal` when it opens a database. The Cowork mount permits
    write and truncate but NOT unlink, so any `.wal` left behind by an earlier
    command makes every subsequent open fail — the first write of a session
    works and the second does not, which reads as an intermittent fault rather
    than a permission model. `store.close_checkpointed` stops us leaving one;
    this turns somebody else's leftover into instructions instead of a traceback.
    """
    try:
        return duckdb.connect(str(path), read_only=read_only)
    except duckdb.IOException as e:
        msg = str(e)
        if "Could not remove file" not in msg or ".wal" not in msg:
            raise
        raise SystemExit(
            f"Cannot open {path}: a stale write-ahead log is in the way and this "
            f"filesystem does not allow deleting it.\n"
            f"  {msg.strip()}\n\n"
            f"  This mount allows writing and truncating but not unlinking, so\n"
            f"  emptying the file works where removing it does not:\n"
            f"    : > '{path}.wal'\n"
            f"  then re-run. If that is also refused, copy the store instead:\n"
            f"{sandbox_copy_recipe()}") from e


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the profile's one store at DB_PATH."""
    require_profile()
    if DB_PATH is None:
        raise SystemExit("no store path resolved (profile has no data/ dir?)")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if read_only and not DB_PATH.exists():
        raise SystemExit(f"no store at {DB_PATH} — run `bedrok backfill` to build it.")
    return _open(DB_PATH, read_only)


# Initialise from environment/cwd at import; the CLI re-calls set_profile(--profile).
set_profile()
