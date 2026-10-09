"""Tests for blue-data-wordnet-mcp tool functions.

Run directly against the real WordNet corpus (nltk downloads it once, on
first use, into BLUE_DATA_WORDNET_MCP_NLTK_DATA_DIR or ~/nltk_data) — no
mocking, since WordNet is a fixed, offline dataset once downloaded.
"""
import json

import pytest

from blue_data_wordnet_mcp.server import (
    get_antonyms,
    get_holonyms,
    get_hypernyms,
    get_hyponyms,
    get_meronyms,
    get_similarity,
    get_synonyms,
    get_synset,
    lemmatize,
    lookup_synsets,
)


def _load(result: str):
    return json.loads(result)


# ---------------------------------------------------------------------------
# lookup_synsets / get_synset / get_synonyms
# ---------------------------------------------------------------------------

async def test_lookup_synsets_filters_by_pos():
    data = _load(await lookup_synsets("bank", pos="n"))
    assert len(data) > 1
    assert all(s["pos"] == "n" for s in data)
    assert any(s["id"] == "bank.n.01" for s in data)


async def test_lookup_synsets_invalid_pos_errors():
    data = _load(await lookup_synsets("dog", pos="x"))
    assert "error" in data


async def test_get_synset_returns_detail():
    data = _load(await get_synset("dog.n.01"))
    assert data["id"] == "dog.n.01"
    assert data["pos"] == "n"
    assert "dog" in data["lemma_names"]
    assert data["definition"]


async def test_get_synset_unknown_id_errors():
    data = _load(await get_synset("not_a_real_synset.n.99"))
    assert "error" in data


async def test_get_synonyms():
    data = _load(await get_synonyms("dog.n.01"))
    assert data["id"] == "dog.n.01"
    assert "domestic_dog" in data["synonyms"]


# ---------------------------------------------------------------------------
# hypernyms / hyponyms
# ---------------------------------------------------------------------------

async def test_get_hypernyms_depth_1():
    data = _load(await get_hypernyms("dog.n.01", depth=1))
    assert all(item["distance"] == 1 for item in data)
    assert any(item["id"] == "canine.n.02" for item in data)


async def test_get_hypernyms_depth_2_includes_farther_ancestors():
    shallow = {item["id"] for item in _load(await get_hypernyms("dog.n.01", depth=1))}
    deep = _load(await get_hypernyms("dog.n.01", depth=2))
    deep_ids = {item["id"] for item in deep}
    assert shallow <= deep_ids
    assert any(item["distance"] == 2 for item in deep)


async def test_get_hypernyms_invalid_depth_errors():
    data = _load(await get_hypernyms("dog.n.01", depth=0))
    assert "error" in data


async def test_get_hyponyms_depth_1():
    data = _load(await get_hyponyms("dog.n.01", depth=1))
    ids = {item["id"] for item in data}
    assert "puppy.n.01" in ids


# ---------------------------------------------------------------------------
# meronyms / holonyms
# ---------------------------------------------------------------------------

async def test_get_meronyms_tags_relation_type():
    data = _load(await get_meronyms("car.n.01"))
    ids = {item["id"] for item in data}
    assert "car_door.n.01" in ids
    assert all(item["relation"] in ("member", "part", "substance") for item in data)


async def test_get_holonyms_inverts_meronyms():
    data = _load(await get_holonyms("wheel.n.01"))
    ids = {item["id"] for item in data}
    assert "wheeled_vehicle.n.01" in ids


# ---------------------------------------------------------------------------
# antonyms / similarity / lemmatize
# ---------------------------------------------------------------------------

async def test_get_antonyms_per_sense():
    data = _load(await get_antonyms("good", pos="a"))
    assert any("bad" in item["antonyms"] for item in data)


async def test_get_antonyms_no_antonym_returns_empty():
    data = _load(await get_antonyms("dog", pos="n"))
    assert data == []


async def test_get_similarity_identical_synset_scores_highest():
    # Not guaranteed to be exactly 1.0: nltk's wup_similarity uses shortest-path
    # depth, and a synset reachable via hypernym paths of different lengths
    # (as dog.n.01 is) can score itself just under 1.0.
    data = _load(await get_similarity("dog.n.01", "dog.n.01", metric="wup"))
    assert data["score"] > 0.9
    other = _load(await get_similarity("dog.n.01", "cat.n.01", metric="wup"))
    assert data["score"] > other["score"]


async def test_get_similarity_related_words_score_high():
    data = _load(await get_similarity("dog.n.01", "cat.n.01", metric="wup"))
    assert data["score"] > 0.5


async def test_get_similarity_lch_requires_matching_pos():
    data = _load(await get_similarity("dog.n.01", "run.v.01", metric="lch"))
    assert "error" in data


async def test_get_similarity_unknown_metric_errors():
    data = _load(await get_similarity("dog.n.01", "cat.n.01", metric="cosine"))
    assert "error" in data


async def test_lemmatize_specific_pos():
    data = _load(await lemmatize("running", pos="v"))
    assert data["lemma"] == "run"


async def test_lemmatize_all_pos():
    data = _load(await lemmatize("geese"))
    assert data["lemmas_by_pos"].get("n") == "goose"


def test_importing_the_server_does_not_load_the_corpus():
    """The corpus must not be touched at import: on a fresh machine it isn't downloaded
    yet, and ensure_wordnet_data() (which downloads it, and adds
    BLUE_DATA_WORDNET_MCP_NLTK_DATA_DIR to the search path) only runs on the first
    tool call (#131)."""
    import subprocess
    import sys

    probe = (
        "from nltk.corpus import wordnet as wn\n"
        "import blue_data_wordnet_mcp.server\n"
        "print(type(wn).__name__)\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "LazyCorpusLoader"
