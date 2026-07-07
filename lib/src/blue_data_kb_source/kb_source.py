from __future__ import annotations

from abc import ABC
from typing import Any, Callable, Literal

from blue_data.core.schema import DataSchema
from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import (
    BOOLEAN,
    CLASS,
    DATE,
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
    NUMBER,
    PROPERTY,
    RANGE,
    RELATION,
    REPRESENTS,
    SOURCE,
    STRING,
    TAIL,
)
from blue_data_kb_source.extraction.engine import ExtractionEngine
from blue_data_kb_source.knowledge.manager import KnowledgeManager
from blue_data_kb_source.notification.notifier import ChangeNotifier
from blue_data_kb_source.provenance.tracker import ProvenanceTracker
from blue_data_kb_source.schema.manager import SchemaManager
from blue_data_kb_source.types import (
    AnswerResult,
    EnrichmentResult,
    SchemaSearchResult,
    ValidationResult,
)


class KBSource(GraphSource, ABC):
    """Abstract knowledge-base source built on top of GraphSource.

    Provides typed builders for the KB schema:
      Nodes  — CLASS, RELATION, PROPERTY, ENTITY, FACT, LITERAL, SOURCE, EXTRACTION
      Edges  — IS_SUBCLASS_OF, IS_INSTANCE_OF, DEFINE_PROPERTY, IS_VALUE_OF,
               HAS_PROPERTY, REPRESENTS, HEAD, TAIL, DOMAIN, RANGE, EXTRACTED_FROM

    Backend is selected by mixing with a concrete GraphSource via Python MRO:

        class MemoryKBSource(KBSource, MemoryGraphSource): pass

    Subclasses must implement the abstract GraphSource primitives:
    add_node, get_node, delete_node, add_edge, delete_edge, get_neighbors.
    """

    # ------------------------------------------------------------------ #
    # Node label constants (re-exported for callers)                       #
    # ------------------------------------------------------------------ #

    CLASS = CLASS
    RELATION = RELATION
    PROPERTY = PROPERTY
    ENTITY = ENTITY
    FACT = FACT
    LITERAL = LITERAL
    SOURCE = SOURCE
    EXTRACTION = EXTRACTION

    # ------------------------------------------------------------------ #
    # Edge type constants                                                  #
    # ------------------------------------------------------------------ #

    IS_SUBCLASS_OF = IS_SUBCLASS_OF
    DEFINE_PROPERTY = DEFINE_PROPERTY
    DOMAIN = DOMAIN
    RANGE = RANGE
    IS_INSTANCE_OF = IS_INSTANCE_OF
    IS_VALUE_OF = IS_VALUE_OF
    HAS_PROPERTY = HAS_PROPERTY
    REPRESENTS = REPRESENTS
    HEAD = HEAD
    TAIL = TAIL
    EXTRACTED_FROM = EXTRACTED_FROM

    # ------------------------------------------------------------------ #
    # Literal type constants                                               #
    # ------------------------------------------------------------------ #

    NUMBER = NUMBER
    DATE = DATE
    BOOLEAN = BOOLEAN
    STRING = STRING

    # ------------------------------------------------------------------ #
    # Initialisation                                                       #
    # ------------------------------------------------------------------ #

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._notifier = ChangeNotifier()
        self._provenance = ProvenanceTracker(self)
        self._schema = SchemaManager(self)
        self._knowledge = KnowledgeManager(self, self._provenance, self._notifier)
        self._extraction = ExtractionEngine(self, self._schema, self._knowledge)

    # ------------------------------------------------------------------ #
    # Schema operations → SchemaManager                                   #
    # ------------------------------------------------------------------ #

    async def declare_class(
        self,
        database: str,
        name: str,
        description: str,
        class_id: str | None = None,
    ) -> str:
        return await self._schema.declare_class(database, name, description, class_id)

    async def declare_relation_type(
        self,
        database: str,
        name: str,
        description: str,
        is_symmetric: bool = False,
        relation_id: str | None = None,
        domain: str | list[str] | None = None,
        range_: str | list[str] | None = None,
    ) -> str:
        return await self._schema.declare_relation_type(
            database, name, description, is_symmetric, relation_id, domain, range_
        )

    async def declare_class_property(
        self,
        database: str,
        name: str,
        description: str,
        property_id: str | None = None,
        class_id: str | None = None,
    ) -> str:
        return await self._schema.declare_class_property(
            database, name, description, property_id, class_id
        )

    async def declare_relation_property(
        self,
        database: str,
        name: str,
        description: str,
        property_id: str | None = None,
        relation_id: str | None = None,
    ) -> str:
        return await self._schema.declare_relation_property(
            database, name, description, property_id, relation_id
        )

    async def add_is_subclass_of(self, database: str, child_id: str, parent_id: str) -> None:
        await self._schema.add_is_subclass_of(database, child_id, parent_id)

    async def fetch_database_schema(self, database: str) -> DataSchema:
        return await self._schema.fetch_database_schema(database)

    async def import_database_schema(self, database: str, schema_json: dict) -> None:
        await self._schema.import_database_schema(database, schema_json)

    async def search_schema(self, database: str, query: str) -> list[SchemaSearchResult]:
        return await self._schema.search_schema(database, query)

    async def consolidate_schema(
        self,
        database: str,
        operation: str,
        source_names: list[str],
        target_name: str,
        predicate: object | None = None,
    ) -> None:
        await self._schema.consolidate_schema(database, operation, source_names, target_name, predicate)

    # ------------------------------------------------------------------ #
    # Knowledge operations → KnowledgeManager                             #
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
        return await self._knowledge.add_entity(
            database, name, description, entity_id, class_id, text, source_id
        )

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
        return await self._knowledge.add_fact(
            database, name, description, fact_id,
            head_entity_id, tail_entity_id, relation_id, text, source_id,
        )

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
        return await self._knowledge.add_entity_property_value(
            database, value, name, description, literal_type, entity_id, property_id, text, source_id,
        )

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
        return await self._knowledge.add_fact_property_value(
            database, value, name, description, literal_type, fact_id, property_id, text, source_id,
        )

    async def fetch_database_data(
        self,
        database: str,
        include_provenance: bool = False,
        compact: bool = False,
    ) -> dict | list:
        return await self._knowledge.fetch_database_data(database, include_provenance, compact)

    async def answer(self, database: str, question: str) -> list[AnswerResult]:
        return await self._knowledge.answer(database, question)

    async def validate(self, database: str, statement: str) -> ValidationResult:
        return await self._knowledge.validate(database, statement)

    async def enrich(self, database: str, query: str, depth: int = 2) -> list[EnrichmentResult]:
        return await self._knowledge.enrich(database, query, depth)

    # ------------------------------------------------------------------ #
    # Provenance operations → ProvenanceTracker                           #
    # ------------------------------------------------------------------ #

    async def add_source(
        self,
        database: str,
        source: str,
        name: str | None = None,
        description: str | None = None,
        node_id: str | None = None,
    ) -> str:
        return await self._provenance.add_source(database, source, name, description, node_id)

    async def add_extraction(
        self,
        database: str,
        text: str,
        name: str | None = None,
        description: str | None = None,
        node_id: str | None = None,
        source_id: str | None = None,
    ) -> str:
        return await self._provenance.add_extraction(database, text, name, description, node_id, source_id)

    async def add_extracted_from(self, database: str, element_id: str, extraction_id: str) -> None:
        await self._provenance.add_extracted_from(database, element_id, extraction_id)

    # ------------------------------------------------------------------ #
    # Change hooks → ChangeNotifier                                        #
    # ------------------------------------------------------------------ #

    def add_change_hook(self, database: str, fn: Callable[[dict], None]) -> None:
        """Register *fn* to be called after every instance-level write to *database*.

        *fn* receives a dict with at minimum ``"type"``, ``"database"``, ``"id"``.

        Event types and extra keys:
        ``entity_added``          — ``name``, ``class_id``, ``class_name``
        ``fact_added``            — ``head/tail_entity_id/name``, ``relation_id/name``
        ``entity_property_added`` — ``entity_id``, ``property_id/name``, ``value``
        ``fact_property_added``   — ``fact_id``, ``property_id/name``, ``value``,
                                    ``head/tail_entity_name``, ``relation_name``
        """
        self._notifier.add_hook(database, fn)

    def remove_change_hook(self, database: str, fn: Callable[[dict], None]) -> None:
        self._notifier.remove_hook(database, fn)

    # ------------------------------------------------------------------ #
    # Extraction → ExtractionEngine                                        #
    # ------------------------------------------------------------------ #

    async def import_text(
        self,
        database: str,
        text: str,
        queries: list[str] | None = None,
        model: str = "gpt-4o",
        source_id: str | None = None,
    ) -> None:
        await self._extraction.import_text(database, text, queries, model, source_id)

    # ------------------------------------------------------------------ #
    # Meta-schema (static — describes the KB graph structure itself)       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def fetch_meta_schema() -> DataSchema:
        """Return a DataSchema describing the KB graph schema itself."""
        schema = DataSchema()

        schema.add_entity("CLASS", description="Defines a category or type. Example: PERSON, COMPANY")
        schema.add_entity_attribute("CLASS", "name", "str", description="name of the class")
        schema.add_entity_attribute("CLASS", "description", "str", description="description of the class")

        schema.add_entity(
            "RELATION",
            description="Defines the nature of a relationship between two entity classes. "
                        "Example: WORKED_AT, LIVES_IN, PURCHASED",
        )
        schema.add_entity_attribute("RELATION", "name", "str", description="name of the relation")
        schema.add_entity_attribute("RELATION", "description", "str", description="description of the relation")
        schema.add_entity_attribute("RELATION", "is_symmetric", "bool", description="boolean indicating symmetry")

        schema.add_entity("PROPERTY", description="Feature of an entity, class, relation, or fact. Example: AGE, START_DATE")
        schema.add_entity_attribute("PROPERTY", "name", "str", description="name of the property")
        schema.add_entity_attribute("PROPERTY", "description", "str", description="description of the property")

        schema.add_entity("ENTITY", description="A specific instance of a class. Example: Alice, IBM")
        schema.add_entity_attribute("ENTITY", "name", "str", description="name of the entity")
        schema.add_entity_attribute("ENTITY", "description", "str", description="description of the entity")

        schema.add_entity(
            "FACT",
            description="Represents a specific factual relation between entities. Example: Alice_WORKED_AT_IBM",
        )
        schema.add_entity_attribute("FACT", "name", "str", description="name of the fact")
        schema.add_entity_attribute("FACT", "description", "str", description="description of the fact")

        schema.add_entity("LITERAL", description="Specifies a property value. Example: 18, 1998")
        schema.add_entity_attribute("LITERAL", "name", "str", description="name of the literal")
        schema.add_entity_attribute("LITERAL", "description", "str", description="description of the literal")
        schema.add_entity_attribute("LITERAL", "value", "str | int | float | bool", description="value of the property")
        schema.add_entity_attribute("LITERAL", "type", "NUMBER | DATE | BOOLEAN | STRING", description="type of the value")

        schema.add_entity("SOURCE", description="A source of data.")
        schema.add_entity_attribute("SOURCE", "id", "str", description="id of the source")
        schema.add_entity_attribute("SOURCE", "name", "str", description="name of the source")
        schema.add_entity_attribute("SOURCE", "description", "str", description="description of the source")

        schema.add_entity("EXTRACTION", description="Extraction from a source.")
        schema.add_entity_attribute("EXTRACTION", "name", "str", description="name of the extraction")
        schema.add_entity_attribute("EXTRACTION", "description", "str", description="description of the extraction")
        schema.add_entity_attribute("EXTRACTION", "text", "str", description="text segment of extraction from source")

        _subclass_desc = (
            "Defines hierarchical inheritance between two schema elements. "
            "The child inherits properties and semantics from the parent. "
            "Example: EMPLOYEE IS_SUBCLASS_OF PERSON"
        )
        schema.add_relation("CLASS", "IS_SUBCLASS_OF", "CLASS", description=_subclass_desc)
        schema.add_relation("RELATION", "IS_SUBCLASS_OF", "RELATION", description=_subclass_desc)

        schema.add_relation(
            "ENTITY", "IS_INSTANCE_OF", "CLASS",
            description="Connects a specific entity to a class, asserting that the entity is a "
                        "concrete realization of that class. Example: IBM IS_INSTANCE_OF COMPANY",
        )

        _define_prop_desc = (
            "Declares that a class or relation has a given property in its schema definition. "
            "Example: PERSON DEFINE_PROPERTY AGE"
        )
        schema.add_relation("CLASS", "DEFINE_PROPERTY", "PROPERTY", description=_define_prop_desc)
        schema.add_relation("RELATION", "DEFINE_PROPERTY", "PROPERTY", description=_define_prop_desc)

        schema.add_relation(
            "LITERAL", "IS_VALUE_OF", "PROPERTY",
            description="Links a literal value to the property it instantiates. Example: 18 IS_VALUE_OF AGE",
        )

        _has_prop_desc = "Assigns a literal value or attribute to an entity or fact instance. Example: Alice HAS_PROPERTY 28"
        schema.add_relation("ENTITY", "HAS_PROPERTY", "LITERAL", description=_has_prop_desc)
        schema.add_relation("FACT", "HAS_PROPERTY", "LITERAL", description=_has_prop_desc)

        schema.add_relation(
            "FACT", "REPRESENTS", "RELATION",
            description="Connects a fact instance to the relation type it instantiates, "
                        "grounding the fact in schema semantics. "
                        "Example: Alice_WORKED_AT_IBM REPRESENTS WORKED_AT",
        )
        schema.add_relation("FACT", "HEAD", "ENTITY", description="Specifies the subject (from) entity of a fact. Example: FACT → Alice")
        schema.add_relation("FACT", "TAIL", "ENTITY", description="Specifies the object (to) entity of a fact. Example: FACT → IBM")

        schema.add_relation(
            "RELATION", "DOMAIN", "CLASS",
            description="Declares the valid source class of a relation (what type of entity can "
                        "appear in HEAD position). Example: WORKED_AT DOMAIN EMPLOYEE",
        )
        schema.add_relation(
            "RELATION", "RANGE", "CLASS",
            description="Declares the valid target class of a relation (what type of entity can "
                        "appear in TAIL position). Example: WORKED_AT RANGE ORGANIZATION",
        )

        _extracted_desc = (
            "Links any schema element (entity, fact, or literal) to its originating extraction "
            "record or source evidence. Example: Alice WORKED_AT IBM EXTRACTED_FROM Resume_Text_1"
        )
        schema.add_relation("ENTITY", "EXTRACTED_FROM", "EXTRACTION", description=_extracted_desc)
        schema.add_relation("LITERAL", "EXTRACTED_FROM", "EXTRACTION", description=_extracted_desc)
        schema.add_relation("FACT", "EXTRACTED_FROM", "EXTRACTION", description=_extracted_desc)

        return schema
