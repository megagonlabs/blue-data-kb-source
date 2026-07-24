from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Literal

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
    SOURCE,
    EXTRACTION,
    TAIL,
)
from blue_data_kb_source.knowledge.engine import QueryEngine
from blue_data_kb_source.types import DataSearchResult, EnrichmentResult, KBError, ValidationResult

if TYPE_CHECKING:
    from blue_data_kb_source.notification.notifier import ChangeNotifier
    from blue_data_kb_source.provenance.tracker import ProvenanceTracker
    from blue_data_kb_source.schema.manager import SchemaManager
    from blue_data_kb_source.search.indexer import KBSearchIndexer


class KnowledgeManager:
    """Manages instance-level nodes (ENTITY, FACT, LITERAL) and knowledge queries."""

    def __init__(
        self,
        source: GraphSource,
        provenance_tracker: ProvenanceTracker,
        change_notifier: ChangeNotifier,
        schema_manager: SchemaManager,
        search_indexer: KBSearchIndexer | None = None,
    ) -> None:
        self._source = source
        self._provenance = provenance_tracker
        self._notifier = change_notifier
        self._query = QueryEngine(source, schema_manager, search_indexer)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _node_name(self, database: str, node_id: str | None) -> str | None:
        if node_id is None:
            return None
        node = self._source.get_node(database, node_id)
        if node is None:
            return None
        return node.get("properties", {}).get("name")

    def _first_neighbor(
        self, database: str, node_id: str, edge_type: str, direction: str = "out"
    ) -> str | None:
        nbs = self._source.get_neighbors(database, node_id, edge_type=edge_type, direction=direction)
        return nbs[0]["node"]["id"] if nbs else None

    def _fact_context(self, database: str, fact_id: str | None) -> dict:
        if fact_id is None:
            return {
                "fact_name": None,
                "head_entity_id": None, "head_entity_name": None,
                "tail_entity_id": None, "tail_entity_name": None,
                "relation_id": None,   "relation_name": None,
            }
        fact_node = self._source.get_node(database, fact_id)
        head_id = self._first_neighbor(database, fact_id, HEAD)
        tail_id = self._first_neighbor(database, fact_id, TAIL)
        rel_id  = self._first_neighbor(database, fact_id, REPRESENTS)
        return {
            "fact_name":       (fact_node or {}).get("properties", {}).get("name"),
            "head_entity_id":  head_id,
            "head_entity_name": self._node_name(database, head_id),
            "tail_entity_id":  tail_id,
            "tail_entity_name": self._node_name(database, tail_id),
            "relation_id":     rel_id,
            "relation_name":   self._node_name(database, rel_id),
        }

    def _provenance_text(self, database: str, node_id: str) -> str | None:
        nbs = self._source.get_neighbors(database, node_id, edge_type=EXTRACTED_FROM, direction="out")
        texts = [nb["node"].get("properties", {}).get("text") for nb in nbs]
        return " | ".join(t for t in texts if t) or None

    # ------------------------------------------------------------------ #
    # Instance node builders                                               #
    # ------------------------------------------------------------------ #

    async def add_entity(
        self,
        database: str,
        name: str,
        description: str | None = None,
        entity_id: str | None = None,
        class_id: str | None = None,
        text: str | None = None,
        source_id: str | None = None,
    ) -> str:
        node_id = self._source.add_node(
            database,
            ENTITY,
            {"name": name, "description": description},
            node_id=entity_id,
        )
        if class_id is not None:
            self._source.add_edge(database, node_id, IS_INSTANCE_OF, class_id)
        if text is not None:
            await self._provenance.track(database, node_id, text, source_id)
        self._notifier.fire({
            "type": "entity_added",
            "database": database,
            "id": node_id,
            "name": name,
            "class_id": class_id,
            "class_name": self._node_name(database, class_id),
        })
        return node_id

    async def add_fact(
        self,
        database: str,
        name: str | None = None,
        description: str | None = None,
        fact_id: str | None = None,
        head_entity_id: str | None = None,
        tail_entity_id: str | None = None,
        relation_id: str | None = None,
        text: str | None = None,
        source_id: str | None = None,
    ) -> str:
        if name is None:
            head_name = self._node_name(database, head_entity_id) or "?"
            tail_name = self._node_name(database, tail_entity_id) or "?"
            rel_name = self._node_name(database, relation_id) or "?"
            name = f"{head_name}_{rel_name}_{tail_name}"
        node_id = self._source.add_node(
            database, FACT, {"name": name, "description": description}, node_id=fact_id
        )
        if head_entity_id is not None:
            self._source.add_edge(database, node_id, HEAD, head_entity_id)
        if tail_entity_id is not None:
            self._source.add_edge(database, node_id, TAIL, tail_entity_id)
        if relation_id is not None:
            self._source.add_edge(database, node_id, REPRESENTS, relation_id)
        if text is not None:
            await self._provenance.track(database, node_id, text, source_id)
        self._notifier.fire({
            "type": "fact_added",
            "database": database,
            "id": node_id,
            "name": name,
            "head_entity_id": head_entity_id,
            "tail_entity_id": tail_entity_id,
            "relation_id": relation_id,
            "head_entity_name": self._node_name(database, head_entity_id),
            "tail_entity_name": self._node_name(database, tail_entity_id),
            "relation_name": self._node_name(database, relation_id),
        })
        return node_id

    async def add_literal(
        self,
        database: str,
        value: str | int | float | bool,
        name: str | None = None,
        description: str | None = None,
        literal_type: Literal["NUMBER", "DATE", "BOOLEAN", "STRING"] | None = None,
        property_id: str | None = None,
        node_id: str | None = None,
    ) -> str:
        if name is None:
            name = str(value)
        if literal_type is None:
            # bool must be checked before int (bool is a subclass of int)
            if isinstance(value, bool):
                literal_type = "BOOLEAN"
            elif isinstance(value, (int, float)):
                literal_type = "NUMBER"
            else:
                literal_type = "STRING"
        lid = self._source.add_node(
            database,
            LITERAL,
            {"name": name, "description": description, "value": value, "type": literal_type},
            node_id=node_id,
        )
        if property_id is not None:
            self._source.add_edge(database, lid, IS_VALUE_OF, property_id)
        return lid

    async def add_entity_property_value(
        self,
        database: str,
        value: str | int | float | bool,
        name: str | None = None,
        description: str | None = None,
        literal_type: Literal["NUMBER", "DATE", "BOOLEAN", "STRING"] | None = None,
        entity_id: str | None = None,
        property_id: str | None = None,
        text: str | None = None,
        source_id: str | None = None,
    ) -> str:
        literal_id = await self.add_literal(
            database, value, name=name, description=description,
            literal_type=literal_type, property_id=property_id
        )
        if entity_id is not None:
            self._source.add_edge(database, entity_id, HAS_PROPERTY, literal_id)
        if text is not None:
            await self._provenance.track(database, literal_id, text, source_id)
        self._notifier.fire({
            "type": "entity_property_added",
            "database": database,
            "id": literal_id,
            "entity_id": entity_id,
            "entity_name": self._node_name(database, entity_id),
            "property_id": property_id,
            "property_name": self._node_name(database, property_id),
            "value": value,
        })
        return literal_id

    async def add_fact_property_value(
        self,
        database: str,
        value: str | int | float | bool,
        name: str | None = None,
        description: str | None = None,
        literal_type: Literal["NUMBER", "DATE", "BOOLEAN", "STRING"] | None = None,
        fact_id: str | None = None,
        property_id: str | None = None,
        text: str | None = None,
        source_id: str | None = None,
    ) -> str:
        literal_id = await self.add_literal(
            database, value, name=name, description=description,
            literal_type=literal_type, property_id=property_id
        )
        if fact_id is not None:
            self._source.add_edge(database, fact_id, HAS_PROPERTY, literal_id)
        if text is not None:
            await self._provenance.track(database, literal_id, text, source_id)
        self._notifier.fire({
            "type": "fact_property_added",
            "database": database,
            "id": literal_id,
            "fact_id": fact_id,
            "property_id": property_id,
            "value": value,
            "property_name": self._node_name(database, property_id),
            **self._fact_context(database, fact_id),
        })
        return literal_id

    async def add_evidence(
        self,
        database: str,
        element_id: str,
        text: str,
        source_id: str | None = None,
    ) -> str:
        """Attach a new EXTRACTION built from *text* to an existing entity/fact/literal.

        Unlike add_entity/add_fact with text=, this does not create or touch the
        element's node — use it to record additional evidence for something
        that already exists (e.g. found via a lookup during extraction),
        producing an additional EXTRACTED_FROM edge alongside any existing ones.
        """
        return await self._provenance.track(database, element_id, text, source_id)

    # ------------------------------------------------------------------ #
    # Update operations                                                    #
    # ------------------------------------------------------------------ #

    async def update_entity(
        self,
        database: str,
        entity_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        props = {k: v for k, v in {"name": name, "description": description}.items() if v is not None}
        if not props:
            raise KBError("At least one of --name or --description must be provided.")
        self._source.update_node(database, entity_id, props)
        self._notifier.fire({"type": "entity_updated", "database": database, "id": entity_id})

    async def update_fact(
        self,
        database: str,
        fact_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        props = {k: v for k, v in {"name": name, "description": description}.items() if v is not None}
        if not props:
            raise KBError("At least one of --name or --description must be provided.")
        self._source.update_node(database, fact_id, props)
        self._notifier.fire({"type": "fact_updated", "database": database, "id": fact_id})

    def _find_literal(self, database: str, owner_id: str, property_id: str) -> str | None:
        for lit_nb in self._source.get_neighbors(database, owner_id, edge_type=HAS_PROPERTY, direction="out"):
            lit_id = lit_nb["node"]["id"]
            for prop_nb in self._source.get_neighbors(database, lit_id, edge_type=IS_VALUE_OF, direction="out"):
                if prop_nb["node"]["id"] == property_id:
                    return lit_id
        return None

    async def update_entity_property_value(
        self,
        database: str,
        entity_id: str,
        property_id: str,
        value: str | int | float | bool | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        lit_id = self._find_literal(database, entity_id, property_id)
        if lit_id is None:
            raise KBError("No property value found for the given entity and property.")
        props: dict = {}
        if value is not None:
            props["value"] = value
            if name is None:
                props["name"] = str(value)
        if name is not None:
            props["name"] = name
        if description is not None:
            props["description"] = description
        if not props:
            raise KBError("At least one of --value, --name, or --description must be provided.")
        self._source.update_node(database, lit_id, props)
        self._notifier.fire({"type": "entity_property_updated", "database": database,
                             "id": lit_id, "entity_id": entity_id})

    async def update_fact_property_value(
        self,
        database: str,
        fact_id: str,
        property_id: str,
        value: str | int | float | bool | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        lit_id = self._find_literal(database, fact_id, property_id)
        if lit_id is None:
            raise KBError("No property value found for the given fact and property.")
        props: dict = {}
        if value is not None:
            props["value"] = value
            if name is None:
                props["name"] = str(value)
        if name is not None:
            props["name"] = name
        if description is not None:
            props["description"] = description
        if not props:
            raise KBError("At least one of --value, --name, or --description must be provided.")
        self._source.update_node(database, lit_id, props)
        self._notifier.fire({"type": "fact_property_updated", "database": database,
                             "id": lit_id, "fact_id": fact_id})

    # ------------------------------------------------------------------ #
    # Delete operations                                                    #
    # ------------------------------------------------------------------ #

    def _delete_literals(self, database: str, owner_id: str) -> None:
        """Delete all LITERAL nodes attached to owner via HAS_PROPERTY."""
        for nb in self._source.get_neighbors(database, owner_id, edge_type=HAS_PROPERTY, direction="out"):
            self._source.delete_node(database, nb["node"]["id"])

    def _facts_referencing(self, database: str, entity_id: str) -> list[str]:
        """Return IDs of all FACT nodes that reference entity_id as HEAD or TAIL."""
        fact_ids: list[str] = []
        for nb in self._source.get_neighbors(database, entity_id, edge_type=HEAD, direction="in"):
            fact_ids.append(nb["node"]["id"])
        for nb in self._source.get_neighbors(database, entity_id, edge_type=TAIL, direction="in"):
            fact_ids.append(nb["node"]["id"])
        return list(dict.fromkeys(fact_ids))  # deduplicate, preserve order

    async def delete_entity(self, database: str, entity_id: str, cascade: bool = False) -> None:
        referencing = self._facts_referencing(database, entity_id)
        if referencing and not cascade:
            raise KBError(
                f"Entity is referenced by {len(referencing)} fact(s). "
                "Use cascade=True to also delete them."
            )
        for fact_id in referencing:
            self._delete_literals(database, fact_id)
            self._source.delete_node(database, fact_id)
            self._notifier.fire({"type": "fact_deleted", "database": database, "id": fact_id})
        self._delete_literals(database, entity_id)
        self._source.delete_node(database, entity_id)
        self._notifier.fire({"type": "entity_deleted", "database": database, "id": entity_id})

    async def delete_fact(self, database: str, fact_id: str) -> None:
        self._delete_literals(database, fact_id)
        self._source.delete_node(database, fact_id)
        self._notifier.fire({"type": "fact_deleted", "database": database, "id": fact_id})

    async def delete_entity_property_value(
        self, database: str, entity_id: str, property_id: str
    ) -> None:
        lit_id = self._find_literal(database, entity_id, property_id)
        if lit_id is None:
            raise KBError("No property value found for the given entity and property.")
        self._source.delete_node(database, lit_id)
        self._notifier.fire({"type": "entity_property_deleted", "database": database,
                             "id": lit_id, "entity_id": entity_id})

    async def delete_fact_property_value(
        self, database: str, fact_id: str, property_id: str
    ) -> None:
        lit_id = self._find_literal(database, fact_id, property_id)
        if lit_id is None:
            raise KBError("No property value found for the given fact and property.")
        self._source.delete_node(database, lit_id)
        self._notifier.fire({"type": "fact_property_deleted", "database": database,
                             "id": lit_id, "fact_id": fact_id})

    # ------------------------------------------------------------------ #
    # Data export                                                          #
    # ------------------------------------------------------------------ #

    async def fetch_database_data(
        self,
        database: str,
        include_provenance: bool = False,
        compact: bool = False,
    ) -> dict | list:
        if compact:
            return self._fetch_compact(database, include_provenance)

        nodes: list[dict] = []
        edges: list[dict] = []

        def _edge(type_: str, src: str, tgt: str) -> dict:
            return {"type": type_, "source_id": src, "target_id": tgt}

        for node in self._source.match_nodes(database, label=ENTITY):
            nid = node["id"]
            nodes.append({"id": nid, "label": ENTITY, "properties": node.get("properties", {})})
            for nb in self._source.get_neighbors(database, nid, edge_type=IS_INSTANCE_OF, direction="out"):
                edges.append(_edge(IS_INSTANCE_OF, nid, nb["node"]["id"]))
            for nb in self._source.get_neighbors(database, nid, edge_type=HAS_PROPERTY, direction="out"):
                edges.append(_edge(HAS_PROPERTY, nid, nb["node"]["id"]))
            if include_provenance:
                for nb in self._source.get_neighbors(database, nid, edge_type=EXTRACTED_FROM, direction="out"):
                    edges.append(_edge(EXTRACTED_FROM, nid, nb["node"]["id"]))

        for node in self._source.match_nodes(database, label=FACT):
            nid = node["id"]
            nodes.append({"id": nid, "label": FACT, "properties": node.get("properties", {})})
            for nb in self._source.get_neighbors(database, nid, edge_type=REPRESENTS, direction="out"):
                edges.append(_edge(REPRESENTS, nid, nb["node"]["id"]))
            for nb in self._source.get_neighbors(database, nid, edge_type=HEAD, direction="out"):
                edges.append(_edge(HEAD, nid, nb["node"]["id"]))
            for nb in self._source.get_neighbors(database, nid, edge_type=TAIL, direction="out"):
                edges.append(_edge(TAIL, nid, nb["node"]["id"]))
            for nb in self._source.get_neighbors(database, nid, edge_type=HAS_PROPERTY, direction="out"):
                edges.append(_edge(HAS_PROPERTY, nid, nb["node"]["id"]))
            if include_provenance:
                for nb in self._source.get_neighbors(database, nid, edge_type=EXTRACTED_FROM, direction="out"):
                    edges.append(_edge(EXTRACTED_FROM, nid, nb["node"]["id"]))

        for node in self._source.match_nodes(database, label=LITERAL):
            nid = node["id"]
            nodes.append({"id": nid, "label": LITERAL, "properties": node.get("properties", {})})
            for nb in self._source.get_neighbors(database, nid, edge_type=IS_VALUE_OF, direction="out"):
                edges.append(_edge(IS_VALUE_OF, nid, nb["node"]["id"]))
            if include_provenance:
                for nb in self._source.get_neighbors(database, nid, edge_type=EXTRACTED_FROM, direction="out"):
                    edges.append(_edge(EXTRACTED_FROM, nid, nb["node"]["id"]))

        if include_provenance:
            for node in self._source.match_nodes(database, label=SOURCE):
                nodes.append({"id": node["id"], "label": SOURCE, "properties": node.get("properties", {})})
            for node in self._source.match_nodes(database, label=EXTRACTION):
                nodes.append({"id": node["id"], "label": EXTRACTION, "properties": node.get("properties", {})})

        return {"nodes": nodes, "edges": edges}

    def _fetch_compact(self, database: str, include_provenance: bool) -> list[dict]:
        results: list[dict] = []

        def _name(node: dict) -> str:
            return node.get("properties", {}).get("name", node["id"])

        for entity_node in self._source.match_nodes(database, label=ENTITY):
            nid = entity_node["id"]
            for nb in self._source.get_neighbors(database, nid, edge_type=IS_INSTANCE_OF, direction="out"):
                entry: dict = {
                    "relation_type": IS_INSTANCE_OF,
                    "entity_name": _name(entity_node),
                    "class_name": _name(nb["node"]),
                }
                if include_provenance:
                    entry["extraction"] = self._provenance_text(database, nid)
                results.append(entry)

        for literal_node in self._source.match_nodes(database, label=LITERAL):
            nid = literal_node["id"]
            owner_name: str | None = None
            owner_label: str | None = None
            owner_id: str | None = None
            for owner_nb in self._source.get_neighbors(database, nid, edge_type=HAS_PROPERTY, direction="in"):
                owner_node  = owner_nb["node"]
                owner_name  = _name(owner_node)
                owner_label = owner_node.get("label")
                owner_id    = owner_node.get("id")
                break
            for nb in self._source.get_neighbors(database, nid, edge_type=IS_VALUE_OF, direction="out"):
                entry = {
                    "relation_type": IS_VALUE_OF,
                    "owner_label":   owner_label,
                    "entity_name":   owner_name,
                    "property_value": _name(literal_node),
                    "property_name":  _name(nb["node"]),
                }
                if owner_label == FACT and owner_id:
                    for h in self._source.get_neighbors(database, owner_id, edge_type=HEAD, direction="out"):
                        entry["head_name"] = _name(h["node"])
                        break
                    for t in self._source.get_neighbors(database, owner_id, edge_type=TAIL, direction="out"):
                        entry["tail_name"] = _name(t["node"])
                        break
                    for r in self._source.get_neighbors(database, owner_id, edge_type=REPRESENTS, direction="out"):
                        entry["relation_name"] = _name(r["node"])
                        break
                if include_provenance:
                    entry["extraction"] = self._provenance_text(database, nid)
                results.append(entry)

        for fact_node in self._source.match_nodes(database, label=FACT):
            nid = fact_node["id"]
            relation_name: str | None = None
            head_name: str | None = None
            tail_name: str | None = None
            for nb in self._source.get_neighbors(database, nid, edge_type=REPRESENTS, direction="out"):
                relation_name = _name(nb["node"])
                break
            for nb in self._source.get_neighbors(database, nid, edge_type=HEAD, direction="out"):
                head_name = _name(nb["node"])
                break
            for nb in self._source.get_neighbors(database, nid, edge_type=TAIL, direction="out"):
                tail_name = _name(nb["node"])
                break
            entry = {
                "relation_type": REPRESENTS,
                "entity_name": _name(fact_node),
                "source_name": head_name,
                "target_name": tail_name,
                "relation_name": relation_name,
            }
            if include_provenance:
                entry["extraction"] = self._provenance_text(database, nid)
            results.append(entry)

        return results

    # ------------------------------------------------------------------ #
    # Find (substring scan)                                                #
    # ------------------------------------------------------------------ #

    async def find_data(
        self,
        database: str,
        query: str,
        type_filter: str | list[str] | None = None,
    ) -> list[DataSearchResult]:
        """Return entities/facts whose name, description, or related names contain *query*."""
        all_labels = (ENTITY, FACT)
        if type_filter is None:
            labels: tuple[str, ...] = all_labels
        else:
            requested = {type_filter.upper()} if isinstance(type_filter, str) else {t.upper() for t in type_filter}
            labels = tuple(lbl for lbl in all_labels if lbl in requested)
        q = query.lower()
        results: list[DataSearchResult] = []

        if ENTITY in labels:
            for node in self._source.match_nodes(database, label=ENTITY):
                props = node.get("properties", {})
                name = props.get("name", "")
                desc = props.get("description", "") or ""
                if q in name.lower() or q in desc.lower():
                    cls_nbs = self._source.get_neighbors(database, node["id"], edge_type=IS_INSTANCE_OF, direction="out")
                    class_name = cls_nbs[0]["node"].get("properties", {}).get("name") if cls_nbs else None
                    results.append(DataSearchResult(
                        id=node["id"], type=ENTITY, name=name, description=desc or None,
                        class_name=class_name,
                    ))

        if FACT in labels:
            for node in self._source.match_nodes(database, label=FACT):
                props = node.get("properties", {})
                name = props.get("name", "")
                desc = props.get("description", "") or ""
                rel_nbs  = self._source.get_neighbors(database, node["id"], edge_type=REPRESENTS, direction="out")
                head_nbs = self._source.get_neighbors(database, node["id"], edge_type=HEAD, direction="out")
                tail_nbs = self._source.get_neighbors(database, node["id"], edge_type=TAIL, direction="out")
                head_name = head_nbs[0]["node"].get("properties", {}).get("name") if head_nbs else None
                rel_name  = rel_nbs[0]["node"].get("properties", {}).get("name") if rel_nbs else None
                tail_name = tail_nbs[0]["node"].get("properties", {}).get("name") if tail_nbs else None
                searchable = " ".join(s for s in [name, desc, head_name, rel_name, tail_name] if s).lower()
                if q in searchable:
                    results.append(DataSearchResult(
                        id=node["id"], type=FACT, name=name, description=desc or None,
                        head_name=head_name, relation_name=rel_name, tail_name=tail_name,
                    ))

        return results

    # ------------------------------------------------------------------ #
    # NL queries                                                           #
    # ------------------------------------------------------------------ #

    async def answer(
        self,
        database: str,
        question: str,
        model: str = "gpt-4o",
        natural_language: bool = False,
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, dict, Any], None] | None = None,
    ) -> list[dict] | str:
        results = await self._query.answer(database, question, model, on_tool_call, on_tool_result)
        if natural_language:
            return await self._query.answer_as_text(question, results, model)
        return results

    async def validate(
        self,
        database: str,
        statement: str,
        model: str = "gpt-4o",
        natural_language: bool = False,
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, dict, Any], None] | None = None,
    ) -> ValidationResult | str:
        result = await self._query.validate(database, statement, model, on_tool_call, on_tool_result)
        if natural_language:
            return await self._query.validate_as_text(statement, result, model)
        return result

    async def enrich(
        self,
        database: str,
        query: str,
        depth: int = 2,
        model: str = "gpt-4o",
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, dict, Any], None] | None = None,
    ) -> list[EnrichmentResult]:
        return await self._query.enrich(database, query, depth, model, on_tool_call, on_tool_result)
