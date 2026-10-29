#!/usr/bin/env bash
set -euo pipefail

# Supported Python: 3.10-3.14, as each pyproject.toml's requires-python says (3.15 not yet).
python3 -c 'import sys; sys.exit(not (3, 10) <= sys.version_info[:2] <= (3, 14))' || {
    echo "error: Python 3.10-3.14 is required; python3 is $(python3 -V 2>&1)" >&2
    exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_ROOT="$(cd "$CLI_ROOT/../../lib" && pwd)"
BLUE_DATA_LIB="$(cd "$CLI_ROOT/../../../blue-data/lib" && pwd)"

echo "Installing blue-data..."
pip install -e "$BLUE_DATA_LIB"

# With every lib extra: `answer`/`extract` need openai, BM25 search needs rank-bm25
# (#150). Same as the top-level scripts/install.sh.
echo "Installing blue-data-kb-source..."
pip install -e "$LIB_ROOT[all]"

echo "Installing blue-data-kb-cli..."
pip install -e "$CLI_ROOT"

echo "Done. Run: blue-kb --help"
