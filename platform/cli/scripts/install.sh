#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_ROOT="$(cd "$CLI_ROOT/../../lib" && pwd)"
BLUE_DATA_LIB="$(cd "$CLI_ROOT/../../../blue-data/lib" && pwd)"

echo "Installing blue-data..."
pip install -e "$BLUE_DATA_LIB"

echo "Installing blue-data-kb-source..."
pip install -e "$LIB_ROOT"

echo "Installing blue-data-kb-cli..."
pip install -e "$CLI_ROOT"

echo "Done. Run: blue-kb --help"
