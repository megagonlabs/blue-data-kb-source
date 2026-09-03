#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORDNET_MCP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Installing blue-data-wordnet-mcp..."
pip install -e "$WORDNET_MCP_ROOT"

echo "Done. Run: blue-data-wordnet-mcp"
