#!/usr/bin/env bash
set -euo pipefail

# Supported Python: 3.12-3.13, as each pyproject.toml's requires-python says.
python3 -c 'import sys; sys.exit(not (3, 12) <= sys.version_info[:2] <= (3, 13))' || {
    echo "error: Python 3.12-3.13 is required; python3 is $(python3 -V 2>&1)" >&2
    exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORDNET_MCP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Installing blue-data-wordnet-mcp..."
pip install -e "$WORDNET_MCP_ROOT"

echo "Done. Run: blue-data-wordnet-mcp"
