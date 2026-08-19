from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Literal

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import (
    CLASS,
    ENTITY,
    EXTRACTED_FROM,
    FACT,
    HAS_PROPERTY,
    HEAD,
    IS_INSTANCE_OF,
    IS_VALUE_OF,
    LITERAL,
    PROPERTY,
    RELATION,
    REPRESENTS,
    SOURCE,
    EXTRACTION,
    TAIL,
)
from blue_data_kb_source.checks import require_node_type, require_node_type_one_of
from blue_data_kb_source.knowledge.engine import QueryEngine
from blue_data_kb_source.knowledge.helpers import (
    detail_attrs,
    fetch_node_metadata,
    node_name,
    provenance_detail,
)
from blue_data_kb_source.timestamps import edge_stamped, stamped, touched
from blue_data_kb_source.types import (
    DataSearchResult,
    EnrichmentResult,
    KnowledgeError,
    ValidationResult,
)

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
        if class_id is not None:
            require_node_type(self._source, database, class_id, CLASS, "class_id", KnowledgeError)
        if source_id is not None:
            require_node_type(self._source, database, source_id, SOURCE, "source_id", KnowledgeError)
        node_id = self._source.add_node(
            database,
            ENTITY,
            stamped({"name": name, "description": description}),
            node_id=entity_id,
        )
        if class_id is not None:
            self._source.add_edge(database, node_id, IS_INSTANCE_OF, class_id, properties=edge_stamped())
        if text is not None:
            await self._provenance.link_extraction(database, node_id, text, source_id)
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
        if head_entity_id is not None:
            require_node_type(self._source, database, head_entity_id, ENTITY, "head_entity_id", KnowledgeError)
        if tail_entity_id is not None:
            require_node_type(self._source, database, tail_entity_id, ENTITY, "tail_entity_id", KnowledgeError)
        if relation_id is not None:
            require_node_type(self._source, database, relation_id, RELATION, "relation_id", KnowledgeError)
        if source_id is not None:
            require_node_type(self._source, database, source_id, SOURCE, "source_id", KnowledgeError)
        if name is None:
            head_name = self._node_name(database, head_entity_id) or "?"
            tail_name = self._node_name(database, tail_entity_id) or "?"
            rel_name = self._node_name(database, relation_id) or "?"
            name = f"{head_name}_{rel_name}_{tail_name}"
        node_id = self._source.add_node(
            database, FACT, stamped({"name": name, "description": description}), node_id=fact_id
        )
        if head_entity_id is not None:
            self._source.add_edge(database, node_id, HEAD, head_entity_id, properties=edge_stamped())
        if tail_entity_id is not None:
            self._source.add_edge(database, node_id, TAIL, tail_entity_id, properties=edge_stamped())
        if relation_id is not None:
            self._source.add_edge(database, node_id, REPRESENTS, relation_id, properties=edge_stamped())
        if text is not None:
            await self._provenance.link_extraction(database, node_id, text, source_id)
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
        if property_id is not None:
            require_node_type(self._source, database, property_id, PROPERTY, "property_id", KnowledgeError)
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
            stamped({"name": name, "description": description, "value": value, "type": literal_type}),
            node_id=node_id,
        )
        if property_id is not None:
            self._source.add_edge(database, lid, IS_VALUE_OF, property_id, properties=edge_stamped())
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
        if entity_id is not None:
            require_node_type(self._source, database, entity_id, ENTITY, "entity_id", KnowledgeError)
        if source_id is not None:
            require_node_type(self._source, database, source_id, SOURCE, "source_id", KnowledgeError)
        literal_id = await self.add_literal(
            database, value, name=name, description=description,
            literal_type=literal_type, property_id=property_id
        )
        if entity_id is not None:
            self._source.add_edge(database, entity_id, HAS_PROPERTY, literal_id, properties=edge_stamped())
        if text is not None:
            await self._provenance.link_extraction(database, literal_id, text, source_id)
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
        if fact_id is not None:
            require_node_type(self._source, database, fact_id, FACT, "fact_id", KnowledgeError)
        if source_id is not None:
            require_node_type(self._source, database, source_id, SOURCE, "source_id", KnowledgeError)
        literal_id = await self.add_literal(
            database, value, name=name, description=description,
            literal_type=literal_type, property_id=property_id
        )
        if fact_id is not None:
            self._source.add_edge(database, fact_id, HAS_PROPERTY, literal_id, properties=edge_stamped())
        if text is not None:
            await self._provenance.link_extraction(database, literal_id, text, source_id)
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

    async def add_entity_extraction(
        self,
        database: str,
        entity_id: str,
        text: str,
        source_id: str | None = None,
    ) -> str:
        """Link a new EXTRACTION built from *text* to an existing entity.

        Unlike add_entity with text=, this does not create or touch the
        entity's node — use it to record an additional text span for an
        entity that already exists, producing another EXTRACTED_FROM edge
        alongside any existing ones.
        """
        require_node_type(self._source, database, entity_id, ENTITY, "entity_id", KnowledgeError)
        if source_id is not None:
            require_node_type(self._source, database, source_id, SOURCE, "source_id", KnowledgeError)
        return await self._provenance.link_extraction(database, entity_id, text, source_id)

    async def add_fact_extraction(
        self,
        database: str,
        fact_id: str,
        text: str,
        source_id: str | None = None,
    ) -> str:
        """Link a new EXTRACTION built from *text* to an existing fact.

        Unlike add_fact with text=, this does not create or touch the fact's
        node — use it to record an additional text span for a fact that
        already exists, producing another EXTRACTED_FROM edge alongside any
        existing ones.
        """
        require_node_type(self._source, database, fact_id, FACT, "fact_id", KnowledgeError)
        if source_id is not None:
            require_node_type(self._source, database, source_id, SOURCE, "source_id", KnowledgeError)
        return await self._provenance.link_extraction(database, fact_id, text, source_id)

    async def add_entity_property_value_extraction(
        self,
        database: str,
        entity_id: str,
        property_id: str,
        text: str,
        source_id: str | None = None,
    ) -> str:
        """Link a new EXTRACTION built from *text* to an entity's existing property value."""
        require_node_type(self._source, database, entity_id, ENTITY, "entity_id", KnowledgeError)
        require_node_type(self._source, database, property_id, PROPERTY, "property_id", KnowledgeError)
        if source_id is not None:
            require_node_type(self._source, database, source_id, SOURCE, "source_id", KnowledgeError)
        lit_id = self._find_literal(database, entity_id, property_id)
        if lit_id is None:
            raise KnowledgeError("No property value found for the given entity and property.")
        return await self._provenance.link_extraction(database, lit_id, text, source_id)

    async def add_fact_property_value_extraction(
        self,
        database: str,
        fact_id: str,
        property_id: str,
        text: str,
        source_id: str | None = None,
    ) -> str:
        """Link a new EXTRACTION built from *text* to a fact's existing property value."""
        require_node_type(self._source, database, fact_id, FACT, "fact_id", KnowledgeError)
        require_node_type(self._source, database, property_id, PROPERTY, "property_id", KnowledgeError)
        if source_id is not None:
            require_node_type(self._source, database, source_id, SOURCE, "source_id", KnowledgeError)
        lit_id = self._find_literal(database, fact_id, property_id)
        if lit_id is None:
            raise KnowledgeError("No property value found for the given fact and property.")
        return await self._provenance.link_extraction(database, lit_id, text, source_id)

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
        require_node_type(self._source, database, entity_id, ENTITY, "entity_id", KnowledgeError)
        props = {k: v for k, v in {"name": name, "description": description}.items() if v is not None}
        if not props:
            raise KnowledgeError("At least one of --name or --description must be provided.")
        self._source.update_node(database, entity_id, touched(props))
        self._notifier.fire({"type": "entity_updated", "database": database, "id": entity_id})

    async def update_fact(
        self,
        database: str,
        fact_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        require_node_type(self._source, database, fact_id, FACT, "fact_id", KnowledgeError)
        props = {k: v for k, v in {"name": name, "description": description}.items() if v is not None}
        if not props:
            raise KnowledgeError("At least one of --name or --description must be provided.")
        self._source.update_node(database, fact_id, touched(props))
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
        require_node_type(self._source, database, entity_id, ENTITY, "entity_id", KnowledgeError)
        require_node_type(self._source, database, property_id, PROPERTY, "property_id", KnowledgeError)
        lit_id = self._find_literal(database, entity_id, property_id)
        if lit_id is None:
            raise KnowledgeError("No property value found for the given entity and property.")
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
            raise KnowledgeError("At least one of --value, --name, or --description must be provided.")
        self._source.update_node(database, lit_id, touched(props))
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
        require_node_type(self._source, database, fact_id, FACT, "fact_id", KnowledgeError)
        require_node_type(self._source, database, property_id, PROPERTY, "property_id", KnowledgeError)
        lit_id = self._find_literal(database, fact_id, property_id)
        if lit_id is None:
            raise KnowledgeError("No property value found for the given fact and property.")
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
            raise KnowledgeError("At least one of --value, --name, or --description must be provided.")
        self._source.update_node(database, lit_id, touched(props))
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
        require_node_type(self._source, database, entity_id, ENTITY, "entity_id", KnowledgeError)
        referencing = self._facts_referencing(database, entity_id)
        if referencing and not cascade:
            raise KnowledgeError(
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
        require_node_type(self._source, database, fact_id, FACT, "fact_id", KnowledgeError)
        self._delete_literals(database, fact_id)
        self._source.delete_node(database, fact_id)
        self._notifier.fire({"type": "fact_deleted", "database": database, "id": fact_id})

    async def delete_entity_property_value(
        self, database: str, entity_id: str, property_id: str
    ) -> None:
        require_node_type(self._source, database, entity_id, ENTITY, "entity_id", KnowledgeError)
        require_node_type(self._source, database, property_id, PROPERTY, "property_id", KnowledgeError)
        lit_id = self._find_literal(database, entity_id, property_id)
        if lit_id is None:
            raise KnowledgeError("No property value found for the given entity and property.")
        self._source.delete_node(database, lit_id)
        self._notifier.fire({"type": "entity_property_deleted", "database": database,
                             "id": lit_id, "entity_id": entity_id})

    async def delete_fact_property_value(
        self, database: str, fact_id: str, property_id: str
    ) -> None:
        require_node_type(self._source, database, fact_id, FACT, "fact_id", KnowledgeError)
        require_node_type(self._source, database, property_id, PROPERTY, "property_id", KnowledgeError)
        lit_id = self._find_literal(database, fact_id, property_id)
        if lit_id is None:
            raise KnowledgeError("No property value found for the given fact and property.")
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
        detailed: bool = False,
    ) -> dict | list:
        if compact:
            return self._fetch_compact(database, include_provenance, detailed)

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

    def _fetch_compact(self, database: str, include_provenance: bool, detailed: bool = False) -> list[dict]:
        results: list[dict] = []
        _name = node_name
        _detail_attrs = detail_attrs

        def _provenance_detail(node_id: str) -> dict:
            return provenance_detail(self._source, database, node_id)

        for entity_node in self._source.match_nodes(database, label=ENTITY):
            nid = entity_node["id"]
            for nb in self._source.get_neighbors(database, nid, edge_type=IS_INSTANCE_OF, direction="out"):
                entry: dict = {
                    "relation_type": IS_INSTANCE_OF,
                    "entity_name": _name(entity_node),
                    "class_name": _name(nb["node"]),
                }
                prov = _provenance_detail(nid) if (include_provenance or detailed) else None
                if detailed:
                    entry.update(_detail_attrs(entity_node))
                if include_provenance:
                    entry["extraction"] = prov["extraction"]
                    entry["source"] = prov["source"]
                    if detailed:
                        entry["source_original"] = prov["source_original"]
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
                prov = _provenance_detail(nid) if (include_provenance or detailed) else None
                if detailed:
                    entry.update(_detail_attrs(literal_node))
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
                    entry["extraction"] = prov["extraction"]
                    entry["source"] = prov["source"]
                    if detailed:
                        entry["source_original"] = prov["source_original"]
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
            prov = _provenance_detail(nid) if (include_provenance or detailed) else None
            if detailed:
                entry.update(_detail_attrs(fact_node))
            if include_provenance:
                entry["extraction"] = prov["extraction"]
                entry["source"] = prov["source"]
                if detailed:
                    entry["source_original"] = prov["source_original"]
            results.append(entry)

        return results

    # ------------------------------------------------------------------ #
    # Data export / import                                                 #
    # ------------------------------------------------------------------ #

    async def export_database_data(self, database: str, include_provenance: bool = False) -> dict:
        """Portable, name-based export of instance data (entities, facts,
        property values) — self-sufficient for `import_database_data`
        regardless of what ids the target database's schema ends up with,
        since `import_database_schema` declares by name and mints new ids
        rather than preserving the originals. Schema (CLASS/RELATION/
        PROPERTY) isn't included here — export/import it separately via
        `export_database_schema`/`import_database_schema` first.

        `ref`/`owner_ref`/`head_ref`/`tail_ref` are the *original* node ids,
        used only as in-payload join keys during import — entities aren't
        guaranteed unique by name (`add_entity` never dedups), so names
        alone can't safely re-link facts/properties to the right one.
        """
        def _node_name(node: dict) -> str | None:
            return node.get("properties", {}).get("name")

        def _first(nbs: list[dict]) -> dict | None:
            return nbs[0]["node"] if nbs else None

        entities: list[dict] = []
        for node in self._source.match_nodes(database, label=ENTITY):
            nid = node["id"]
            props = node.get("properties", {})
            cls = _first(self._source.get_neighbors(database, nid, edge_type=IS_INSTANCE_OF, direction="out"))
            entry = {
                "ref": nid,
                "name": props.get("name"),
                "description": props.get("description"),
                "class": _node_name(cls) if cls else None,
            }
            if include_provenance:
                entry["extraction"] = self._provenance_text(database, nid)
            entities.append(entry)

        facts: list[dict] = []
        for node in self._source.match_nodes(database, label=FACT):
            nid = node["id"]
            props = node.get("properties", {})
            rel  = _first(self._source.get_neighbors(database, nid, edge_type=REPRESENTS, direction="out"))
            head = _first(self._source.get_neighbors(database, nid, edge_type=HEAD, direction="out"))
            tail = _first(self._source.get_neighbors(database, nid, edge_type=TAIL, direction="out"))
            entry = {
                "ref": nid,
                "name": props.get("name"),
                "description": props.get("description"),
                "relation": _node_name(rel) if rel else None,
                "head_ref": head["id"] if head else None,
                "head_name": _node_name(head) if head else None,
                "tail_ref": tail["id"] if tail else None,
                "tail_name": _node_name(tail) if tail else None,
            }
            if include_provenance:
                entry["extraction"] = self._provenance_text(database, nid)
            facts.append(entry)

        entity_properties: list[dict] = []
        fact_properties: list[dict] = []
        for node in self._source.match_nodes(database, label=LITERAL):
            nid = node["id"]
            props = node.get("properties", {})
            prop = _first(self._source.get_neighbors(database, nid, edge_type=IS_VALUE_OF, direction="out"))
            owner = _first(self._source.get_neighbors(database, nid, edge_type=HAS_PROPERTY, direction="in"))
            if owner is None:
                continue  # orphaned literal (no owner) -- nothing to re-link on import
            entry = {
                "owner_ref": owner["id"],
                "name": _node_name(prop) if prop else None,
                "value": props.get("value"),
                "literal_type": props.get("type"),
            }
            if include_provenance:
                entry["extraction"] = self._provenance_text(database, nid)
            (entity_properties if owner.get("label") == ENTITY else fact_properties).append(entry)

        return {
            "entities": entities,
            "facts": facts,
            "entity_properties": entity_properties,
            "fact_properties": fact_properties,
        }

    def _resolve_schema_ref(self, database: str, label: str, name: str | None, what: str) -> str | None:
        if not name:
            return None
        nodes = self._source.match_nodes(database, label=label, properties={"name": name})
        if not nodes:
            raise KnowledgeError(f"import_database_data: {what} {name!r} not found in database {database!r}")
        return nodes[0]["id"]

    def _resolve_data_ref(self, ref_to_id: dict[str, str], ref: str | None, what: str) -> str | None:
        if ref is None:
            return None
        if ref not in ref_to_id:
            raise KnowledgeError(f"import_database_data: {what} references {ref!r}, which wasn't among the imported entities/facts")
        return ref_to_id[ref]

    async def import_database_data(self, database: str, data: dict) -> None:
        """Import entities/facts/property values previously produced by
        `export_database_data`. CLASS/RELATION/PROPERTY references are
        resolved by name against *database*'s current schema (e.g. already
        populated via `import_database_schema`) — raises `KnowledgeError` if
        a referenced schema element or `ref`/`owner_ref` join key can't be
        resolved, rather than silently dropping the link.
        """
        ref_to_id: dict[str, str] = {}

        for ent in data.get("entities", []):
            class_id = self._resolve_schema_ref(database, CLASS, ent.get("class"), "class")
            new_id = await self.add_entity(
                database, ent.get("name"), description=ent.get("description"),
                class_id=class_id, text=ent.get("extraction"),
            )
            if ent.get("ref") is not None:
                ref_to_id[ent["ref"]] = new_id

        for fact in data.get("facts", []):
            relation_id = self._resolve_schema_ref(database, RELATION, fact.get("relation"), "relation")
            head_id = self._resolve_data_ref(ref_to_id, fact.get("head_ref"), "fact head_ref")
            tail_id = self._resolve_data_ref(ref_to_id, fact.get("tail_ref"), "fact tail_ref")
            new_id = await self.add_fact(
                database, name=fact.get("name"), description=fact.get("description"),
                head_entity_id=head_id, tail_entity_id=tail_id, relation_id=relation_id,
                text=fact.get("extraction"),
            )
            if fact.get("ref") is not None:
                ref_to_id[fact["ref"]] = new_id

        for prop in data.get("entity_properties", []):
            owner_id = self._resolve_data_ref(ref_to_id, prop.get("owner_ref"), "entity property owner_ref")
            property_id = self._resolve_schema_ref(database, PROPERTY, prop.get("name"), "property")
            await self.add_entity_property_value(
                database, prop.get("value"), literal_type=prop.get("literal_type"),
                entity_id=owner_id, property_id=property_id, text=prop.get("extraction"),
            )

        for prop in data.get("fact_properties", []):
            owner_id = self._resolve_data_ref(ref_to_id, prop.get("owner_ref"), "fact property owner_ref")
            property_id = self._resolve_schema_ref(database, PROPERTY, prop.get("name"), "property")
            await self.add_fact_property_value(
                database, prop.get("value"), literal_type=prop.get("literal_type"),
                fact_id=owner_id, property_id=property_id, text=prop.get("extraction"),
            )

    # ------------------------------------------------------------------ #
    # Metadata lookup                                                       #
    # ------------------------------------------------------------------ #

    async def get_metadata(self, database: str, node_id: str) -> dict:
        """created/modified/extraction/source metadata for an entity, fact, or property value id."""
        require_node_type_one_of(self._source, database, node_id, (ENTITY, FACT, LITERAL), "node_id", KnowledgeError)
        return fetch_node_metadata(self._source, database, node_id)

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
        include_metadata: bool = False,
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, dict, Any], None] | None = None,
        max_turns: int = 25,
    ) -> list[dict] | str | tuple[list[dict] | str, dict]:
        if include_metadata:
            results, metadata = await self._query.answer(
                database, question, model, include_metadata=True,
                on_tool_call=on_tool_call, on_tool_result=on_tool_result, max_turns=max_turns,
            )
            if natural_language:
                text = await self._query.answer_as_text(question, results, model)
                if metadata:
                    text = f"{text}\n\n{await self._query.metadata_as_text(metadata, model)}"
                return text, metadata
            return results, metadata
        results = await self._query.answer(
            database, question, model,
            on_tool_call=on_tool_call, on_tool_result=on_tool_result, max_turns=max_turns,
        )
        if natural_language:
            return await self._query.answer_as_text(question, results, model)
        return results

    async def validate(
        self,
        database: str,
        statement: str,
        model: str = "gpt-4o",
        natural_language: bool = False,
        include_metadata: bool = False,
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, dict, Any], None] | None = None,
        max_turns: int = 25,
    ) -> ValidationResult | str | tuple[ValidationResult | str, dict]:
        if include_metadata:
            result, metadata = await self._query.validate(
                database, statement, model, include_metadata=True,
                on_tool_call=on_tool_call, on_tool_result=on_tool_result, max_turns=max_turns,
            )
            if natural_language:
                text = await self._query.validate_as_text(statement, result, model)
                if metadata:
                    text = f"{text}\n\n{await self._query.metadata_as_text(metadata, model)}"
                return text, metadata
            return result, metadata
        result = await self._query.validate(
            database, statement, model,
            on_tool_call=on_tool_call, on_tool_result=on_tool_result, max_turns=max_turns,
        )
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
        max_turns: int = 25,
    ) -> list[EnrichmentResult]:
        return await self._query.enrich(
            database, query, depth, model, on_tool_call, on_tool_result, max_turns=max_turns
        )
