"""Ranked name resolution for the ingest path: exact, then normalized, then search.

A *resolution* answers "does something that may *mean* this name exist?" — unlike the
exact `find_*` lookups, which answer "does exactly this name exist?" and stay frozen.
`NodeResolver` never picks a winner: it returns ranked candidates and the caller owns
the pick-or-raise policy.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import CLASS, ENTITY, IS_INSTANCE_OF, PROPERTY, RELATION
from blue_data_kb_source.search.indexer import KBSearchIndexer
from blue_data_kb_source.types import ResolutionCandidate

_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")


def _normalize(name: str) -> str:
    """Casefold, trim/collapse whitespace, strip punctuation, NFKC + accent fold.

    Deliberately no legal-suffix dropping: that would merge "Apple Inc." with the
    fruit, and belongs in the alias layer where a human confirms the mapping.
    """
    text = unicodedata.normalize("NFKC", name)
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    text = _PUNCTUATION.sub("", text).casefold()
    return _WHITESPACE.sub(" ", text).strip()


class NodeResolver:
    """Label-scoped candidate resolution over one database.

    Each resolve method returns ranked `ResolutionCandidate`s: exact name matches,
    then normalized matches (equal after `_normalize`), then search hits — via the
    database's index when configured (semantic when available, else keyword; only
    semantic hits carry scores), else an unranked name/description substring scan.
    A node is reported once, under its strongest match.
    """

    def __init__(self, source: GraphSource, search_indexer: KBSearchIndexer | None = None) -> None:
        self._source = source
        self._indexer = search_indexer

    async def resolve_class(
        self, database: str, name: str, k: int = 5
    ) -> list[ResolutionCandidate]:
        return self._resolve(database, CLASS, name, k)

    async def resolve_relation(
        self, database: str, name: str, k: int = 5
    ) -> list[ResolutionCandidate]:
        return self._resolve(database, RELATION, name, k)

    async def resolve_property(
        self, database: str, name: str, k: int = 5
    ) -> list[ResolutionCandidate]:
        return self._resolve(database, PROPERTY, name, k)

    async def resolve_entity(
        self,
        database: str,
        name: str,
        class_id: str | None = None,
        k: int = 5,
        include_search: bool = True,
    ) -> list[ResolutionCandidate]:
        keep = None
        if class_id is not None:
            def keep(node_id: str) -> bool:
                nbs = self._source.get_neighbors(
                    database, node_id, edge_type=IS_INSTANCE_OF, direction="out"
                )
                return any(nb["node"]["id"] == class_id for nb in nbs)
        return self._resolve(database, ENTITY, name, k, keep=keep, include_search=include_search)

    # ------------------------------------------------------------------ #
    # Internals                                                            #
    # ------------------------------------------------------------------ #

    def _resolve(
        self,
        database: str,
        label: str,
        name: str,
        k: int,
        keep: Callable[[str], bool] | None = None,
        include_search: bool = True,
    ) -> list[ResolutionCandidate]:
        exact: list[ResolutionCandidate] = []
        normalized: list[ResolutionCandidate] = []
        seen: set[str] = set()
        target = _normalize(name)
        for node in self._source.match_nodes(database, label=label):
            node_name = node.get("properties", {}).get("name") or ""
            if node_name == name:
                tier = exact
            elif _normalize(node_name) == target:
                tier = normalized
            else:
                continue
            seen.add(node["id"])
            tier.append(ResolutionCandidate(
                node_id=node["id"], name=node_name, label=label,
                match_type="exact" if tier is exact else "normalized",
            ))
        candidates = exact + normalized
        if include_search:
            for node_id, node_name, score in self._search_hits(database, label, name, k):
                if node_id in seen:
                    continue
                seen.add(node_id)
                candidates.append(ResolutionCandidate(
                    node_id=node_id, name=node_name, label=label, match_type="search", score=score,
                ))
        if keep is not None:
            candidates = [c for c in candidates if keep(c.node_id)]
        return candidates[:k]

    def _search_hits(
        self, database: str, label: str, query: str, k: int
    ) -> list[tuple[str, str, float | None]]:
        """Ranked (node_id, name, score) index hits, or an unranked substring scan."""
        if self._indexer is not None:
            mode = "semantic" if self._indexer.has_semantic else "keyword"
            search = self._indexer.search_data if label == ENTITY else self._indexer.search_schema
            hits = search(database, query, k=k, mode=mode, type_filter=label)
            return [(h.id, h.name, h.score) for h in hits]
        q = query.lower()
        results: list[tuple[str, str, float | None]] = []
        for node in self._source.match_nodes(database, label=label):
            props = node.get("properties", {})
            node_name = props.get("name", "") or ""
            desc = props.get("description", "") or ""
            if q in node_name.lower() or q in desc.lower():
                results.append((node["id"], node_name, None))
        return results
