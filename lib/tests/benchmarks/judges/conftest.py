from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def vcr_config():
    """Never record API keys into committed cassette files."""
    return {"filter_headers": ["authorization"]}


@pytest.fixture(scope="module")
def vcr_cassette_dir() -> str:
    return str(Path(__file__).parent / "cassettes")
