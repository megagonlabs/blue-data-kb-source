"""Lazy WordNet corpus download.

nltk ships WordNet as a downloadable corpus, not a package dependency — the
first call to any tool triggers a one-time download (cached under
BLUE_DATA_WORDNET_MCP_NLTK_DATA_DIR, or nltk's default ~/nltk_data if unset)
if the corpus isn't already present locally.
"""
from __future__ import annotations

import os

import nltk

_ready = False

_PACKAGES = (("wordnet", "corpora/wordnet"), ("omw-1.4", "corpora/omw-1.4"))


def ensure_wordnet_data() -> None:
    global _ready
    if _ready:
        return
    data_dir = os.environ.get("BLUE_DATA_WORDNET_MCP_NLTK_DATA_DIR")
    if data_dir and data_dir not in nltk.data.path:
        nltk.data.path.insert(0, data_dir)
    for package, path in _PACKAGES:
        try:
            nltk.data.find(path)
        except LookupError:
            nltk.download(package, download_dir=data_dir, quiet=True)
    _ready = True


def reset() -> None:
    """Clear the readiness cache — for tests."""
    global _ready
    _ready = False
