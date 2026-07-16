from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import (
    CLASS,
    DEFINE_PROPERTY,
    DOMAIN,
    ENTITY,
    EXTRACTED_FROM,
    FACT,
    HAS_PROPERTY,
    HEAD,
    IS_INSTANCE_OF,
    IS_VALUE_OF,
    PROPERTY,
    RANGE,
    RELATION,
    REPRESENTS,
    TAIL,
)
from blue_data_kb_source.types import DataSearchResult, KBError, SchemaSearchResult

if TYPE_CHECKING:
    from blue_data.source.vector.vector_source import VectorSource

_SCHEMA_COLL = "schema"
_DATA_COLL = "data"


class KBSearchIndexer:
    """Dual-mode search index for KB nodes.

    Always maintains an in-memory BM25 corpus (requires rank-bm25 at search time).
    Optionally maintains a vector index when vector_source + embedding_fn are provided.
    Registered as a global change-notifier hook; also called directly by KBSource
    for deletes and updates (which do not fire change events).
    """

    def __init__(
        self,
        source: GraphSource,
        vector_source: VectorSource | None = None,
        embedding_fn: Callable[[str], list[float]] | None = None,
        vector_dim: int = 1536,
        distance_metric: str = "COSINE",
    ) -> None:
        self._source = source
        self._vector_source = vector_source
        self._embedding_fn = embedding_fn
        self._vector_dim = vector_dim
        self._distance_metric = distance_metric
        # BM25 corpus: db -> collection -> node_id -> text
        self._corpus: dict[str, dict[str, dict[str, str]]] = {}
        # Metadata: db -> collection -> node_id -> {type, name}
        self._meta: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
        # Track which (db, collection) vector indices have been created
        self._vector_indices_created: set[str] = set()

    @property
    def has_semantic(self) -> bool:
        return self._vector_source is not None and self._embedding_fn is not None

    # ------------------------------------------------------------------ #
    # Global hook entry point                                              #
    # ------------------------------------------------------------------ #

    def on_change(self, event: dict) -> None:
        database = event.get("database")
        if not database:
            return
        etype = event.get("type", "")
        node_id = event.get("id")

        # ---- additions ----
        if etype == "entity_added":
            self._index_entity_from_event(database, event)
        elif etype == "entity_property_added":
            entity_id = event.get("entity_id")
            if entity_id:
                self.reindex_node(database, entity_id)
        elif etype == "fact_added":
            self._index_fact_from_event(database, event)
        elif etype == "fact_property_added":
            fact_id = event.get("fact_id")
            if fact_id:
                self.reindex_node(database, fact_id)
        elif etype == "class_declared":
            self._index_class_from_event(database, event)
        elif etype == "class_property_declared":
            self._index_property_from_event(database, event)
            class_id = event.get("class_id")
            if class_id:
                self.reindex_node(database, class_id)
        elif etype == "relation_declared":
            self._index_relation_from_event(database, event)
        elif etype == "relation_property_declared":
            self._index_property_from_event(database, event)
            relation_id = event.get("relation_id")
            if relation_id:
                self.reindex_node(database, relation_id)

        # ---- updates ----
        elif etype in ("class_updated", "relation_updated", "property_updated",
                       "entity_updated", "fact_updated"):
            if node_id:
                self.reindex_node(database, node_id)
        elif etype == "entity_property_updated":
            entity_id = event.get("entity_id")
            if entity_id:
                self.reindex_node(database, entity_id)
        elif etype == "fact_property_updated":
            fact_id = event.get("fact_id")
            if fact_id:
                self.reindex_node(database, fact_id)

        # ---- deletes ----
        elif etype in ("class_deleted", "relation_deleted", "property_deleted",
                       "entity_deleted", "fact_deleted"):
            if node_id:
                self.remove(database, node_id)
        elif etype == "entity_property_deleted":
            entity_id = event.get("entity_id")
            if entity_id:
                self.reindex_node(database, entity_id)
        elif etype == "fact_property_deleted":
            fact_id = event.get("fact_id")
            if fact_id:
                self.reindex_node(database, fact_id)

    # ------------------------------------------------------------------ #
    # Direct index management (deletes / updates)                          #
    # ------------------------------------------------------------------ #

    def reindex_node(self, database: str, node_id: str) -> None:
        """Re-fetch a node from the graph and rebuild its index entry."""
        node = self._source.get_node(database, node_id)
        if not node:
            return
        label = node.get("label", "")
        props = node.get("properties", {}) or {}
        name = props.get("name", "")
        description = props.get("description") or ""

        if label == ENTITY:
            cls_nbs = self._source.get_neighbors(database, node_id, edge_type=IS_INSTANCE_OF, direction="out")
            class_name = cls_nbs[0]["node"].get("properties", {}).get("name") if cls_nbs else None
            text = self._entity_text(database, node_id, name, class_name or "", description)
            self._upsert(database, _DATA_COLL, node_id, text, ENTITY, name)
        elif label == FACT:
            rel_nbs  = self._source.get_neighbors(database, node_id, edge_type=REPRESENTS, direction="out")
            head_nbs = self._source.get_neighbors(database, node_id, edge_type=HEAD, direction="out")
            tail_nbs = self._source.get_neighbors(database, node_id, edge_type=TAIL, direction="out")
            text = self._fact_text(
                database, node_id,
                _node_name(head_nbs) or "", _node_name(rel_nbs) or "", _node_name(tail_nbs) or "",
                description,
            )
            self._upsert(database, _DATA_COLL, node_id, text, FACT, name)
        elif label == CLASS:
            text = self._class_text(database, node_id, name, description)
            self._upsert(database, _SCHEMA_COLL, node_id, text, CLASS, name)
        elif label == RELATION:
            text = self._relation_text(database, node_id, name, description)
            self._upsert(database, _SCHEMA_COLL, node_id, text, RELATION, name)
        elif label == PROPERTY:
            text = self._property_text(name, description)
            self._upsert(database, _SCHEMA_COLL, node_id, text, PROPERTY, name)

    def remove(self, database: str, node_id: str) -> None:
        """Remove a node from all index collections."""
        for collection in (_DATA_COLL, _SCHEMA_COLL):
            self._delete_bm25(database, collection, node_id)
            self._delete_vector(database, collection, node_id)

    def reindex_all(self, database: str) -> None:
        """Bulk rebuild: iterate every node in the graph and re-index it."""
        for label in (CLASS, RELATION, PROPERTY, ENTITY, FACT):
            for node in self._source.match_nodes(database, label=label):
                self.reindex_node(database, node["id"])

    # ------------------------------------------------------------------ #
    # Search                                                               #
    # ------------------------------------------------------------------ #

    def search_schema(
        self,
        database: str,
        query: str,
        k: int = 5,
        mode: str = "keyword",
        type_filter: str | list[str] | None = None,
    ) -> list[SchemaSearchResult]:
        hits = self._search(database, _SCHEMA_COLL, query, k, mode, type_filter)
        results: list[SchemaSearchResult] = []
        for node_id, score in hits:
            node = self._source.get_node(database, node_id)
            if not node:
                continue
            props = node.get("properties", {}) or {}
            node_meta = self._meta.get(database, {}).get(_SCHEMA_COLL, {}).get(node_id, {})
            results.append(SchemaSearchResult(
                id=node_id,
                type=node_meta.get("type", node.get("label", "")),
                name=props.get("name", ""),
                description=props.get("description") or "",
                score=score if mode == "semantic" else None,
            ))
        return results

    def search_data(
        self,
        database: str,
        query: str,
        k: int = 5,
        mode: str = "keyword",
        type_filter: str | list[str] | None = None,
    ) -> list[DataSearchResult]:
        hits = self._search(database, _DATA_COLL, query, k, mode, type_filter)
        results: list[DataSearchResult] = []
        for node_id, score in hits:
            node = self._source.get_node(database, node_id)
            if not node:
                continue
            label = node.get("label", "")
            props = node.get("properties", {}) or {}
            name = props.get("name", "")
            description = props.get("description") or ""
            if label == ENTITY:
                cls_nbs = self._source.get_neighbors(database, node_id, edge_type=IS_INSTANCE_OF, direction="out")
                results.append(DataSearchResult(
                    id=node_id, type=ENTITY, name=name, description=description,
                    class_name=cls_nbs[0]["node"].get("properties", {}).get("name") if cls_nbs else None,
                    score=score if mode == "semantic" else None,
                ))
            elif label == FACT:
                rel_nbs  = self._source.get_neighbors(database, node_id, edge_type=REPRESENTS, direction="out")
                head_nbs = self._source.get_neighbors(database, node_id, edge_type=HEAD, direction="out")
                tail_nbs = self._source.get_neighbors(database, node_id, edge_type=TAIL, direction="out")
                results.append(DataSearchResult(
                    id=node_id, type=FACT, name=name, description=description,
                    head_name=_node_name(head_nbs),
                    relation_name=_node_name(rel_nbs),
                    tail_name=_node_name(tail_nbs),
                    score=score if mode == "semantic" else None,
                ))
        return results

    # ------------------------------------------------------------------ #
    # Event-driven index helpers                                           #
    # ------------------------------------------------------------------ #

    def _index_entity_from_event(self, database: str, event: dict) -> None:
        node_id = event["id"]
        name = event.get("name", "")
        class_name = event.get("class_name") or ""
        node = self._source.get_node(database, node_id)
        description = (node or {}).get("properties", {}).get("description") or ""
        text = self._entity_text(database, node_id, name, class_name, description)
        self._upsert(database, _DATA_COLL, node_id, text, ENTITY, name)

    def _index_fact_from_event(self, database: str, event: dict) -> None:
        node_id = event["id"]
        name = event.get("name", "")
        head_name = event.get("head_entity_name") or ""
        relation_name = event.get("relation_name") or ""
        tail_name = event.get("tail_entity_name") or ""
        node = self._source.get_node(database, node_id)
        description = (node or {}).get("properties", {}).get("description") or ""
        text = self._fact_text(database, node_id, head_name, relation_name, tail_name, description)
        self._upsert(database, _DATA_COLL, node_id, text, FACT, name)

    def _index_class_from_event(self, database: str, event: dict) -> None:
        node_id = event["id"]
        name = event.get("name", "")
        description = event.get("description") or ""
        text = self._class_text(database, node_id, name, description)
        self._upsert(database, _SCHEMA_COLL, node_id, text, CLASS, name)

    def _index_relation_from_event(self, database: str, event: dict) -> None:
        node_id = event["id"]
        name = event.get("name", "")
        description = event.get("description") or ""
        text = self._relation_text(database, node_id, name, description)
        self._upsert(database, _SCHEMA_COLL, node_id, text, RELATION, name)

    def _index_property_from_event(self, database: str, event: dict) -> None:
        node_id = event["id"]
        name = event.get("name", "")
        description = event.get("description") or ""
        text = self._property_text(name, description)
        self._upsert(database, _SCHEMA_COLL, node_id, text, PROPERTY, name)

    # ------------------------------------------------------------------ #
    # Text builders                                                         #
    # ------------------------------------------------------------------ #

    def _entity_text(self, database: str, node_id: str, name: str, class_name: str, description: str) -> str:
        parts = [name, class_name, description]
        for nb in self._source.get_neighbors(database, node_id, edge_type=HAS_PROPERTY, direction="out"):
            lit = nb["node"]
            lit_p = lit.get("properties", {})
            prop_nbs = self._source.get_neighbors(database, lit["id"], edge_type=IS_VALUE_OF, direction="out")
            prop_name = _node_name(prop_nbs) or ""
            value = str(lit_p.get("value", ""))
            if prop_name:
                parts.append(f"{prop_name}: {value}")
        prov = self._provenance_text(database, node_id)
        if prov:
            parts.append(prov)
        return " ".join(p for p in parts if p).strip()

    def _fact_text(
        self, database: str, node_id: str,
        head_name: str, relation_name: str, tail_name: str, description: str,
    ) -> str:
        parts = [head_name, relation_name, tail_name, description]
        for nb in self._source.get_neighbors(database, node_id, edge_type=HAS_PROPERTY, direction="out"):
            lit = nb["node"]
            lit_p = lit.get("properties", {})
            prop_nbs = self._source.get_neighbors(database, lit["id"], edge_type=IS_VALUE_OF, direction="out")
            prop_name = _node_name(prop_nbs) or ""
            value = str(lit_p.get("value", ""))
            if prop_name:
                parts.append(f"{prop_name}: {value}")
        prov = self._provenance_text(database, node_id)
        if prov:
            parts.append(prov)
        return " ".join(p for p in parts if p).strip()

    def _class_text(self, database: str, node_id: str, name: str, description: str) -> str:
        parts = [name, "CLASS", description]
        for nb in self._source.get_neighbors(database, node_id, edge_type=DEFINE_PROPERTY, direction="out"):
            prop_p = nb["node"].get("properties", {})
            prop_name = prop_p.get("name", "")
            prop_desc = prop_p.get("description") or ""
            if prop_name:
                parts.append(f"{prop_name}: {prop_desc}")
        return " ".join(p for p in parts if p).strip()

    def _relation_text(self, database: str, node_id: str, name: str, description: str) -> str:
        parts = [name, "RELATION", description]
        for nb in self._source.get_neighbors(database, node_id, edge_type=DOMAIN, direction="out"):
            domain_name = nb["node"].get("properties", {}).get("name", "")
            if domain_name:
                parts.append(f"domain: {domain_name}")
        for nb in self._source.get_neighbors(database, node_id, edge_type=RANGE, direction="out"):
            range_name = nb["node"].get("properties", {}).get("name", "")
            if range_name:
                parts.append(f"range: {range_name}")
        for nb in self._source.get_neighbors(database, node_id, edge_type=DEFINE_PROPERTY, direction="out"):
            prop_p = nb["node"].get("properties", {})
            prop_name = prop_p.get("name", "")
            prop_desc = prop_p.get("description") or ""
            if prop_name:
                parts.append(f"{prop_name}: {prop_desc}")
        return " ".join(p for p in parts if p).strip()

    def _property_text(self, name: str, description: str) -> str:
        return " ".join(p for p in [name, "PROPERTY", description] if p).strip()

    def _provenance_text(self, database: str, node_id: str) -> str | None:
        nbs = self._source.get_neighbors(database, node_id, edge_type=EXTRACTED_FROM, direction="out")
        return nbs[0]["node"].get("properties", {}).get("text") if nbs else None

    # ------------------------------------------------------------------ #
    # BM25 corpus management                                               #
    # ------------------------------------------------------------------ #

    def _get_corpus(self, database: str, collection: str) -> dict[str, str]:
        return self._corpus.setdefault(database, {}).setdefault(collection, {})

    def _get_meta(self, database: str, collection: str) -> dict[str, dict[str, Any]]:
        return self._meta.setdefault(database, {}).setdefault(collection, {})

    def _upsert(self, database: str, collection: str, node_id: str, text: str, node_type: str, name: str) -> None:
        self._get_corpus(database, collection)[node_id] = text
        self._get_meta(database, collection)[node_id] = {"type": node_type, "name": name}
        self._upsert_vector(database, collection, node_id, text, node_type, name)

    def _delete_bm25(self, database: str, collection: str, node_id: str) -> None:
        self._get_corpus(database, collection).pop(node_id, None)
        self._get_meta(database, collection).pop(node_id, None)

    # ------------------------------------------------------------------ #
    # Vector management                                                     #
    # ------------------------------------------------------------------ #

    def _vector_coll_name(self, database: str, collection: str) -> str:
        return f"kb_{collection}_{database}"

    def _ensure_vector_index(self, database: str, collection: str) -> None:
        if self._vector_source is None:
            return
        key = f"{database}:{collection}"
        if key in self._vector_indices_created:
            return
        coll_name = self._vector_coll_name(database, collection)
        try:
            self._vector_source.execute_query({
                "op": "VECTOR.CREATE_INDEX",
                "index": coll_name,
                "collection": coll_name,
                "dim": self._vector_dim,
                "distance_metric": self._distance_metric,
            })
        except Exception:
            pass
        self._vector_indices_created.add(key)

    def _upsert_vector(
        self, database: str, collection: str, node_id: str, text: str, node_type: str, name: str
    ) -> None:
        if self._vector_source is None or self._embedding_fn is None:
            return
        self._ensure_vector_index(database, collection)
        embedding = self._embedding_fn(text)
        self._vector_source.execute_query({
            "op": "VECTOR.UPSERT",
            "collection": self._vector_coll_name(database, collection),
            "id": node_id,
            "doc": {"embedding": embedding, "node_id": node_id, "type": node_type, "name": name},
        })

    def _delete_vector(self, database: str, collection: str, node_id: str) -> None:
        if self._vector_source is None:
            return
        try:
            self._vector_source.execute_query({
                "op": "VECTOR.DELETE",
                "collection": self._vector_coll_name(database, collection),
                "id": node_id,
            })
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Unified search dispatch                                              #
    # ------------------------------------------------------------------ #

    def _search(
        self,
        database: str,
        collection: str,
        query: str,
        k: int,
        mode: str,
        type_filter: str | list[str] | None,
    ) -> list[tuple[str, float]]:
        if mode == "semantic":
            return self._knn_search(database, collection, query, k, type_filter)
        return self._bm25_search(database, collection, query, k, type_filter)

    def _bm25_search(
        self,
        database: str,
        collection: str,
        query: str,
        k: int,
        type_filter: str | list[str] | None,
    ) -> list[tuple[str, float]]:
        try:
            from rank_bm25 import BM25Okapi
        except ImportError as exc:
            raise KBError(
                "BM25 search requires rank-bm25: pip install blue-data-kb-source[search]"
            ) from exc

        corpus = self._get_corpus(database, collection)
        meta   = self._get_meta(database, collection)
        if not corpus:
            return []

        type_set = _normalise_type_filter(type_filter)
        doc_ids = [
            nid for nid in corpus
            if not type_set or meta.get(nid, {}).get("type") in type_set
        ]
        if not doc_ids:
            return []

        tokenized = [corpus[nid].lower().split() for nid in doc_ids]
        bm25 = BM25Okapi(tokenized)
        scores = bm25.get_scores(query.lower().split())
        ranked = sorted(zip(doc_ids, scores.tolist()), key=lambda x: x[1], reverse=True)
        return [(nid, float(s)) for nid, s in ranked if s > 0][:k]

    def _knn_search(
        self,
        database: str,
        collection: str,
        query: str,
        k: int,
        type_filter: str | list[str] | None,
    ) -> list[tuple[str, float]]:
        if self._vector_source is None or self._embedding_fn is None:
            raise KBError("Semantic search requires vector_source and embedding_fn at init")
        self._ensure_vector_index(database, collection)
        query_embedding = self._embedding_fn(query)
        type_set = _normalise_type_filter(type_filter)
        fetch_k = k * 3 if type_set else k
        rows = self._vector_source.knn(
            index_name=self._vector_coll_name(database, collection),
            query_vector=query_embedding,
            k=fetch_k,
            return_fields=["node_id", "type", "name"],
        )
        if type_set:
            rows = [r for r in rows if r.get("type") in type_set]
        # MemoryVectorSource returns cosine distance (lower = better); convert to similarity.
        return [(r["node_id"], max(0.0, 1.0 - float(r.get("score", 1.0)))) for r in rows[:k]]


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _normalise_type_filter(type_filter: str | list[str] | None) -> set[str]:
    if not type_filter:
        return set()
    if isinstance(type_filter, str):
        return {type_filter.upper()}
    return {t.upper() for t in type_filter}


def _node_name(neighbors: list[dict]) -> str | None:
    return neighbors[0]["node"].get("properties", {}).get("name") if neighbors else None
