#!/usr/bin/env bash
set -euo pipefail

# Supported Python: 3.10-3.14, as each pyproject.toml's requires-python says (3.15 not yet).
python3 -c 'import sys; sys.exit(not (3, 10) <= sys.version_info[:2] <= (3, 14))' || {
    echo "error: Python 3.10-3.14 is required; python3 is $(python3 -V 2>&1)" >&2
    exit 1
}
echo 'Installing blue-data-kb-source lib...'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Install the local blue-data dependency first
echo 'Installing blue-data from local path...'
python3 -m pip install -e "$SCRIPT_DIR/../../../blue-data/lib"

# Install this package in editable mode with every optional extra (openai, search,
# neo4j, mcp) and the dev tools, the same as the top-level scripts/install.sh, so the
# quickstart, the examples and the whole test suite work after it (#150).
echo 'Installing blue-data-kb-source...'
python3 -m pip install -e "$SCRIPT_DIR/..[all,dev]"
