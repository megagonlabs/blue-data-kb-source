#!/usr/bin/env bash
# Install everything in this repo, editable, in dependency order:
#   blue-data (sibling repo) → lib (with every optional extra) → CLI, MCP server, WordNet MCP.
# Expects the sibling repo checked out beside this one: ../blue-data
# Usage: bash scripts/install.sh        (run from anywhere; uses the active python3)
set -euo pipefail

# Supported Python: 3.12-3.13, as each pyproject.toml's requires-python says.
python3 -c 'import sys; sys.exit(not (3, 12) <= sys.version_info[:2] <= (3, 13))' || {
    echo "error: Python 3.12-3.13 is required; python3 is $(python3 -V 2>&1)" >&2
    exit 1
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BLUE_DATA_LIB="$ROOT/../blue-data/lib"

if [ ! -d "$BLUE_DATA_LIB" ]; then
    echo "error: sibling repo not found at $BLUE_DATA_LIB — clone blue-data beside this repo first" >&2
    exit 1
fi

pip() { python3 -m pip install -q "$@"; }

echo "Installing blue-data..."
pip -e "$BLUE_DATA_LIB"

echo "Installing blue-data-kb-source (lib, with neo4j/openai/search/mcp extras and dev tools)..."
pip -e "$ROOT/lib[all,dev]"

echo "Installing blue-data-kb-cli, blue-data-kb-mcp, blue-data-wordnet-mcp..."
pip -e "$ROOT/platform/cli" -e "$ROOT/platform/mcp" -e "$ROOT/platform/wordnet-mcp"

echo "Done. Commands: blue-kb, blue-data-kb-mcp, blue-data-wordnet-mcp"
