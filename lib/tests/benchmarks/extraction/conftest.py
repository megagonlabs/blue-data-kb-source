from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def vcr_config():
    """Never record API keys into committed cassette files."""
    return {"filter_headers": ["authorization"]}


@pytest.fixture(scope="module")
def vcr_cassette_dir() -> str:
    return str(Path(__file__).parent / "cassettes")


@pytest.fixture
def kb():
    """Override the global `blue-kb` fixture, which builds a KBSource with no search
    index — see `runner.make_kb` for why that silently zeroes fact scores."""
    from ..common import runner

    return runner.make_kb("extraction-benchmark")
