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
from blue_data_kb_source._text_match import match_score
from blue_data_kb_source.checks import require_free_id, require_node_type, require_node_type_one_of
from blue_data_kb_source.schema.expansion import SchemaExpansionEngine
from blue_data_kb_source.timestamps import edge_stamped, stamped, touched
from blue_data_kb_source.types import SchemaError, SchemaSearchResult

import re
from typing import TYPE_CHECKING, Any, Callable, Literal

if TYPE_CHECKING:
    from blue_data_kb_source.notification.notifier import ChangeNotifier
    from blue_data_kb_source.search.indexer import KBSearchIndexer

# camelCase word starts: lower->upper ("worksAt"), and digit->Capitalized word
# ("item2Name"). A digit before a capital that isn't followed by a lowercase letter
# is not a boundary: "B2B" stays one word, so a canonical name has no boundary
# left to split and canonicalizing is idempotent (#125).
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[0-9])(?=[A-Z][a-z])")
_NON_ALNUM_RUN = re.compile(r"[^0-9A-Za-z]+")


# Merge fires the same change events an ordinary declare/update/delete fires,
# so the search indexer rebuilds the entries naming a merged element instead of
# keeping the pre-merge schema indexed. Keyed by the node's own label.
_SCHEMA_UPDATED_EVENT = {CLASS: "class_updated", RELATION: "relation_updated", PROPERTY: "property_updated"}
_SCHEMA_DELETED_EVENT = {CLASS: "class_deleted", RELATION: "relation_deleted", PROPERTY: "property_deleted"}
# Neighbours whose indexed text names the merged element. LITERAL and
# EXTRACTION are absent on purpose: the indexer has no entry for either, so
# `reindex_node` would no-op on them anyway.
_NODE_UPDATED_EVENT = {
    ENTITY: "entity_updated",
    FACT: "fact_updated",
    CLASS: "class_updated",
    RELATION: "relation_updated",
    PROPERTY: "property_updated",
}


# Structural edge names that must never also exist as a declared RELATION: a RELATION
# named IS_SUBCLASS_OF looks like a hierarchy in exports and listings while carrying
# none of its meaning (#108).
_RESERVED_RELATION_NAMES = frozenset({IS_SUBCLASS_OF, IS_INSTANCE_OF})


