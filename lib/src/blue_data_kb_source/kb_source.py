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
    META_DATABASE,
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
        self._schema = SchemaManager(self, self._notifier)
        self._knowledge = KnowledgeManager(self, self._provenance, self._notifier, self._schema)
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

    async def update_class(
        self,
        database: str,
        class_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        await self._schema.update_class(database, class_id, name, description)

    async def update_relation(
        self,
        database: str,
        relation_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        await self._schema.update_relation(database, relation_id, name, description)

    async def update_property(
        self,
        database: str,
        property_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        await self._schema.update_property(database, property_id, name, description)

    async def delete_class(self, database: str, class_id: str) -> None:
        await self._schema.delete_class(database, class_id)

    async def delete_relation(self, database: str, relation_id: str) -> None:
        await self._schema.delete_relation(database, relation_id)

    async def delete_property(self, database: str, property_id: str) -> None:
        await self._schema.delete_property(database, property_id)

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

    async def update_entity(
        self,
        database: str,
        entity_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        await self._knowledge.update_entity(database, entity_id, name, description)

    async def update_fact(
        self,
        database: str,
        fact_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        await self._knowledge.update_fact(database, fact_id, name, description)

    async def update_entity_property_value(
        self,
        database: str,
        entity_id: str,
        property_id: str,
        value: str | int | float | bool | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        await self._knowledge.update_entity_property_value(
            database, entity_id, property_id, value, name, description
        )

    async def update_fact_property_value(
        self,
        database: str,
        fact_id: str,
        property_id: str,
        value: str | int | float | bool | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        await self._knowledge.update_fact_property_value(
            database, fact_id, property_id, value, name, description
        )

    async def delete_entity(self, database: str, entity_id: str, cascade: bool = False) -> None:
        await self._knowledge.delete_entity(database, entity_id, cascade)

    async def delete_fact(self, database: str, fact_id: str) -> None:
        await self._knowledge.delete_fact(database, fact_id)

    async def delete_entity_property_value(
        self, database: str, entity_id: str, property_id: str
    ) -> None:
        await self._knowledge.delete_entity_property_value(database, entity_id, property_id)

    async def delete_fact_property_value(
        self, database: str, fact_id: str, property_id: str
    ) -> None:
        await self._knowledge.delete_fact_property_value(database, fact_id, property_id)

    async def fetch_database_data(
        self,
        database: str,
        include_provenance: bool = False,
        compact: bool = False,
    ) -> dict | list:
        return await self._knowledge.fetch_database_data(database, include_provenance, compact)

    async def answer(
        self,
        database: str,
        question: str,
        model: str = "gpt-4o",
        natural_language: bool = False,
    ) -> list[dict] | str:
        return await self._knowledge.answer(database, question, model, natural_language)

    async def validate(
        self,
        database: str,
        statement: str,
        model: str = "gpt-4o",
        natural_language: bool = False,
    ) -> ValidationResult | str:
        return await self._knowledge.validate(database, statement, model, natural_language)

    async def enrich(self, database: str, query: str, depth: int = 2, model: str = "gpt-4o") -> list[EnrichmentResult]:
        return await self._knowledge.enrich(database, query, depth, model)

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

    async def update_source(
        self,
        database: str,
        source_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        await self._provenance.update_source(database, source_id, name, description)

    async def delete_source(self, database: str, source_id: str) -> None:
        await self._provenance.delete_source(database, source_id)

    # ------------------------------------------------------------------ #
    # Change hooks → ChangeNotifier                                        #
    # ------------------------------------------------------------------ #

    def add_change_hook(self, database: str, fn: Callable[[dict], None]) -> None:
        """Register *fn* to be called after every schema or instance-level write to *database*.

        *fn* receives a dict with at minimum ``"type"``, ``"database"``, ``"id"``.
        Schema events fire only when a new element is created; idempotent repeats do not re-fire.

        Schema event types and extra keys:
        ``class_declared``             — ``name``, ``description``
        ``relation_declared``          — ``name``, ``description``, ``is_symmetric``,
                                         ``domain`` (list), ``range`` (list)
        ``class_property_declared``    — ``name``, ``description``, ``class_id``, ``class_name``
        ``relation_property_declared`` — ``name``, ``description``, ``relation_id``, ``relation_name``
        ``class_subclass``             — ``child_id``, ``child_name``, ``parent_id``, ``parent_name``
        ``relation_subclass``          — ``child_id``, ``child_name``, ``parent_id``, ``parent_name``

        Instance event types and extra keys:
        ``entity_added``          — ``name``, ``class_id``, ``class_name``
        ``fact_added``            — ``name``, ``head/tail_entity_id/name``, ``relation_id/name``
        ``entity_property_added`` — ``entity_id``, ``entity_name``, ``property_id/name``, ``value``
        ``fact_property_added``   — ``fact_id``, ``fact_name``, ``property_id/name``, ``value``,
                                    ``head/tail_entity_id/name``, ``relation_id``, ``relation_name``
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
    # Meta-schema (describes the KB graph structure itself)               #
    # ------------------------------------------------------------------ #

    META_DATABASE = META_DATABASE

    async def fetch_meta_schema(self) -> DataSchema:
        """Return a DataSchema built from the persisted __meta__ database."""
        return await self._schema.fetch_meta_schema()
