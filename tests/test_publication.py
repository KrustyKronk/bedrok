"""The repository is public, so what may be published is a test, not a habit.

Nothing tracked names a position size, a NAV, an amount, a transaction, an API
key or a private path. Before this was a test, a LibreOffice lock file sat in
the tree for months: its content was harmless, but its *filename* named a
broker and a tax position, and no reviewer reads filenames.

Two layers. The general patterns below hold for anyone: home-directory paths,
literal key values, provider token shapes, lock files and anything that should
only ever live in `profile/`. The owner's own terms — names, private folders,
account and bucket names — cannot be written here without publishing them, so
they live one per line in `profile/publication-terms.txt` (gitignored); the
test that reads them skips when the file is absent, as it is on CI and on a
contributor's machine.

One use of an owner term is sanctioned: the author's name exactly as
`pyproject.toml` declares it in `authors`, wherever it appears (the README's
credit line, the bundle manifest, the wheel's METADATA). Any other use — a first
name in a comment, a surname in a docstring — still fails.

The wheel is checked too when one has been built (`uv build`), because the
seeds ship in it: `bedrok/seeds/charting.md` once carried a first name onto
every install.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import tomllib
import zipfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TERMS_FILE = REPO / "profile" / "publication-terms.txt"
BINARY = {".png", ".jpg", ".gif", ".ico", ".whl", ".mcpb", ".duckdb", ".zip"}

PATTERNS = {
    # The lookbehind keeps URLs out: `ebrd.com/home/news/` is not a home folder.
    "home path": re.compile(
        r"(?<![\w.-])(/Users/[A-Za-z0-9._-]{2,}/|/home/[A-Za-z0-9._-]{2,}/)|C:\\Users\\"),
    "key value": re.compile(
        r"(?i)(api[_-]?key|token|secret)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{20,}"),
    "AWS key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b"),
    "PyPI token": re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}"),
}

# Files that must never be tracked, whatever they contain.
FORBIDDEN_FILES = re.compile(
    r"(^|/)(\.~lock\..*#|\.DS_Store|\.env|[^/]*\.env|[^/]*\.duckdb(\.wal)?)$|^profile/")

# (file, pattern name) pairs that are legitimate. Keep this empty if you can.
ALLOWED: set[tuple[str, str]] = set()


def _tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                             check=True).stdout.decode()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [f for f in out.split("\0") if f]


def _text_files() -> list[tuple[str, str]]:
    out = []
    for name in _tracked():
        path = REPO / name
        if pathlib.Path(name).suffix.lower() in BINARY or not path.is_file():
            continue
        out.append((name, path.read_text(encoding="utf-8", errors="replace")))
    return out


def _owner_terms() -> list[str]:
    if not TERMS_FILE.exists():
        pytest.skip("no profile/publication-terms.txt on this machine")
    terms = [ln.strip() for ln in TERMS_FILE.read_text().splitlines()]
    return [t for t in terms if t and not t.startswith("#")]


def _authors() -> list[str]:
    meta = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
    return [a["name"].lower() for a in meta.get("authors", []) if a.get("name")]


def _hits(name: str, text: str, terms: list[str] | None = None) -> list[str]:
    found = []
    authors = _authors() if terms is not None else []
    for lineno, line in enumerate(text.splitlines(), 1):
        if terms is None:
            for label, rx in PATTERNS.items():
                if rx.search(line) and (name, label) not in ALLOWED:
                    found.append(f"{name}:{lineno}: {label}")
        else:
            low = line.lower()
            for author in authors:
                low = low.replace(author, "")
            # The term itself is not printed: the failure message lands in CI logs.
            found += [f"{name}:{lineno}: owner term #{i + 1}"
                      for i, t in enumerate(terms) if t.lower() in low]
    return found


def test_no_forbidden_file_is_tracked():
    bad = [f for f in _tracked() if FORBIDDEN_FILES.search(f)]
    assert not bad, f"these must never be tracked: {bad}"


def test_no_tracked_file_carries_a_general_pattern():
    hits = [h for name, text in _text_files() for h in _hits(name, text)]
    assert not hits, "\n".join(hits)


def test_no_tracked_file_or_filename_carries_an_owner_term():
    terms = _owner_terms()
    hits = [f"{n}: filename, owner term" for n in _tracked()
            if any(t.lower() in n.lower() for t in terms)]
    hits += [h for name, text in _text_files() for h in _hits(name, text, terms)]
    assert not hits, "\n".join(hits)


def _wheel_members():
    wheels = sorted((REPO / "dist").glob("*.whl"))
    if not wheels:
        pytest.skip("no wheel built (run `uv build`)")
    with zipfile.ZipFile(wheels[-1]) as zf:
        return [(m, zf.read(m).decode("utf-8", errors="replace")) for m in zf.namelist()
                if m.endswith((".md", ".csv", ".py", ".json", ".txt", "METADATA"))]


def test_the_wheel_carries_no_general_pattern():
    hits = [h for name, text in _wheel_members() for h in _hits(name, text)]
    assert not hits, "\n".join(hits)


def test_the_wheel_carries_no_owner_term():
    terms = _owner_terms()
    hits = [h for name, text in _wheel_members() for h in _hits(name, text, terms)]
    assert not hits, "\n".join(hits)
