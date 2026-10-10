"""Keyword matching for lookups made without a search index."""
from __future__ import annotations

import re

_WORD = re.compile(r"[0-9a-z]+")
_STOPWORDS = frozenset(
    "a an and are as at be by do does did for from has have had how i in is it its me my "
    "of on or our she he her his their them they the to was were what when where which who "
    "whom whose why will with you your".split()
)


def words(text: str) -> set[str]:
    """Lowercased alphanumeric words minus stopwords — `PROFICIENT_IN` → {"proficient"}."""
    return set(_WORD.findall(text.lower())) - _STOPWORDS


def match_score(query: str, text: str) -> int:
    """How well *text* answers a keyword *query*: the whole query as a substring ranks
    first, then the number of distinct query words present (0 = no match).

    The index-free fallbacks used to be the whole-query substring test alone, so natural
    queries matched nothing — "is proficient in" never found the PROFICIENT_IN relation,
    "Alice Johnson programming" never found Alice — and the agents relying on them
    skipped facts or searched until their turn limit (#133)."""
    q = query.lower().strip()
    if q and q in text.lower():
        return 1000
    return len(words(query) & words(text))