def _canonicalize_schema_name(name: str) -> str:
    """Canonicalize a CLASS/RELATION/PROPERTY name to UPPER_SNAKE_CASE.

    Splits camelCase where a word starts ("worksAt" -> "works_At", "item2Name" ->
    "item2_Name"; "B2B" is one word),
    then collapses every run of non-alphanumeric characters (spaces, hyphens,
    existing underscores) into a single underscore and uppercases the result.
    Idempotent — an already-canonical name passes through unchanged. Keeps
    declare_class/declare_relation_type/declare_*_property from creating a
    second schema element that differs from an existing one only by casing
    or separator style.
    """
    split = _CAMEL_BOUNDARY.sub("_", name)
    return _NON_ALNUM_RUN.sub("_", split).strip("_").upper()


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
                         ("confidence", "derived: the highest confidence among the fact's "
                                        "extractions, from 0.0 to 1.0; unset when none "
                                        "states one"),
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
                         ("text", "text segment of extraction from source"),
                         ("extraction_type", "form of the evidence: text_span, external_record, "
                                             "or assertion"),
                         ("confidence", "how confident the assertion is, from 0.0 to 1.0; "
                                        "unset when the source stated none"),
                         *_timestamp_attrs],
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
            "Links an instance item (entity, fact, literal) or schema element (class, relation, "
            "property) to its originating extraction "
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
            (CLASS,     EXTRACTED_FROM,  EXTRACTION, _extracted_desc),
            (RELATION,  EXTRACTED_FROM,  EXTRACTION, _extracted_desc),
            (PROPERTY,  EXTRACTED_FROM,  EXTRACTION, _extracted_desc),
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
        name = _canonicalize_schema_name(name)
        node = await self.find_class(database, name)
        if node:
            return node["id"]
        require_free_id(self._source, database, class_id, "class_id", SchemaError)
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
        name = _canonicalize_schema_name(name)
        if name in _RESERVED_RELATION_NAMES:
            raise SchemaError(
                f"'{name}' is one of the KB's own structural edges, not a relation type — "
                "use add_is_subclass_of for a class/relation hierarchy, and an entity's class "
                "for instance-of."
            )
        domain_names: list[str] = [
            _canonicalize_schema_name(d) for d in ([domain] if isinstance(domain, str) else (domain or []))
        ]
        range_names: list[str] = [
            _canonicalize_schema_name(r) for r in ([range] if isinstance(range, str) else (range or []))
        ]
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
        require_free_id(self._source, database, relation_id, "relation_id", SchemaError)
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
        name = _canonicalize_schema_name(name)
        if class_id is not None:
            require_node_type(self._source, database, class_id, CLASS, "class_id", SchemaError)
            for nb in self._source.get_neighbors(database, class_id, edge_type=DEFINE_PROPERTY, direction="out"):
                if nb["node"].get("properties", {}).get("name") == name:
                    return nb["node"]["id"]
        else:
            node = await self.find_property(database, name)
            if node:
                return node["id"]
        require_free_id(self._source, database, property_id, "property_id", SchemaError)
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
        name = _canonicalize_schema_name(name)
        if relation_id is not None:
            require_node_type(self._source, database, relation_id, RELATION, "relation_id", SchemaError)
            for nb in self._source.get_neighbors(database, relation_id, edge_type=DEFINE_PROPERTY, direction="out"):
                if nb["node"].get("properties", {}).get("name") == name:
                    return nb["node"]["id"]
        else:
            node = await self.find_property(database, name)
            if node:
                return node["id"]
        require_free_id(self._source, database, property_id, "property_id", SchemaError)
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
        if name is not None:
            name = self._checked_name(database, class_id, CLASS, name)
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
        if name is not None:
            name = self._checked_name(database, relation_id, RELATION, name)
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
        if name is not None:
            name = self._checked_name(database, property_id, PROPERTY, name)
        props = {k: v for k, v in {"name": name, "description": description}.items() if v is not None}
        if not props:
            raise SchemaError("At least one of name or description must be provided.")
        self._source.update_node(database, property_id, touched(props))
        self._fire({"type": "property_updated", "database": database, "id": property_id})

    def _checked_name(self, database: str, node_id: str, label: str, name: str) -> str:
        """The canonical form of *name* for renaming *node_id*, under the rules declare_*
        enforce: canonicalized, not a reserved relation name, and not already taken —
        by another node of the same label, or for a property by another property of
        the same owner (properties are owner-scoped, so VERSION may exist on two
        classes). Renames used to write the name as given (#114)."""
        name = _canonicalize_schema_name(name)
        if label == RELATION and name in _RESERVED_RELATION_NAMES:
            raise SchemaError(
                f"'{name}' is one of the KB's own structural edges, not a relation type."
            )
        if label == PROPERTY:
            for owner in self._source.get_neighbors(database, node_id, edge_type=DEFINE_PROPERTY, direction="in"):
                siblings = self._source.get_neighbors(
                    database, owner["node"]["id"], edge_type=DEFINE_PROPERTY, direction="out"
                )
                if any(sib["node"]["id"] != node_id and sib["node"].get("properties", {}).get("name") == name
                       for sib in siblings):
                    owner_name = owner["node"].get("properties", {}).get("name")
                    raise SchemaError(f"{owner_name} already has a property named {name!r}.")
            return name
        clash = [n for n in self._source.match_nodes(database, label=label, properties={"name": name})
                 if n["id"] != node_id]
        if clash:
            raise SchemaError(
                f"a {label} named {name!r} already exists ({clash[0]['id']}); merge the two with "
                "consolidate_schema instead of renaming one onto the other."
            )
        return name

    async def delete_class(self, database: str, class_id: str) -> None:
        require_node_type(self._source, database, class_id, CLASS, "class_id", SchemaError)
        if self._source.get_neighbors(database, class_id, edge_type=IS_INSTANCE_OF, direction="in"):
            raise SchemaError("Class has entity instances. Delete them before removing the class.")
        if self._source.get_neighbors(database, class_id, edge_type=IS_SUBCLASS_OF, direction="in"):
            raise SchemaError(
                "Class is a parent of other classes. "
                "Remove subclass relationships first with `blue-kb schema class parent remove`."
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
                "Remove subclass relationships first with `blue-kb schema relation parent remove`."
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
        """Wire *child_id* IS_SUBCLASS_OF *parent_id* (idempotent).

        Both must be the same kind (CLASS or RELATION). Raises SchemaError on a
        self-loop or when the edge would close a cycle in the hierarchy.
        """
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
        child_name = child_node.get("properties", {}).get("name")
        parent_name = parent_node.get("properties", {}).get("name")
        if child_id == parent_id:
            raise SchemaError(f"{child_name} ({child_id}) cannot be a subclass of itself.")
        nbs = self._source.get_neighbors(database, child_id, edge_type=IS_SUBCLASS_OF, direction="out")
        if any(nb["node"]["id"] == parent_id for nb in nbs):
            return
        descendants = self._hierarchy_walk(database, child_id, "in", max_hops=None)
        if any(node["id"] == parent_id for node in descendants):
            raise SchemaError(
                f"{parent_name} ({parent_id}) is already a descendant of {child_name} ({child_id}) — "
                "adding this subclass relationship would create a cycle."
            )
        self._source.add_edge(database, child_id, IS_SUBCLASS_OF, parent_id, properties=edge_stamped())
        event_type = "class_subclass" if child_label == CLASS else "relation_subclass"
        self._fire({
            "type": event_type,
            "database": database,
            "id": child_id,
            "child_id":   child_id,
            "child_name":  child_name,
            "parent_id":  parent_id,
            "parent_name": parent_name,
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
        timeout: float | None = None,
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
            timeout=timeout,
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
        subclass_pairs: list[tuple[str, str]] = []
        for rel_obj in schema.relations.values():
            rel_name = rel_obj["name"]
            # fetch_database_schema writes each IS_SUBCLASS_OF edge (class or relation
            # hierarchy) as a relations entry; it is structure, not a relation type.
            if rel_name == IS_SUBCLASS_OF:
                subclass_pairs.append((rel_obj.get("source", ""), rel_obj.get("target", "")))
                continue
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

        # Wired last, once every class and relation exists. A child name that is a
        # class resolves as a class hierarchy edge, otherwise as a relation one.
        for child_name, parent_name in subclass_pairs:
            if child_name in class_ids:
                child = await self.find_class(database, child_name)
                parent = await self.find_class(database, parent_name)
            else:
                child = await self.find_relation_type(database, child_name)
                parent = await self.find_relation_type(database, parent_name)
            if child is None or parent is None:
                raise SchemaError(
                    f"IS_SUBCLASS_OF entry {child_name!r} -> {parent_name!r} names an element "
                    "that isn't declared in the imported schema."
                )
            await self.add_is_subclass_of(database, child["id"], parent["id"])

    # ------------------------------------------------------------------ #
    # Search and consolidation                                             #
    # ------------------------------------------------------------------ #

    async def find_schema(
        self,
        database: str,
        query: str,
        type_filter: str | list[str] | None = None,
    ) -> list[SchemaSearchResult]:
        """Schema elements matching *query* by keyword, best first: the whole query inside
        the name or description, then the most query words shared (names split on `_`, so
        "is proficient in" finds PROFICIENT_IN). The fallback when no search index is
        configured."""
        all_labels = (CLASS, RELATION, PROPERTY)
        if type_filter is None:
            labels = all_labels
        else:
            requested = {type_filter.upper()} if isinstance(type_filter, str) else {t.upper() for t in type_filter}
            labels = tuple(lbl for lbl in all_labels if lbl.upper() in requested)
        scored: list[tuple[int, SchemaSearchResult]] = []
        for label in labels:
            for node in self._source.match_nodes(database, label=label):
                props = node.get("properties", {})
                name = props.get("name", "")
                desc = props.get("description", "") or ""
                score = match_score(query, f"{name} {desc}")
                if score:
                    scored.append((score, SchemaSearchResult(id=node["id"], type=label, name=name, description=desc)))
        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored]

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

    def _find_merge_sources(
        self, database: str, source_names: list[str]
    ) -> tuple[list[dict], str]:
        """The nodes named by *source_names*, and the single label they share.

        Names are canonicalized the way `declare_*` canonicalizes them, so
        `["worker"]` finds the WORKER a declare call would have created.
        """
        source_nodes: list[dict] = []
        label: str | None = None
        for raw_name in source_names:
            name = _canonicalize_schema_name(raw_name)
            for lbl in (CLASS, RELATION, PROPERTY):
                nodes = self._source.match_nodes(database, label=lbl, properties={"name": name})
                if not nodes:
                    continue
                if label is not None and label != lbl:
                    raise SchemaError("cannot merge schema elements of different types")
                label = lbl
                source_nodes.extend(nodes)
                break
            else:
                raise SchemaError(f"schema element '{raw_name}' not found in '{database}'")

        if label is None:
            raise SchemaError("no source schema elements found")
        return source_nodes, label

    def _edge_exists(self, database: str, source_id: str, edge_type: str, target_id: str) -> bool:
        return bool(self._source.match_edges(
            database, edge_type=edge_type, source_id=source_id, target_id=target_id
        ))

    def _rewire_edges(self, database: str, source_id: str, target_id: str) -> set[str]:
        """Re-point every edge incident to *source_id* at *target_id*.

        Generic rather than a per-label list of edge types, because a merge
        means "nothing references the source any more" — and a hardcoded list
        silently misses whichever edge type gets added next. The source node is
        deleted afterwards, which drops the original edges with it (delete_node
        removes every incident edge), so nothing has to be unwired by hand.

        Returns the ids of the nodes on the far side, whose index entries name
        the merged element and so need rebuilding.
        """
        affected: set[str] = set()
        for nb in self._source.get_neighbors(database, source_id):
            edge = nb["edge"]
            outgoing = nb["direction"] == "out"
            other_id = edge["target_id"] if outgoing else edge["source_id"]
            # An edge between the two participants, or a self-loop on the
            # source, would become a self-loop on the target: drop it.
            if other_id in (source_id, target_id):
                continue
            new_source, new_target = (
                (target_id, other_id) if outgoing else (other_id, target_id)
            )
            # add_edge neither deduplicates nor raises, so an edge the target
            # already has must be checked for explicitly — otherwise merging
            # two classes an entity already belonged to would leave it with
            # the same IS_INSTANCE_OF edge twice.
            if not self._edge_exists(database, new_source, edge["edge_type"], new_target):
                self._source.add_edge(
                    database, new_source, edge["edge_type"], new_target,
                    properties=dict(edge.get("properties") or {}),
                )
            affected.add(other_id)
        return affected

    def _refuse_merge_cycle(
        self, database: str, source_ids: list[str], target_id: str | None, target_name: str
    ) -> None:
        """Raise if folding *source_ids* into the target would close a hierarchy cycle.

        _rewire_edges re-points every IS_SUBCLASS_OF edge of a source at the target,
        which can close a loop through a third node (EMPLOYEE → PERSON → AGENT, merge
        EMPLOYEE into AGENT → AGENT ⇄ PERSON) — a state add_is_subclass_of refuses, and
        one an export of the KB can't be re-imported from (#114). Simulate the merged
        hierarchy and look for a cycle before writing anything.
        """
        merged = target_id or "<new merge target>"
        fold = {sid: merged for sid in source_ids}
        graph: dict[str, set[str]] = {}
        for e in self._source.match_edges(database, edge_type=IS_SUBCLASS_OF):
            a, b = fold.get(e["source_id"], e["source_id"]), fold.get(e["target_id"], e["target_id"])
            if a != b:  # edges between merge participants become self-loops and are dropped
                graph.setdefault(a, set()).add(b)

        state: dict[str, int] = {}  # 1 = on the current path, 2 = done

        def on_cycle(node: str) -> bool:
            state[node] = 1
            for nxt in graph.get(node, ()):
                if state.get(nxt) == 1 or (nxt not in state and on_cycle(nxt)):
                    return True
            state[node] = 2
            return False

        if merged in graph and on_cycle(merged):
            raise SchemaError(
                f"merging into {target_name!r} would create a cycle in the class/relation "
                "hierarchy; remove one of the subclass links first."
            )

    async def _merge_schema(
        self,
        database: str,
        source_names: list[str],
        target_name: str,
    ) -> None:
        """Fold every source element into *target_name*, leaving only the target.

        Everything that referenced a source — instances, subclass links,
        domain/range, property definitions, provenance — references the target
        afterwards, and the source nodes are gone. Change events are fired for
        the target, for each rewired neighbour and for each deleted source, so
        the search index reflects the merged schema rather than the one that
        existed before the call.
        """
        source_nodes, label = self._find_merge_sources(database, source_names)
        target_name = _canonicalize_schema_name(target_name)
        if label == RELATION and target_name in _RESERVED_RELATION_NAMES:
            raise SchemaError(
                f"'{target_name}' is one of the KB's own structural edges, not a relation type."
            )

        existing_target = self._source.match_nodes(database, label=label, properties={"name": target_name})
        # Checked before anything is written, so a refused merge leaves the graph as it was.
        self._refuse_merge_cycle(
            database, [n["id"] for n in source_nodes],
            existing_target[0]["id"] if existing_target else None, target_name,
        )
        if existing_target:
            target_id = existing_target[0]["id"]
        else:
            target_id = self._source.add_node(
                database, label, stamped({"name": target_name, "description": ""})
            )

        affected: set[str] = set()
        for src_node in source_nodes:
            if src_node["id"] == target_id:
                continue
            # A merged-away element's description is the only record of what it
            # meant; keep it if the target has none of its own.
            target_node = self._source.get_node(database, target_id) or {}
            if not (target_node.get("properties", {}) or {}).get("description"):
                description = (src_node.get("properties", {}) or {}).get("description")
                if description:
                    self._source.update_node(database, target_id, touched({"description": description}))

            affected |= self._rewire_edges(database, src_node["id"], target_id)
            self._source.delete_node(database, src_node["id"])
            self._fire({"type": _SCHEMA_DELETED_EVENT[label], "database": database, "id": src_node["id"]})

        self._fire({"type": _SCHEMA_UPDATED_EVENT[label], "database": database, "id": target_id})
        for node_id in affected:
            node = self._source.get_node(database, node_id)
            if node is None:
                continue
            event = _NODE_UPDATED_EVENT.get(node.get("label", ""))
            if event:
                self._fire({"type": event, "database": database, "id": node_id})
