#!/usr/bin/env bash
set -euo pipefail

# Supported Python: 3.10-3.14, as each pyproject.toml's requires-python says (3.15 not yet).
python3 -c 'import sys; sys.exit(not (3, 10) <= sys.version_info[:2] <= (3, 14))' || {
    echo "error: Python 3.10-3.14 is required; python3 is $(python3 -V 2>&1)" >&2
    exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_ROOT="$(cd "$MCP_ROOT/../../lib" && pwd)"
BLUE_DATA_LIB="$(cd "$MCP_ROOT/../../../blue-data/lib" && pwd)"

echo "Installing blue-data..."
pip install -e "$BLUE_DATA_LIB"

echo "Installing blue-data-kb-source..."
pip install -e "$LIB_ROOT"

echo "Installing blue-data-kb-mcp..."
pip install -e "$MCP_ROOT"

echo "Done. Run: blue-data-kb-mcp"
