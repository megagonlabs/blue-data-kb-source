#!/usr/bin/env bash
set -euo pipefail
echo 'Installing blue-data-kb-source lib...'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Install the local blue-data dependency first
echo 'Installing blue-data from local path...'
python3 -m pip install -e "$SCRIPT_DIR/../../../blue-data/lib"

# Install this package in editable mode with dev extras
echo 'Installing blue-data-kb-source...'
python3 -m pip install -e "$SCRIPT_DIR/..[dev]"
