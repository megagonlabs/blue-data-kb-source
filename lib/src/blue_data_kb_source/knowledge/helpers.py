"""Shared lookup and metadata helpers for ENTITY/FACT/LITERAL nodes.

Used by both `KnowledgeManager` (compact data listing, `get_metadata`, the exact-name
`find_entity`/`find_fact` lookups behind the extraction agent's tools) and `QueryEngine`
(the `get_metadata` LLM tool) so the per-type lookup logic lives in exactly one place.
"""

from __future__ import annotations

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import (
    ENTITY,
    EXTRACTED_FROM,
    FACT,
    HAS_PROPERTY,
    HEAD,
    IS_INSTANCE_OF,
    IS_VALUE_OF,
    LITERAL,
    REPRESENTS,
    TAIL,
)


def node_name(node: dict) -> str:
    return node.get("properties", {}).get("name", node["id"])


def detail_attrs(node: dict) -> dict:
    """description/created/modified for a node's own properties."""
    props = node.get("properties", {})
    return {
        "description": props.get("description"),
        "created": props.get("created"),
        "modified": props.get("modified"),
    }


def find_entity(source: GraphSource, database: str, name: str) -> dict | None:
    """First ENTITY node with exactly *name*, or None — entity names are not unique."""
    nodes = source.match_nodes(database, label=ENTITY, properties={"name": name})
    return nodes[0] if nodes else None


def find_fact(
    source: GraphSource,
    database: str,
    head_entity_id: str,
    relation_id: str,
    tail_entity_id: str,
) -> dict | None:
    """First FACT matching the exact directed (head, relation, tail) triple, or None."""
    for nb in source.get_neighbors(database, head_entity_id, edge_type=HEAD, direction="in"):
        fact_node = nb["node"]
        fact_id = fact_node["id"]
        tail_nbs = source.get_neighbors(database, fact_id, edge_type=TAIL, direction="out")
        rel_nbs = source.get_neighbors(database, fact_id, edge_type=REPRESENTS, direction="out")
        if (any(t["node"]["id"] == tail_entity_id for t in tail_nbs)
                and any(r["node"]["id"] == relation_id for r in rel_nbs)):
            return fact_node
    return None


def entity_classes(source: GraphSource, database: str, entity_id: str) -> list[dict]:
    """CLASS nodes *entity_id* is an instance of, via IS_INSTANCE_OF."""
    nbs = source.get_neighbors(database, entity_id, edge_type=IS_INSTANCE_OF, direction="out")
    return [nb["node"] for nb in nbs]


def fact_parts(source: GraphSource, database: str, fact_id: str) -> dict:
    """First HEAD / RELATION / TAIL nodes of *fact_id*, as {"head", "relation", "tail"}."""
    def _first(edge_type: str) -> dict | None:
        nbs = source.get_neighbors(database, fact_id, edge_type=edge_type, direction="out")
        return nbs[0]["node"] if nbs else None

    return {"head": _first(HEAD), "relation": _first(REPRESENTS), "tail": _first(TAIL)}


def property_values(source: GraphSource, database: str, owner_id: str) -> list[dict]:
    """LITERAL values with their PROPERTY nodes for an ENTITY or FACT owner."""
    results: list[dict] = []
    for nb in source.get_neighbors(database, owner_id, edge_type=HAS_PROPERTY, direction="out"):
        literal = nb["node"]
        prop_nbs = source.get_neighbors(
            database, literal["id"], edge_type=IS_VALUE_OF, direction="out"
        )
        results.append({"literal": literal, "property": prop_nbs[0]["node"] if prop_nbs else None})
    return results


def provenance_detail(source: GraphSource, database: str, node_id: str) -> dict:
    """Extraction text and linked source name/original for node_id's EXTRACTED_FROM chain."""
    ext_nbs = source.get_neighbors(database, node_id, edge_type=EXTRACTED_FROM, direction="out")
    texts: list[str] = []
    source_names: list[str] = []
    source_originals: list[str] = []
    seen_sources: set[str] = set()
    for nb in ext_nbs:
        extraction_node = nb["node"]
        text = extraction_node.get("properties", {}).get("text")
        if text:
            texts.append(text)
        for src_nb in source.get_neighbors(
            database, extraction_node["id"], edge_type=EXTRACTED_FROM, direction="out"
        ):
            source_node = src_nb["node"]
            if source_node["id"] in seen_sources:
                continue
            seen_sources.add(source_node["id"])
            sprops = source_node.get("properties", {})
            if sprops.get("name"):
                source_names.append(sprops["name"])
            if sprops.get("original"):
                source_originals.append(sprops["original"])
    return {
        "extraction": " | ".join(texts) or None,
        "source": " | ".join(source_names) or None,
        "source_original": " | ".join(source_originals) or None,
    }


def fetch_node_metadata(source: GraphSource, database: str, node_id: str) -> dict:
    """created/modified/extraction/source metadata for an ENTITY, FACT, or LITERAL (property value) node.

    Raises KeyError if node_id doesn't exist or isn't one of those three types.
    """
    node = source.get_node(database, node_id)
    if node is None:
        raise KeyError(f"node {node_id!r} does not exist in database {database!r}")
    label = node.get("label")
    if label not in (ENTITY, FACT, LITERAL):
        raise KeyError(f"node {node_id!r} is a {label} node; expected ENTITY, FACT, or LITERAL")

    identity: dict = {}
    if label == ENTITY:
        classes = entity_classes(source, database, node_id)
        identity = {
            "name": node_name(node),
            "class_name": node_name(classes[0]) if classes else None,
        }
    elif label == FACT:
        parts = fact_parts(source, database, node_id)
        identity = {
            "name": node_name(node),
            "relation_name": node_name(parts["relation"]) if parts["relation"] else None,
            "head_name": node_name(parts["head"]) if parts["head"] else None,
            "tail_name": node_name(parts["tail"]) if parts["tail"] else None,
        }
    else:  # LITERAL
        prop_nbs = source.get_neighbors(database, node_id, edge_type=IS_VALUE_OF, direction="out")
        owner_nbs = source.get_neighbors(database, node_id, edge_type=HAS_PROPERTY, direction="in")
        owner_node = owner_nbs[0]["node"] if owner_nbs else None
        identity = {
            "property_name": node_name(prop_nbs[0]["node"]) if prop_nbs else None,
            "value": node.get("properties", {}).get("value"),
            "owner_label": owner_node.get("label") if owner_node else None,
            "owner_name": node_name(owner_node) if owner_node else None,
        }

    return {
        "id": node_id,
        "type": label,
        **identity,
        **detail_attrs(node),
        **provenance_detail(source, database, node_id),
    }
