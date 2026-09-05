"""Shared lookup, metadata, and derived-value helpers for ENTITY/FACT/LITERAL nodes.

Used by `KnowledgeManager` (compact data listing, `get_metadata`, `evidence_records`,
the exact-name `find_entity`/`find_fact` lookups behind the extraction agent's tools),
`IngestionManager` (resolution and dedupe reads),
`QueryEngine` (the `get_metadata` LLM tool), and `ProvenanceTracker`
(`recompute_fact_confidence`, which must run wherever an EXTRACTED_FROM edge is written)
so the per-type logic lives in exactly one place.
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
from blue_data_kb_source.timestamps import touched
from blue_data_kb_source.types import KnowledgeError


def cast_literal_value(value, literal_type: str):
    """Cast *value* to the Python type `literal_type` implies, tolerating an
    already-typed input (idempotent when *value* already matches).

    Callers that only ever see raw text — the CLI, the MCP server, the
    extraction agent's tool calls — pass `value` as a string alongside an
    explicit `literal_type`; without this, that string was stored verbatim
    instead of the NUMBER/BOOLEAN it names. STRING and DATE values are
    normalized to `str` but otherwise left alone. Raises KnowledgeError if a
    NUMBER or BOOLEAN value can't be parsed as such.
    """
    if literal_type == "NUMBER":
        if isinstance(value, bool):
            raise KnowledgeError(f"value {value!r} is not a valid NUMBER")
        if isinstance(value, (int, float)):
            return value
        text = str(value).strip()
        for cast in (int, float):
            try:
                return cast(text)
            except ValueError:
                continue
        raise KnowledgeError(f"value {value!r} is not a valid NUMBER")
    if literal_type == "BOOLEAN":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "1"):
            return True
        if text in ("false", "0"):
            return False
        raise KnowledgeError(f"value {value!r} is not a valid BOOLEAN")
    if literal_type in ("STRING", "DATE"):
        return value if isinstance(value, str) else str(value)
    return value


def node_name(node: dict) -> str:
    return node.get("properties", {}).get("name", node["id"])


def detail_attrs(node: dict) -> dict:
    """description/created/modified/confidence for a node's own properties."""
    props = node.get("properties", {})
    return {
        "description": props.get("description"),
        "created": props.get("created"),
        "modified": props.get("modified"),
        "confidence": props.get("confidence"),
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


def find_literal(
    source: GraphSource, database: str, owner_id: str, property_id: str
) -> str | None:
    """The owner's existing LITERAL for *property_id*, or None."""
    for pv in property_values(source, database, owner_id):
        prop = pv["property"]
        if prop is not None and prop["id"] == property_id:
            return pv["literal"]["id"]
    return None


def evidence_records(source: GraphSource, database: str, node_id: str) -> list[dict]:
    """One record per EXTRACTION on node_id, each with its own score, date, and source."""
    records: list[dict] = []
    for nb in source.get_neighbors(database, node_id, edge_type=EXTRACTED_FROM, direction="out"):
        extraction_node = nb["node"]
        eprops = extraction_node.get("properties", {})
        src_nbs = source.get_neighbors(
            database, extraction_node["id"], edge_type=EXTRACTED_FROM, direction="out"
        )
        own_source = src_nbs[0]["node"].get("properties", {}) if src_nbs else {}
        records.append({
            "text": eprops.get("text"),
            "confidence": eprops.get("confidence"),
            "created": eprops.get("created"),
            "source": own_source.get("name"),
            "source_original": own_source.get("original"),
        })
    return records


def fetch_node_metadata(source: GraphSource, database: str, node_id: str) -> dict:
    """created/modified, derived confidence, and `evidence` records for an ENTITY, FACT, or LITERAL (property value) node.

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
        "evidence": evidence_records(source, database, node_id),
    }


def recompute_fact_confidence(source: GraphSource, database: str, fact_id: str) -> None:
    """Refresh a FACT's derived `confidence` from its supporting EXTRACTION nodes.

    The highest confidence among them, ignoring those that state none, and `None`
    when none do. A no-op for non-FACT nodes; writes nothing when unchanged.
    """
    node = source.get_node(database, fact_id)
    if node is None or node.get("label") != FACT:
        return
    scores: list[float] = []
    for nb in source.get_neighbors(database, fact_id, edge_type=EXTRACTED_FROM, direction="out"):
        score = nb["node"].get("properties", {}).get("confidence")
        if score is not None:
            scores.append(score)
    recomputed = max(scores) if scores else None
    if node.get("properties", {}).get("confidence") == recomputed:
        return
    source.update_node(database, fact_id, touched({"confidence": recomputed}))
