"""blue-data-wordnet-mcp server.

Exposes `nltk.corpus.wordnet` lookups as MCP tools so LLM clients (Claude
Desktop, Claude Code, and any MCP-compatible host) can resolve a word to its
senses (synsets) and walk the relations between them. Read-only — there are
no write tools; WordNet itself is a fixed, pre-built lexical database.

Tools
-----
lookup_synsets   — resolve a word (optionally filtered by part of speech) to
                    candidate synsets. Usually the first call: WordNet
                    relations are defined on synsets, not raw words, so an
                    ambiguous word needs disambiguating before the others are
                    useful.
get_synset       — full detail for one synset id (e.g. 'dog.n.01').
get_synonyms     — lemma names sharing a synset's exact sense.
get_hypernyms    — walk toward more general senses (is-a, upward).
get_hyponyms     — walk toward more specific senses (is-a, downward).
get_meronyms     — part/member/substance the synset is made of (has-a).
get_holonyms     — part/member/substance wholes the synset belongs to.
get_antonyms     — opposite-meaning words for a specific word sense.
get_similarity   — path/wup/lch semantic similarity between two synsets.
lemmatize        — morphological base form of an inflected word (morphy).

Quick start
-----------
    # Install
    pip install -e .

    # Run (stdio transport for Claude Desktop / Claude Code)
    blue-data-wordnet-mcp

Claude Desktop config (~/.config/claude-desktop/claude_desktop_config.json):
    {
      "mcpServers": {
        "wordnet": {
          "command": "blue-data-wordnet-mcp"
        }
      }
    }

The WordNet corpus is downloaded automatically on first use if not already
present locally (see blue_data_wordnet_mcp.nltk_data).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from nltk.corpus import wordnet as wn
from nltk.corpus.reader.wordnet import Synset

from blue_data_wordnet_mcp.nltk_data import ensure_wordnet_data

mcp = FastMCP(
    "wordnet",
    instructions=(
        "WordNet lexical database lookups. Resolve a word to its senses with "
        "lookup_synsets, then walk hypernym/hyponym chains, part/whole relations, "
        "synonyms, antonyms, and semantic similarity between senses. Read-only."
    ),
)

_VALID_POS = {"n", "v", "a", "r", "s"}
_MORPHY_POS = {"n": wn.NOUN, "v": wn.VERB, "a": wn.ADJ, "r": wn.ADV}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _err(e: Exception) -> str:
    return json.dumps({"error": str(e)}, indent=2)


def _validate_pos(pos: Optional[str]) -> None:
    if pos is not None and pos not in _VALID_POS:
        raise ValueError(f"invalid pos '{pos}', expected one of n, v, a, r, s")


def _synset_to_dict(s: Synset) -> dict[str, Any]:
    return {
        "id": s.name(),
        "pos": s.pos(),
        "definition": s.definition(),
        "examples": s.examples(),
        "lemma_names": s.lemma_names(),
    }


def _walk(start: Synset, depth: int, direction: str) -> list[dict[str, Any]]:
    """BFS up (direction='hypernyms') or down (direction='hyponyms') from
    *start*, up to *depth* levels. A synset reachable via multiple paths keeps
    its shortest distance."""
    visited: dict[str, int] = {}
    frontier = [start]
    for d in range(1, depth + 1):
        next_frontier = []
        for s in frontier:
            neighbors = s.hypernyms() if direction == "hypernyms" else s.hyponyms()
            for n in neighbors:
                if n.name() not in visited:
                    visited[n.name()] = d
                    next_frontier.append(n)
        frontier = next_frontier
        if not frontier:
            break
    results = []
    for name, d in visited.items():
        item = _synset_to_dict(wn.synset(name))
        item["distance"] = d
        results.append(item)
    results.sort(key=lambda r: (r["distance"], r["id"]))
    return results


def _part_whole(s: Synset, kind: str) -> list[dict[str, Any]]:
    """kind='meronyms' (parts of s) or 'holonyms' (wholes s belongs to),
    tagged with relation='member'/'part'/'substance'."""
    results = []
    for relation in ("member", "part", "substance"):
        getter = getattr(s, f"{relation}_{kind}")
        for related in getter():
            item = _synset_to_dict(related)
            item["relation"] = relation
            results.append(item)
    return results


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

@mcp.tool()
async def lookup_synsets(word: str, pos: Optional[str] = None) -> str:
    """Resolve a word to its candidate WordNet synsets (word senses). Usually
    the first call — pick a synset id from the result and pass it to
    get_hypernyms/get_hyponyms/get_meronyms/get_holonyms/get_synonyms/
    get_similarity.

    Args:
        word: The word to look up, e.g. 'bank', 'run'.
        pos:  Restrict to one part of speech: 'n' (noun), 'v' (verb), 'a'
              (adjective), 'r' (adverb), 's' (adjective satellite). Omit to
              search all.
    """
    try:
        ensure_wordnet_data()
        _validate_pos(pos)
        return _ok([_synset_to_dict(s) for s in wn.synsets(word, pos=pos)])
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_synset(synset_id: str) -> str:
    """Full detail for one synset.

    Args:
        synset_id: A WordNet synset name, e.g. 'dog.n.01' (from lookup_synsets).
    """
    try:
        ensure_wordnet_data()
        return _ok(_synset_to_dict(wn.synset(synset_id)))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_synonyms(synset_id: str) -> str:
    """The synonym set (lemma names) of a synset — the words that share this
    exact sense.

    Args:
        synset_id: A WordNet synset name, e.g. 'dog.n.01' (from lookup_synsets).
    """
    try:
        ensure_wordnet_data()
        s = wn.synset(synset_id)
        return _ok({"id": s.name(), "synonyms": s.lemma_names()})
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Hierarchy (is-a)
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_hypernyms(synset_id: str, depth: int = 1) -> str:
    """Walk up the is-a hierarchy toward more general senses (e.g. dog ->
    canine -> carnivore). Each result is tagged with its distance from
    *synset_id*.

    Args:
        synset_id: A WordNet synset name, e.g. 'dog.n.01'.
        depth:     How many levels to walk up (default 1 = immediate parents).
    """
    try:
        ensure_wordnet_data()
        if depth < 1:
            raise ValueError("depth must be >= 1")
        return _ok(_walk(wn.synset(synset_id), depth, "hypernyms"))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_hyponyms(synset_id: str, depth: int = 1) -> str:
    """Walk down the is-a hierarchy toward more specific senses (e.g. dog ->
    puppy, poodle). Each result is tagged with its distance from *synset_id*.
    A word with many hyponyms (e.g. 'animal') can return a large result at
    higher depths.

    Args:
        synset_id: A WordNet synset name, e.g. 'dog.n.01'.
        depth:     How many levels to walk down (default 1 = immediate children).
    """
    try:
        ensure_wordnet_data()
        if depth < 1:
            raise ValueError("depth must be >= 1")
        return _ok(_walk(wn.synset(synset_id), depth, "hyponyms"))
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Part / whole
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_meronyms(synset_id: str) -> str:
    """Parts, members, and substances that make up this synset (X has-a Y),
    e.g. 'tree' has meronym 'trunk' (part), 'car' has meronym 'engine' (part).
    Each result is tagged relation='member'/'part'/'substance'.

    Args:
        synset_id: A WordNet synset name, e.g. 'car.n.01'.
    """
    try:
        ensure_wordnet_data()
        return _ok(_part_whole(wn.synset(synset_id), "meronyms"))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_holonyms(synset_id: str) -> str:
    """Wholes that this synset is a part, member, or substance of (X part-of
    Y), e.g. 'wheel' has holonym 'car' (part), 'water' has holonym 'ocean'
    (substance/member, depending on sense). Each result is tagged
    relation='member'/'part'/'substance'.

    Args:
        synset_id: A WordNet synset name, e.g. 'wheel.n.01'.
    """
    try:
        ensure_wordnet_data()
        return _ok(_part_whole(wn.synset(synset_id), "holonyms"))
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Antonyms / similarity / morphology
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_antonyms(word: str, pos: Optional[str] = None) -> str:
    """Opposite-meaning words for *word*, one result per sense that actually
    has an antonym recorded (antonymy is defined per word sense, not per
    synset as a whole).

    Args:
        word: The word to look up, e.g. 'good', 'hot'.
        pos:  Restrict to one part of speech: 'n', 'v', 'a', 'r', 's'. Omit to
              search all.
    """
    try:
        ensure_wordnet_data()
        _validate_pos(pos)
        target = word.replace(" ", "_").lower()
        results = []
        for s in wn.synsets(word, pos=pos):
            for lemma in s.lemmas():
                if lemma.name().lower() != target:
                    continue
                antonyms = lemma.antonyms()
                if antonyms:
                    results.append(
                        {
                            "synset_id": s.name(),
                            "definition": s.definition(),
                            "lemma": lemma.name(),
                            "antonyms": [a.name() for a in antonyms],
                        }
                    )
        return _ok(results)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_similarity(synset_id_1: str, synset_id_2: str, metric: str = "wup") -> str:
    """Semantic similarity between two synsets. Use lookup_synsets first to
    resolve ambiguous words to specific synset ids — similarity is only
    meaningful between two already-disambiguated senses.

    Args:
        synset_id_1: A WordNet synset name, e.g. 'dog.n.01'.
        synset_id_2: A WordNet synset name, e.g. 'cat.n.01'.
        metric:      'path' (1/(shortest path length + 1)), 'wup' (Wu-Palmer,
                     default), or 'lch' (Leacock-Chodorow — requires both
                     synsets to share the same part of speech). All range
                     roughly 0 (unrelated) to 1 (identical) except 'lch',
                     which is not bounded by 1.
    """
    try:
        ensure_wordnet_data()
        s1 = wn.synset(synset_id_1)
        s2 = wn.synset(synset_id_2)
        if metric == "path":
            score = s1.path_similarity(s2)
        elif metric == "wup":
            score = s1.wup_similarity(s2)
        elif metric == "lch":
            if s1.pos() != s2.pos():
                raise ValueError("lch similarity requires both synsets to share the same part of speech")
            score = s1.lch_similarity(s2)
        else:
            raise ValueError(f"unknown metric '{metric}', expected one of path, wup, lch")
        return _ok({"synset_id_1": s1.name(), "synset_id_2": s2.name(), "metric": metric, "score": score})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def lemmatize(word: str, pos: Optional[str] = None) -> str:
    """Reduce an inflected word to its dictionary base form (morphological
    analysis only — e.g. 'running' -> 'run', 'geese' -> 'goose'; this is not
    a synonym lookup).

    Args:
        word: The inflected word, e.g. 'running', 'better' (as adjective), 'mice'.
        pos:  'n', 'v', 'a', or 'r'. Omit to try all four and return whichever match.
    """
    try:
        ensure_wordnet_data()
        if pos is not None:
            if pos not in _MORPHY_POS:
                raise ValueError(f"invalid pos '{pos}' for lemmatize, expected one of n, v, a, r")
            return _ok({"word": word, "pos": pos, "lemma": wn.morphy(word, _MORPHY_POS[pos])})
        lemmas_by_pos = {}
        for p, wn_pos in _MORPHY_POS.items():
            base = wn.morphy(word, wn_pos)
            if base is not None:
                lemmas_by_pos[p] = base
        return _ok({"word": word, "lemmas_by_pos": lemmas_by_pos})
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
