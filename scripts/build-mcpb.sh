#!/usr/bin/env bash
# Build the Claude Desktop bundle: dist/bedrok-<version>.mcpb
#
# The bundle is server.type "uv": it carries the package source, pyproject.toml
# and a uv.lock, and Claude Desktop installs Python and the dependencies itself
# on install (`uv sync`). Nothing is vendored, so it is a few hundred kB.
# Needs: uv, and npx (Node) for the official @anthropic-ai/mcpb packer.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

cp -R bedrok pyproject.toml README.md LICENSE "$STAGE/"
cp packaging/mcpb/manifest.json "$STAGE/"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
# Pin the interpreter. Inside a project `uv run` picks the NEWEST Python it can
# get, and on 2026-10-10 that was 3.15, for which DuckDB publishes no wheel —
# so uv compiled DuckDB from source for ten minutes and more. 3.12 is what the
# package is developed and tested on; DuckDB ships wheels for 3.10–3.14.
echo "3.12" > "$STAGE/.python-version"
# Pin exactly what was tested: Desktop's `uv sync` honours the lock.
(cd "$STAGE" && uv lock --quiet)

npx --yes @anthropic-ai/mcpb validate "$STAGE/manifest.json"
mkdir -p dist
npx --yes @anthropic-ai/mcpb pack "$STAGE" "dist/bedrok-$VERSION.mcpb"
echo "built dist/bedrok-$VERSION.mcpb"
