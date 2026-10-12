#!/bin/sh
# Fetch the pinned DuckDB CLI binaries Bedrok's zero-install query path uses.
# They are NOT committed to git (the macOS build alone exceeds GitHub's 100MB
# file limit). Version is pinned to match the duckdb Python package that
# writes the store — keep the two in lockstep.
set -e
VERSION="v1.5.4"
BASE="https://github.com/duckdb/duckdb/releases/download/${VERSION}"
DIR="$(cd "$(dirname "$0")" && pwd)"

fetch() {
    asset="$1"; out="$2"
    [ -x "${DIR}/${out}" ] && { echo "${out}: already present"; return; }
    echo "fetching ${asset}..."
    curl -sL -o "${DIR}/${asset}.zip" "${BASE}/${asset}.zip"
    (cd "${DIR}" && unzip -o -q "${asset}.zip" && mv duckdb "${out}" && rm "${asset}.zip" && chmod +x "${out}")
    echo "${out}: done"
}

fetch duckdb_cli-linux-amd64 duckdb-linux-amd64
fetch duckdb_cli-linux-arm64 duckdb-linux-arm64
case "$(uname -s)" in
    Darwin) fetch duckdb_cli-osx-universal duckdb-osx ;;
esac
echo "all binaries ready (${VERSION})"
