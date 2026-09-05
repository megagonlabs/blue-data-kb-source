from __future__ import annotations

from blue_data.core.schema import DataSchema
from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import (
    CLASS,
    DEFINE_PROPERTY,
    DOMAIN,
    ENTITY,
    EXTRACTED_FROM,
    EXTRACTION,
    FACT,
    HAS_PROPERTY,
    HEAD,
    IS_INSTANCE_OF,
    IS_SUBCLASS_OF,
    IS_VALUE_OF,
    LITERAL,
    PROPERTY,
    RANGE,
    RELATION,
    REPRESENTS,
    SOURCE,
    TAIL,
)
from blue_data_kb_source.checks import require_node_type, require_node_type_one_of
from blue_data_kb_source.schema.expansion import SchemaExpansionEngine
from blue_data_kb_source.timestamps import edge_stamped, stamped, touched
from blue_data_kb_source.types import SchemaError, SchemaSearchResult

from typing import TYPE_CHECKING, Any, Callable, Literal

if TYPE_CHECKING:
    from blue_data_kb_source.notification.notifier import ChangeNotifier
    from blue_data_kb_source.search.indexer import KBSearchIndexer


class SchemaManager:
    """Manages schema-level nodes (CLASS, RELATION, PROPERTY) and schema-level edges."""

    def __init__(
        self,
        source: GraphSource,
        notifier: "ChangeNotifier | None" = None,
        search_indexer: "KBSearchIndexer | None" = None,
    ) -> None:
        self._source = source
        self._notifier = notifier
        self._expansion = SchemaExpansionEngine(source, self, search_indexer)

    def _fire(self, event: dict) -> None:
        if self._notifier is not None:
            self._notifier.fire(event)

    async def fetch_meta_schema(self) -> DataSchema:
        """Return a DataSchema describing the KB primitive types and relations."""
        schema = DataSchema()

        _classes: dict[str, str] = {
            CLASS:      "Defines a category or type. Example: PERSON, COMPANY",
            RELATION:   ("Defines the nature of a relationship between two entity classes. "
                         "Example: WORKED_AT, LIVES_IN, PURCHASED"),
            PROPERTY:   "Feature of an entity, class, relation, or fact. Example: AGE, START_DATE",
            ENTITY:     "A specific instance of a class. Example: Alice, IBM",
            FACT:       "Represents a specific factual relation between entities. Example: Alice_WORKED_AT_IBM",
            LITERAL:    "Specifies a property value. Example: 18, 1998",
            SOURCE:     "A source of data.",
            EXTRACTION: "Extraction from a source.",
        }
        for name, desc in _classes.items():
            schema.add_entity(name, description=desc)

        _created_desc = "timestamp when the node was created"
        _modified_desc = "timestamp when the node's attributes were last modified"
        _timestamp_attrs = [("created", _created_desc), ("modified", _modified_desc)]

        _attrs: dict[str, list[tuple[str, str]]] = {
            CLASS:      [("name", "name of the class"), ("description", "description of the class"),
                         *_timestamp_attrs],
            RELATION:   [("name", "name of the relation"), ("description", "description of the relation"),
                         ("is_symmetric", "boolean indicating symmetry"), *_timestamp_attrs],
            PROPERTY:   [("name", "name of the property"), ("description", "description of the property"),
                         *_timestamp_attrs],
            ENTITY:     [("name", "name of the entity"), ("description", "description of the entity"),
                         *_timestamp_attrs],
            FACT:       [("name", "name of the fact"), ("description", "description of the fact"),
                         *_timestamp_attrs],
            LITERAL:    [("name", "name of the literal"), ("description", "description of the literal"),
                         ("value", "value of the property"), ("type", "type of the value"),
                         *_timestamp_attrs],
            SOURCE:     [("id", "id of the source"), ("name", "name of the source"),
                         ("description", "description of the source"),
                         ("original", "externally supplied date indicating the source's original date, "
                                      "set via the API or CLI"),
                         *_timestamp_attrs],
            EXTRACTION: [("name", "name of the extraction"), ("description", "description of the extraction"),
                         ("text", "text segment of extraction from source"), *_timestamp_attrs],
        }
        for class_name, attrs in _attrs.items():
            for attr_name, attr_desc in attrs:
                schema.add_entity_attribute(class_name, attr_name, "str", description=attr_desc)

        _subclass_desc = (
            "Defines hierarchical inheritance between two schema elements. "
            "The child inherits properties and semantics from the parent. "
            "Example: EMPLOYEE IS_SUBCLASS_OF PERSON"
        )
        _define_prop_desc = (
            "Declares that a class or relation has a given property in its schema definition. "
            "Example: PERSON DEFINE_PROPERTY AGE"
        )
        _extracted_desc = (
            "Links any schema element (entity, fact, or literal) to its originating extraction "
            "record or source evidence. Example: Alice WORKED_AT IBM EXTRACTED_FROM Resume_Text_1. "
            "An element may have multiple EXTRACTED_FROM edges: as further evidence is found in "
            "new extractions or sources, additional edges accumulate rather than replacing prior ones."
        )
        _relations: list[tuple[str, str, str, str]] = [
            (CLASS,     IS_SUBCLASS_OF,  CLASS,      _subclass_desc),
            (RELATION,  IS_SUBCLASS_OF,  RELATION,   _subclass_desc),
            (ENTITY,    IS_INSTANCE_OF,  CLASS,      "Connects a specific entity to a class, asserting that the entity is a concrete realization of that class. Example: IBM IS_INSTANCE_OF COMPANY"),
            (CLASS,     DEFINE_PROPERTY, PROPERTY,   _define_prop_desc),
            (RELATION,  DEFINE_PROPERTY, PROPERTY,   _define_prop_desc),
            (LITERAL,   IS_VALUE_OF,     PROPERTY,   "Links a literal value to the property it instantiates. Example: 18 IS_VALUE_OF AGE"),
            (ENTITY,    HAS_PROPERTY,    LITERAL,    "Assigns a literal value or attribute to an entity or fact instance. Example: Alice HAS_PROPERTY 28"),
            (FACT,      HAS_PROPERTY,    LITERAL,    "Assigns a literal value or attribute to an entity or fact instance. Example: Alice HAS_PROPERTY 28"),
            (FACT,      REPRESENTS,      RELATION,   "Connects a fact instance to the relation type it instantiates, grounding the fact in schema semantics. Example: Alice_WORKED_AT_IBM REPRESENTS WORKED_AT"),
            (FACT,      HEAD,            ENTITY,     "Specifies the subject (from) entity of a fact. Example: FACT → Alice"),
            (FACT,      TAIL,            ENTITY,     "Specifies the object (to) entity of a fact. Example: FACT → IBM"),
            (RELATION,  DOMAIN,          CLASS,      "Declares the valid source class of a relation (what type of entity can appear in HEAD position). Example: WORKED_AT DOMAIN EMPLOYEE"),
            (RELATION,  RANGE,           CLASS,      "Declares the valid target class of a relation (what type of entity can appear in TAIL position). Example: WORKED_AT RANGE ORGANIZATION"),
            (ENTITY,    EXTRACTED_FROM,  EXTRACTION, _extracted_desc),
            (LITERAL,   EXTRACTED_FROM,  EXTRACTION, _extracted_desc),
            (FACT,      EXTRACTED_FROM,  EXTRACTION, _extracted_desc),
            (EXTRACTION, EXTRACTED_FROM, SOURCE,     "Links an extraction record to the source it was drawn from, "
                                                      "continuing the evidence chain from entity/fact/literal down to "
                                                      "origin. Example: Resume_Text_1 EXTRACTED_FROM Alices_Resume. "
                                                      "Wired by add_extraction whenever source_id is given."),
        ]
        for domain, rel_name, range_, desc in _relations:
            rel_key = schema.add_relation(domain, rel_name, range_, description=desc)
            schema.add_relation_attribute(rel_key, "created", "str", description=_created_desc)

        return schema

    # ------------------------------------------------------------------ #
    # Declarations                                                         #
    # ------------------------------------------------------------------ #

    async def declare_class(
        self,
        database: str,
        name: str,
        description: str,
        class_id: str | None = None,
    ) -> str:
        node = await self.find_class(database, name)
        if node:
            return node["id"]
        node_id = self._source.add_node(
            database,
            CLASS,
            stamped({"name": name, "description": description}),
            node_id=class_id,
        )
        self._fire({"type": "class_declared", "database": database, "id": node_id,
                    "name": name, "description": description})
        return node_id

    async def declare_relation_type(
        self,
        database: str,
        name: str,
        description: str,
        is_symmetric: bool = False,
        relation_id: str | None = None,
        *,
        domain: str | list[str],
        range: str | list[str],
    ) -> str:
        domain_names: list[str] = [domain] if isinstance(domain, str) else list(domain or [])
        range_names: list[str] = [range] if isinstance(range, str) else list(range or [])
        if not domain_names:
            raise SchemaError(
                "domain is required when declaring a relation type: pass the name of at least one "
                "existing CLASS (or a list of names). A relation cannot be declared without a domain."
            )
        if not range_names:
            raise SchemaError(
                "range is required when declaring a relation type: pass the name of at least one "
                "existing CLASS (or a list of names). A relation cannot be declared without a range."
            )
        node = await self.find_relation_type(database, name)
        if node:
            return node["id"]
        # Resolve every domain/range class *before* creating the RELATION node, so a
        # missing class raises without leaving a half-wired (domain-less) node behind —
        # a later retry with a valid domain would otherwise hit the idempotency check
        # above and silently reuse that broken node instead of wiring it.
        domain_ids: list[str] = []
        for class_name in domain_names:
            cls = await self.find_class(database, class_name)
            if cls is None:
                raise SchemaError(f"domain class '{class_name}' not found in '{database}'")
            domain_ids.append(cls["id"])
        range_ids: list[str] = []
        for class_name in range_names:
            cls = await self.find_class(database, class_name)
            if cls is None:
                raise SchemaError(f"range class '{class_name}' not found in '{database}'")
            range_ids.append(cls["id"])
        rid = self._source.add_node(
            database,
            RELATION,
            stamped({"name": name, "description": description, "is_symmetric": is_symmetric}),
            node_id=relation_id,
        )
        for class_id in domain_ids:
            self._source.add_edge(database, rid, DOMAIN, class_id, properties=edge_stamped())
        for class_id in range_ids:
            self._source.add_edge(database, rid, RANGE, class_id, properties=edge_stamped())
        self._fire({"type": "relation_declared", "database": database, "id": rid,
                    "name": name, "description": description, "is_symmetric": is_symmetric,
                    "domain": domain_names, "range": range_names})
        return rid

    async def declare_class_property(
        self,
        database: str,
        name: str,
        description: str,
        property_id: str | None = None,
        class_id: str | None = None,
    ) -> str:
        if class_id is not None:
            require_node_type(self._source, database, class_id, CLASS, "class_id", SchemaError)
            for nb in self._source.get_neighbors(database, class_id, edge_type=DEFINE_PROPERTY, direction="out"):
                if nb["node"].get("properties", {}).get("name") == name:
                    return nb["node"]["id"]
        else:
            node = await self.find_property(database, name)
            if node:
                return node["id"]
        node_id = self._source.add_node(
            database,
            PROPERTY,
            stamped({"name": name, "description": description}),
            node_id=property_id,
        )
        if class_id is not None:
            self._source.add_edge(database, class_id, DEFINE_PROPERTY, node_id, properties=edge_stamped())
        class_name = (
            (self._source.get_node(database, class_id) or {}).get("properties", {}).get("name")
            if class_id is not None else None
        )
        self._fire({"type": "class_property_declared", "database": database, "id": node_id,
                    "name": name, "description": description,
                    "class_id": class_id, "class_name": class_name})
        return node_id

    async def declare_relation_property(
        self,
        database: str,
        name: str,
        description: str,
        property_id: str | None = None,
        relation_id: str | None = None,
    ) -> str:
        if relation_id is not None:
            require_node_type(self._source, database, relation_id, RELATION, "relation_id", SchemaError)
            for nb in self._source.get_neighbors(database, relation_id, edge_type=DEFINE_PROPERTY, direction="out"):
                if nb["node"].get("properties", {}).get("name") == name:
                    return nb["node"]["id"]
        else:
            node = await self.find_property(database, name)
            if node:
                return node["id"]
        node_id = self._source.add_node(
            database,
            PROPERTY,
            stamped({"name": name, "description": description}),
            node_id=property_id,
        )
        if relation_id is not None:
            self._source.add_edge(database, relation_id, DEFINE_PROPERTY, node_id, properties=edge_stamped())
        relation_name = (
            (self._source.get_node(database, relation_id) or {}).get("properties", {}).get("name")
            if relation_id is not None else None
        )
        self._fire({"type": "relation_property_declared", "database": database, "id": node_id,
                    "name": name, "description": description,
                    "relation_id": relation_id, "relation_name": relation_name})
        return node_id

    async def attach_property(self, database: str, target_id: str, property_id: str) -> None:
        """Attach an existing PROPERTY to a CLASS or RELATION via DEFINE_PROPERTY (idempotent).

        Use to reuse a property across classes/relations (e.g. a START_DATE property already
        declared on one relation, wanted on another) instead of declaring a same-meaning
        near-duplicate under a new name.
        """
        target_node = self._require_class_or_relation(database, target_id, "target_id")
        require_node_type(self._source, database, property_id, PROPERTY, "property_id", SchemaError)
        for nb in self._source.get_neighbors(database, target_id, edge_type=DEFINE_PROPERTY, direction="out"):
            if nb["node"]["id"] == property_id:
                return
        self._source.add_edge(database, target_id, DEFINE_PROPERTY, property_id, properties=edge_stamped())
        self._fire({
            "type": "property_attached", "database": database, "id": property_id,
            "target_id": target_id, "target_label": target_node.get("label", ""),
            "property_id": property_id,
        })

    async def update_class(
        self,
        database: str,
        class_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        require_node_type(self._source, database, class_id, CLASS, "class_id", SchemaError)
        props = {k: v for k, v in {"name": name, "description": description}.items() if v is not None}
        if not props:
            raise SchemaError("At least one of name or description must be provided.")
        self._source.update_node(database, class_id, touched(props))
        self._fire({"type": "class_updated", "database": database, "id": class_id})

    async def update_relation(
        self,
        database: str,
        relation_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        require_node_type(self._source, database, relation_id, RELATION, "relation_id", SchemaError)
        props = {k: v for k, v in {"name": name, "description": description}.items() if v is not None}
        if not props:
            raise SchemaError("At least one of name or description must be provided.")
        self._source.update_node(database, relation_id, touched(props))
        self._fire({"type": "relation_updated", "database": database, "id": relation_id})

    async def update_property(
        self,
        database: str,
        property_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        require_node_type(self._source, database, property_id, PROPERTY, "property_id", SchemaError)
        props = {k: v for k, v in {"name": name, "description": description}.items() if v is not None}
        if not props:
            raise SchemaError("At least one of name or description must be provided.")
        self._source.update_node(database, property_id, touched(props))
        self._fire({"type": "property_updated", "database": database, "id": property_id})

    async def delete_class(self, database: str, class_id: str) -> None:
        require_node_type(self._source, database, class_id, CLASS, "class_id", SchemaError)
        if self._source.get_neighbors(database, class_id, edge_type=IS_INSTANCE_OF, direction="in"):
            raise SchemaError("Class has entity instances. Delete them before removing the class.")
        if self._source.get_neighbors(database, class_id, edge_type=IS_SUBCLASS_OF, direction="in"):
            raise SchemaError(
                "Class is a parent of other classes. "
                "Remove subclass relationships first with `kb schema class parent remove`."
            )
        self._source.delete_node(database, class_id)
        self._fire({"type": "class_deleted", "database": database, "id": class_id})

    async def delete_relation(self, database: str, relation_id: str) -> None:
        require_node_type(self._source, database, relation_id, RELATION, "relation_id", SchemaError)
        if self._source.get_neighbors(database, relation_id, edge_type=REPRESENTS, direction="in"):
            raise SchemaError("Relation has fact instances. Delete them before removing the relation.")
        if self._source.get_neighbors(database, relation_id, edge_type=IS_SUBCLASS_OF, direction="in"):
            raise SchemaError(
                "Relation is a parent of other relations. "
                "Remove subclass relationships first with `kb schema relation parent remove`."
            )
        self._source.delete_node(database, relation_id)
        self._fire({"type": "relation_deleted", "database": database, "id": relation_id})

    async def delete_property(self, database: str, property_id: str) -> None:
        require_node_type(self._source, database, property_id, PROPERTY, "property_id", SchemaError)
        if self._source.get_neighbors(database, property_id, edge_type=IS_VALUE_OF, direction="in"):
            raise SchemaError("Property has existing values. Delete them before removing the property.")
        self._source.delete_node(database, property_id)
        self._fire({"type": "property_deleted", "database": database, "id": property_id})

    def _require_class_or_relation(self, database: str, node_id: str, param_name: str) -> dict:
        return require_node_type_one_of(
            self._source, database, node_id, (CLASS, RELATION), param_name, SchemaError
        )

    async def remove_is_subclass_of(self, database: str, child_id: str, parent_id: str) -> None:
        self._require_class_or_relation(database, child_id, "child_id")
        self._require_class_or_relation(database, parent_id, "parent_id")
        self._source.delete_edge(database, child_id, IS_SUBCLASS_OF, parent_id)

    async def add_is_subclass_of(self, database: str, child_id: str, parent_id: str) -> None:
        child_node = self._require_class_or_relation(database, child_id, "child_id")
        parent_node = self._require_class_or_relation(database, parent_id, "parent_id")
        child_label = child_node.get("label", "")
        parent_label = parent_node.get("label", "")
        if child_label != parent_label:
            raise SchemaError(
                f"child_id {child_id!r} is a {child_label} node but parent_id {parent_id!r} is a "
                f"{parent_label} node — a subclass relationship requires both to be the same kind "
                "(both CLASS or both RELATION)."
            )
        self._source.add_edge(database, child_id, IS_SUBCLASS_OF, parent_id, properties=edge_stamped())
        event_type = "class_subclass" if child_label == CLASS else "relation_subclass"
        self._fire({
            "type": event_type,
            "database": database,
            "id": child_id,
            "child_id":   child_id,
            "child_name":  child_node.get("properties",  {}).get("name"),
            "parent_id":  parent_id,
            "parent_name": parent_node.get("properties", {}).get("name"),
        })

    async def attach_relation_domain(self, database: str, relation_id: str, class_id: str) -> None:
        """Attach an existing CLASS to a relation's DOMAIN via DOMAIN (idempotent).

        Use to widen a relation after declaration (e.g. a WORKED_AT declared over EMPLOYEE,
        later also holding for CONTRACTOR) — `declare_relation_type` wires DOMAIN only on
        first declaration. Add-only: there is no detach counterpart.
        """
        rel_node = require_node_type(
            self._source, database, relation_id, RELATION, "relation_id", SchemaError
        )
        class_node = require_node_type(
            self._source, database, class_id, CLASS, "class_id", SchemaError
        )
        nbs = self._source.get_neighbors(database, relation_id, edge_type=DOMAIN, direction="out")
        if any(nb["node"]["id"] == class_id for nb in nbs):
            return
        self._source.add_edge(database, relation_id, DOMAIN, class_id, properties=edge_stamped())
        self._fire({
            "type": "relation_domain_attached",
            "database": database,
            "id": relation_id,
            "relation_id":   relation_id,
            "relation_name": rel_node.get("properties",   {}).get("name"),
            "class_id":      class_id,
            "class_name":    class_node.get("properties", {}).get("name"),
        })

    async def attach_relation_range(self, database: str, relation_id: str, class_id: str) -> None:
        """Attach an existing CLASS to a relation's RANGE via RANGE (idempotent).

        Use to widen a relation after declaration (e.g. a WORKED_AT declared over EMPLOYEE,
        later also holding for CONTRACTOR) — `declare_relation_type` wires RANGE only on
        first declaration. Add-only: there is no detach counterpart.
        """
        rel_node = require_node_type(
            self._source, database, relation_id, RELATION, "relation_id", SchemaError
        )
        class_node = require_node_type(
            self._source, database, class_id, CLASS, "class_id", SchemaError
        )
        nbs = self._source.get_neighbors(database, relation_id, edge_type=RANGE, direction="out")
        if any(nb["node"]["id"] == class_id for nb in nbs):
            return
        self._source.add_edge(database, relation_id, RANGE, class_id, properties=edge_stamped())
        self._fire({
            "type": "relation_range_attached",
            "database": database,
            "id": relation_id,
            "relation_id":   relation_id,
            "relation_name": rel_node.get("properties",   {}).get("name"),
            "class_id":      class_id,
            "class_name":    class_node.get("properties", {}).get("name"),
        })

    # ------------------------------------------------------------------ #
    # Expansion                                                            #
    # ------------------------------------------------------------------ #

    async def expand_schema(
        self,
        database: str,
        text: str,
        *,
        depth: int = 1,
        reference: Literal["llm", "mcp"] = "llm",
        mcp_servers: list[dict[str, Any]] | None = None,
        dedup_mode: Literal["search", "llm", "none"] = "search",
        model: str = "gpt-4o",
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None = None,
        max_turns: int = 25,
    ) -> None:
        """Grow the schema (classes, relations, properties) grounded by *text*.

        Never touches entities, facts, or property values — run `import_text`
        (extraction) afterward against the schema this produces. See
        SchemaExpansionEngine for parameter details.
        """
        await self._expansion.expand_schema(
            database,
            text,
            depth=depth,
            reference=reference,
            mcp_servers=mcp_servers,
            dedup_mode=dedup_mode,
            model=model,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            max_turns=max_turns,
        )

    # ------------------------------------------------------------------ #
    # Hierarchy reads                                                      #
    # ------------------------------------------------------------------ #

    def _hierarchy_walk(
        self,
        database: str,
        node_id: str,
        direction: str,
        max_hops: int | None,
    ) -> list[dict]:
        # The start node is seeded into visited so a malformed cycle can never
        # re-add it or hang the walk.
        visited: set[str] = {node_id}
        results: list[dict] = []
        queue: list[tuple[str, int]] = [(node_id, 0)]
        while queue:
            current, depth = queue.pop(0)
            if max_hops is not None and depth >= max_hops:
                continue
            neighbors = self._source.get_neighbors(
                database, current, edge_type=IS_SUBCLASS_OF, direction=direction
            )
            for nb in neighbors:
                node = nb["node"]
                nid = node["id"]
                if nid not in visited:
                    visited.add(nid)
                    results.append(node)
                    queue.append((nid, depth + 1))
        return results

    async def get_class_ancestors(
        self,
        database: str,
        class_id: str,
        max_hops: int | None = 5,
    ) -> list[dict]:
        """CLASS ancestors via IS_SUBCLASS_OF, up to *max_hops* edges (None = unbounded)."""
        require_node_type(self._source, database, class_id, CLASS, "class_id", SchemaError)
        return self._hierarchy_walk(database, class_id, "out", max_hops)

    async def get_class_descendants(
        self,
        database: str,
        class_id: str,
        max_hops: int | None = 5,
    ) -> list[dict]:
        """CLASS descendants via IS_SUBCLASS_OF, up to *max_hops* edges (None = unbounded)."""
        require_node_type(self._source, database, class_id, CLASS, "class_id", SchemaError)
        return self._hierarchy_walk(database, class_id, "in", max_hops)

    async def get_relation_ancestors(
        self,
        database: str,
        relation_id: str,
        max_hops: int | None = 5,
    ) -> list[dict]:
        """RELATION ancestors via IS_SUBCLASS_OF, up to *max_hops* edges (None = unbounded)."""
        require_node_type(
            self._source, database, relation_id, RELATION, "relation_id", SchemaError
        )
        return self._hierarchy_walk(database, relation_id, "out", max_hops)

    async def get_relation_descendants(
        self,
        database: str,
        relation_id: str,
        max_hops: int | None = 5,
    ) -> list[dict]:
        """RELATION descendants via IS_SUBCLASS_OF, up to *max_hops* edges (None = unbounded)."""
        require_node_type(
            self._source, database, relation_id, RELATION, "relation_id", SchemaError
        )
        return self._hierarchy_walk(database, relation_id, "in", max_hops)

    # ------------------------------------------------------------------ #
    # Domain / range and membership reads                                  #
    # ------------------------------------------------------------------ #

    async def get_class_entities(
        self,
        database: str,
        class_id: str,
        limit: int | None = 10,
    ) -> list[dict]:
        """ENTITY instances of *class_id* via IS_INSTANCE_OF, up to *limit* (None = unbounded)."""
        require_node_type(self._source, database, class_id, CLASS, "class_id", SchemaError)
        neighbors = self._source.get_neighbors(
            database, class_id, edge_type=IS_INSTANCE_OF, direction="in"
        )
        nodes = [nb["node"] for nb in neighbors]
        return nodes if limit is None else nodes[:limit]

    async def get_relation_domain_range(
        self,
        database: str,
        relation_id: str,
    ) -> dict[str, list[dict]]:
        """DOMAIN and RANGE CLASS nodes of *relation_id* as {"domain": [...], "range": [...]}."""
        require_node_type(
            self._source, database, relation_id, RELATION, "relation_id", SchemaError
        )
        dom_nbs = self._source.get_neighbors(
            database, relation_id, edge_type=DOMAIN, direction="out"
        )
        rng_nbs = self._source.get_neighbors(
            database, relation_id, edge_type=RANGE, direction="out"
        )
        return {
            "domain": [nb["node"] for nb in dom_nbs],
            "range": [nb["node"] for nb in rng_nbs],
        }

    async def get_allowed_relations_between_classes(
        self,
        database: str,
        head_class_id: str,
        tail_class_id: str,
        expand: bool = True,
        max_hops: int | None = 5,
    ) -> list[dict]:
        """RELATION nodes accepting the head/tail classes; *expand* lets ancestors satisfy."""
        require_node_type(
            self._source, database, head_class_id, CLASS, "head_class_id", SchemaError
        )
        require_node_type(
            self._source, database, tail_class_id, CLASS, "tail_class_id", SchemaError
        )
        head_ok = {head_class_id}
        tail_ok = {tail_class_id}
        if expand:
            head_anc = await self.get_class_ancestors(database, head_class_id, max_hops)
            tail_anc = await self.get_class_ancestors(database, tail_class_id, max_hops)
            head_ok |= {n["id"] for n in head_anc}
            tail_ok |= {n["id"] for n in tail_anc}
        allowed: list[dict] = []
        for rel in self._source.match_nodes(database, label=RELATION):
            rid = rel["id"]
            dom_nbs = self._source.get_neighbors(database, rid, edge_type=DOMAIN, direction="out")
            rng_nbs = self._source.get_neighbors(database, rid, edge_type=RANGE, direction="out")
            dom = {nb["node"]["id"] for nb in dom_nbs}
            rng = {nb["node"]["id"] for nb in rng_nbs}
            if dom & head_ok and rng & tail_ok:
                allowed.append(rel)
        return allowed

    # ------------------------------------------------------------------ #
    # Exact-name lookups                                                   #
    # ------------------------------------------------------------------ #

    async def find_class(self, database: str, name: str) -> dict | None:
        """CLASS node with exactly *name*, or None."""
        nodes = self._source.match_nodes(database, label=CLASS, properties={"name": name})
        return nodes[0] if nodes else None

    async def find_relation_type(self, database: str, name: str) -> dict | None:
        """RELATION node with exactly *name*, or None."""
        nodes = self._source.match_nodes(database, label=RELATION, properties={"name": name})
        return nodes[0] if nodes else None

    async def find_property(self, database: str, name: str) -> dict | None:
        """PROPERTY node with exactly *name*, or None."""
        nodes = self._source.match_nodes(database, label=PROPERTY, properties={"name": name})
        return nodes[0] if nodes else None

    # ------------------------------------------------------------------ #
    # Schema export / import                                               #
    # ------------------------------------------------------------------ #

    async def fetch_database_schema(self, database: str) -> DataSchema:
        schema = DataSchema()

        for class_node in self._source.match_nodes(database, label=CLASS):
            nid = class_node["id"]
            props = class_node.get("properties", {})
            class_name = props.get("name", nid)
            schema.add_entity(class_name, description=props.get("description", ""))
            for nb in self._source.get_neighbors(database, nid, edge_type=DEFINE_PROPERTY, direction="out"):
                p = nb["node"].get("properties", {})
                schema.add_entity_attribute(
                    class_name,
                    p.get("name", nb["node"]["id"]),
                    "str",
                    description=p.get("description", ""),
                )

        for class_node in self._source.match_nodes(database, label=CLASS):
            nid = class_node["id"]
            child_name = class_node.get("properties", {}).get("name", nid)
            for nb in self._source.get_neighbors(database, nid, edge_type=IS_SUBCLASS_OF, direction="out"):
                parent_name = nb["node"].get("properties", {}).get("name", nb["node"]["id"])
                schema.add_relation(child_name, IS_SUBCLASS_OF, parent_name)

        for rel_node in self._source.match_nodes(database, label=RELATION):
            nid = rel_node["id"]
            props = rel_node.get("properties", {})
            rel_name = props.get("name", nid)
            rel_desc = props.get("description", "")
            domains = [
                nb["node"].get("properties", {}).get("name", nb["node"]["id"])
                for nb in self._source.get_neighbors(database, nid, edge_type=DOMAIN, direction="out")
            ] or ["*"]
            ranges = [
                nb["node"].get("properties", {}).get("name", nb["node"]["id"])
                for nb in self._source.get_neighbors(database, nid, edge_type=RANGE, direction="out")
            ] or ["*"]
            rel_prop_nodes = [
                nb["node"]
                for nb in self._source.get_neighbors(database, nid, edge_type=DEFINE_PROPERTY, direction="out")
            ]
            for domain_name in domains:
                for range_name in ranges:
                    rel_key = schema.add_relation(domain_name, rel_name, range_name, description=rel_desc)
                    for prop_node in rel_prop_nodes:
                        p = prop_node.get("properties", {})
                        schema.add_relation_attribute(
                            rel_key,
                            p.get("name", prop_node["id"]),
                            "str",
                            description=p.get("description", ""),
                        )

        for rel_node in self._source.match_nodes(database, label=RELATION):
            nid = rel_node["id"]
            child_name = rel_node.get("properties", {}).get("name", nid)
            for nb in self._source.get_neighbors(database, nid, edge_type=IS_SUBCLASS_OF, direction="out"):
                parent_name = nb["node"].get("properties", {}).get("name", nb["node"]["id"])
                schema.add_relation(child_name, IS_SUBCLASS_OF, parent_name)

        return schema

    async def export_database_schema(self, database: str) -> dict:
        """Portable JSON form of `fetch_database_schema`, symmetric with `import_database_schema`."""
        return (await self.fetch_database_schema(database)).to_json()

    async def import_database_schema(self, database: str, schema_json: dict) -> None:
        schema = DataSchema.from_json(schema_json)

        class_ids: dict[str, str] = {}
        for entity_obj in schema.entities.values():
            class_name = entity_obj["name"]
            class_id = await self.declare_class(
                database, name=class_name, description=entity_obj.get("description", "")
            )
            class_ids[class_name] = class_id
            for attr_name, attr_obj in entity_obj.get("contents", {}).get("attributes", {}).items():
                await self.declare_class_property(
                    database,
                    name=attr_name,
                    description=attr_obj.get("description", "") if isinstance(attr_obj, dict) else "",
                    class_id=class_id,
                )

        rel_data: dict[str, dict] = {}
        for rel_obj in schema.relations.values():
            rel_name = rel_obj["name"]
            if rel_name not in rel_data:
                rel_data[rel_name] = {
                    "description": rel_obj.get("description", ""),
                    "domains": [],
                    "ranges": [],
                    "attributes": rel_obj.get("contents", {}).get("attributes", {}),
                }
            src = rel_obj.get("source", "")
            tgt = rel_obj.get("target", "")
            if src and src != "*" and src not in rel_data[rel_name]["domains"]:
                rel_data[rel_name]["domains"].append(src)
            if tgt and tgt != "*" and tgt not in rel_data[rel_name]["ranges"]:
                rel_data[rel_name]["ranges"].append(tgt)

        for rel_name, rd in rel_data.items():
            relation_id = await self.declare_relation_type(
                database, name=rel_name, description=rd["description"],
                domain=rd["domains"], range=rd["ranges"],
            )
            for attr_name, attr_obj in rd["attributes"].items():
                await self.declare_relation_property(
                    database,
                    name=attr_name,
                    description=attr_obj.get("description", "") if isinstance(attr_obj, dict) else "",
                    relation_id=relation_id,
                )

    # ------------------------------------------------------------------ #
    # Search and consolidation                                             #
    # ------------------------------------------------------------------ #

    async def find_schema(
        self,
        database: str,
        query: str,
        type_filter: str | list[str] | None = None,
    ) -> list[SchemaSearchResult]:
        """Return schema elements whose name or description contains *query* (case-insensitive)."""
        all_labels = (CLASS, RELATION, PROPERTY)
        if type_filter is None:
            labels = all_labels
        else:
            requested = {type_filter.upper()} if isinstance(type_filter, str) else {t.upper() for t in type_filter}
            labels = tuple(lbl for lbl in all_labels if lbl.upper() in requested)
        q = query.lower()
        results: list[SchemaSearchResult] = []
        for label in labels:
            for node in self._source.match_nodes(database, label=label):
                props = node.get("properties", {})
                name = props.get("name", "")
                desc = props.get("description", "") or ""
                if q in name.lower() or q in desc.lower():
                    results.append(SchemaSearchResult(id=node["id"], type=label, name=name, description=desc))
        return results

    async def consolidate_schema(
        self,
        database: str,
        operation: str,
        source_names: list[str],
        target_name: str,
        predicate: object | None = None,
    ) -> None:
        """Merge or split schema elements and rewire instances accordingly."""
        if operation == "merge":
            await self._merge_schema(database, source_names, target_name)
        else:
            raise SchemaError(f"consolidate_schema operation '{operation}' is not supported")

    async def _merge_schema(
        self,
        database: str,
        source_names: list[str],
        target_name: str,
    ) -> None:
        # Collect source nodes, all must share the same label
        source_nodes: list[dict] = []
        label: str | None = None
        for name in source_names:
            found = False
            for lbl in (CLASS, RELATION, PROPERTY):
                nodes = self._source.match_nodes(database, label=lbl, properties={"name": name})
                if nodes:
                    if label is not None and label != lbl:
                        raise SchemaError("cannot merge schema elements of different types")
                    label = lbl
                    source_nodes.extend(nodes)
                    found = True
                    break
            if not found:
                raise SchemaError(f"schema element '{name}' not found in '{database}'")

        if label is None:
            raise SchemaError("no source schema elements found")

        existing_target = self._source.match_nodes(database, label=label, properties={"name": target_name})
        target_id = (
            existing_target[0]["id"]
            if existing_target
            else self._source.add_node(database, label, stamped({"name": target_name, "description": ""}))
        )

        # Edge type and direction for finding instances that reference each source node
        instance_edge_map: dict[str, str] = {
            CLASS: IS_SUBCLASS_OF,   # overridden below; main instance edge is IS_INSTANCE_OF
            RELATION: "REPRESENTS",
            PROPERTY: "IS_VALUE_OF",
        }
        # Primary instance rewire: incoming edges pointing to the schema node
        primary: dict[str, str] = {
            CLASS: "IS_INSTANCE_OF",
            RELATION: "REPRESENTS",
            PROPERTY: "IS_VALUE_OF",
        }
        _ = instance_edge_map  # suppress unused warning

        edge_type = primary[label]
        for src_node in source_nodes:
            if src_node["id"] == target_id:
                continue
            for nb in self._source.get_neighbors(database, src_node["id"], edge_type=edge_type, direction="in"):
                instance_id = nb["node"]["id"]
                try:
                    self._source.add_edge(database, instance_id, edge_type, target_id, properties=edge_stamped())
                except Exception:  # noqa: BLE001
                    pass
