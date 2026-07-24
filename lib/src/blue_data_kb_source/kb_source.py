from __future__ import annotations

import asyncio
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
from blue_data_kb_source.search.indexer import KBSearchIndexer
from blue_data_kb_source.types import (
    DataSearchResult,
    EnrichmentResult,
    KBError,
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

    def __init__(
        self,
        *args: Any,
        enable_search: bool = False,
        vector_source: Any = None,
        embedding_fn: Callable[..., Any] | None = None,
        vector_dim: int = 1536,
        corpus_dir: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._notifier = ChangeNotifier()
        self._provenance = ProvenanceTracker(self)
        self._schema = SchemaManager(self, self._notifier)

        self._search_indexer: KBSearchIndexer | None = None
        if enable_search or vector_source is not None:
            self._search_indexer = KBSearchIndexer(
                self,
                vector_source=vector_source,
                embedding_fn=embedding_fn,
                vector_dim=vector_dim,
                corpus_dir=corpus_dir,
            )
            self._notifier.add_global_hook(self._search_indexer.on_change)

        self._knowledge = KnowledgeManager(
            self, self._provenance, self._notifier, self._schema, self._search_indexer
        )
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

    async def remove_is_subclass_of(self, database: str, child_id: str, parent_id: str) -> None:
        await self._schema.remove_is_subclass_of(database, child_id, parent_id)

    async def add_is_subclass_of(self, database: str, child_id: str, parent_id: str) -> None:
        await self._schema.add_is_subclass_of(database, child_id, parent_id)

    async def fetch_database_schema(self, database: str) -> DataSchema:
        return await self._schema.fetch_database_schema(database)

    async def import_database_schema(self, database: str, schema_json: dict) -> None:
        await self._schema.import_database_schema(database, schema_json)

    async def search_schema(
        self,
        database: str,
        query: str,
        k: int = 5,
        mode: str = "keyword",
        type_filter: str | list[str] | None = None,
    ) -> list[SchemaSearchResult]:
        if self._search_indexer is not None:
            return self._search_indexer.search_schema(database, query, k=k, mode=mode, type_filter=type_filter)
        return await self._schema.find_schema(database, query, type_filter=type_filter)

    async def search_data(
        self,
        database: str,
        query: str,
        k: int = 5,
        mode: str = "keyword",
        type_filter: str | list[str] | None = None,
    ) -> list[DataSearchResult]:
        if self._search_indexer is not None:
            return self._search_indexer.search_data(database, query, k=k, mode=mode, type_filter=type_filter)
        return await self._knowledge.find_data(database, query, type_filter=type_filter)

    def reindex(self, database: str) -> None:
        if self._search_indexer is None:
            raise KBError("reindex requires enable_search=True or vector_source at init")
        self._search_indexer.reindex_all(database)

    @property
    def has_semantic_search(self) -> bool:
        return self._search_indexer is not None and self._search_indexer.has_semantic

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
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, dict, Any], None] | None = None,
    ) -> list[dict] | str:
        return await self._knowledge.answer(database, question, model, natural_language, on_tool_call, on_tool_result)

    async def validate(
        self,
        database: str,
        statement: str,
        model: str = "gpt-4o",
        natural_language: bool = False,
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, dict, Any], None] | None = None,
    ) -> ValidationResult | str:
        return await self._knowledge.validate(database, statement, model, natural_language, on_tool_call, on_tool_result)

    async def enrich(
        self,
        database: str,
        query: str,
        depth: int = 2,
        model: str = "gpt-4o",
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, dict, Any], None] | None = None,
    ) -> list[EnrichmentResult]:
        return await self._knowledge.enrich(database, query, depth, model, on_tool_call, on_tool_result)

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
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, dict, Any], None] | None = None,
    ) -> None:
        await self._extraction.import_text(database, text, queries, model, source_id, on_tool_call, on_tool_result)

    # ------------------------------------------------------------------ #
    # Meta-schema (describes the KB graph structure itself)               #
    # ------------------------------------------------------------------ #

    async def fetch_meta_schema(self) -> DataSchema:
        return await self._schema.fetch_meta_schema()

    # ------------------------------------------------------------------ #
    # execute_query — synchronous KB operation dispatch                   #
    # ------------------------------------------------------------------ #

    def execute_query(self, query, database=None, collection=None, optional_properties=None):
        """Execute a KB operation.

        query must be a dict with:
            op – one of:

            Schema:
                "KB.DECLARE_CLASS"             name, description, class_id (opt)
                "KB.DECLARE_RELATION"          name, description, domain (opt), range (opt), is_symmetric (opt), relation_id (opt)
                "KB.DECLARE_CLASS_PROPERTY"    name, description, class_id (opt), property_id (opt)
                "KB.DECLARE_RELATION_PROPERTY" name, description, relation_id (opt), property_id (opt)
                "KB.UPDATE_CLASS"              class_id, name (opt), description (opt)
                "KB.UPDATE_RELATION"           relation_id, name (opt), description (opt)
                "KB.UPDATE_PROPERTY"           property_id, name (opt), description (opt)
                "KB.DELETE_CLASS"              class_id
                "KB.DELETE_RELATION"           relation_id
                "KB.DELETE_PROPERTY"           property_id
                "KB.ADD_SUBCLASS"              child_id, parent_id
                "KB.REMOVE_SUBCLASS"           child_id, parent_id
                "KB.GET_SCHEMA"                –
                "KB.SEARCH_SCHEMA"             query

            Knowledge:
                "KB.ADD_ENTITY"                name, class_id (opt), description (opt), entity_id (opt), text (opt), source_id (opt)
                "KB.ADD_FACT"                  head_entity_id (opt), relation_id (opt), tail_entity_id (opt), name (opt), description (opt), fact_id (opt), text (opt), source_id (opt)
                "KB.ADD_ENTITY_PROPERTY"       entity_id, property_id, value, literal_type (opt), name (opt), description (opt), text (opt), source_id (opt)
                "KB.ADD_FACT_PROPERTY"         fact_id, property_id, value, literal_type (opt), name (opt), description (opt), text (opt), source_id (opt)
                "KB.UPDATE_ENTITY"             entity_id, name (opt), description (opt)
                "KB.UPDATE_FACT"               fact_id, name (opt), description (opt)
                "KB.UPDATE_ENTITY_PROPERTY"    entity_id, property_id, value (opt), name (opt), description (opt)
                "KB.UPDATE_FACT_PROPERTY"      fact_id, property_id, value (opt), name (opt), description (opt)
                "KB.DELETE_ENTITY"             entity_id, cascade (opt, default False)
                "KB.DELETE_FACT"               fact_id
                "KB.DELETE_ENTITY_PROPERTY"    entity_id, property_id
                "KB.DELETE_FACT_PROPERTY"      fact_id, property_id
                "KB.GET_DATA"                  include_provenance (opt), compact (opt)

            Provenance:
                "KB.ADD_SOURCE"                source, name (opt), description (opt), node_id (opt)
                "KB.ADD_EXTRACTION"            text, name (opt), description (opt), source_id (opt), node_id (opt)
                "KB.LINK_EXTRACTION"           element_id, extraction_id
                "KB.UPDATE_SOURCE"             source_id, name (opt), description (opt)
                "KB.DELETE_SOURCE"             source_id

        GRAPH.* ops are also accepted and forwarded to the GraphSource implementation.
        """
        if not isinstance(query, dict):
            raise ValueError("KBSource.execute_query expects query as a dict with 'op' and operation-specific fields")

        op = (query.get("op") or "").upper()

        def _r(coro):
            return asyncio.run(coro)

        # -- Schema --------------------------------------------------------

        if op == "KB.DECLARE_CLASS":
            return _r(self.declare_class(
                database,
                name=query["name"],
                description=query["description"],
                class_id=query.get("class_id"),
            ))

        if op == "KB.DECLARE_RELATION":
            return _r(self.declare_relation_type(
                database,
                name=query["name"],
                description=query["description"],
                is_symmetric=query.get("is_symmetric", False),
                relation_id=query.get("relation_id"),
                domain=query.get("domain"),
                range_=query.get("range"),
            ))

        if op == "KB.DECLARE_CLASS_PROPERTY":
            return _r(self.declare_class_property(
                database,
                name=query["name"],
                description=query["description"],
                property_id=query.get("property_id"),
                class_id=query.get("class_id"),
            ))

        if op == "KB.DECLARE_RELATION_PROPERTY":
            return _r(self.declare_relation_property(
                database,
                name=query["name"],
                description=query["description"],
                property_id=query.get("property_id"),
                relation_id=query.get("relation_id"),
            ))

        if op == "KB.UPDATE_CLASS":
            _r(self.update_class(database, query["class_id"], query.get("name"), query.get("description")))
            return None

        if op == "KB.UPDATE_RELATION":
            _r(self.update_relation(database, query["relation_id"], query.get("name"), query.get("description")))
            return None

        if op == "KB.UPDATE_PROPERTY":
            _r(self.update_property(database, query["property_id"], query.get("name"), query.get("description")))
            return None

        if op == "KB.DELETE_CLASS":
            _r(self.delete_class(database, query["class_id"]))
            return None

        if op == "KB.DELETE_RELATION":
            _r(self.delete_relation(database, query["relation_id"]))
            return None

        if op == "KB.DELETE_PROPERTY":
            _r(self.delete_property(database, query["property_id"]))
            return None

        if op == "KB.ADD_SUBCLASS":
            _r(self.add_is_subclass_of(database, query["child_id"], query["parent_id"]))
            return None

        if op == "KB.REMOVE_SUBCLASS":
            _r(self.remove_is_subclass_of(database, query["child_id"], query["parent_id"]))
            return None

        if op == "KB.GET_SCHEMA":
            return _r(self.fetch_database_schema(database))

        if op == "KB.SEARCH_SCHEMA":
            return _r(self.search_schema(
                database,
                query["query"],
                k=query.get("k", 5),
                mode=query.get("mode", "keyword"),
                type_filter=query.get("type_filter"),
            ))

        if op == "KB.SEARCH_DATA":
            return _r(self.search_data(
                database,
                query["query"],
                k=query.get("k", 5),
                mode=query.get("mode", "keyword"),
                type_filter=query.get("type_filter"),
            ))

        # -- Knowledge -----------------------------------------------------

        if op == "KB.ADD_ENTITY":
            return _r(self.add_entity(
                database,
                name=query["name"],
                description=query.get("description"),
                entity_id=query.get("entity_id"),
                class_id=query.get("class_id"),
                text=query.get("text"),
                source_id=query.get("source_id"),
            ))

        if op == "KB.ADD_FACT":
            return _r(self.add_fact(
                database,
                name=query.get("name"),
                description=query.get("description"),
                fact_id=query.get("fact_id"),
                head_entity_id=query.get("head_entity_id"),
                tail_entity_id=query.get("tail_entity_id"),
                relation_id=query.get("relation_id"),
                text=query.get("text"),
                source_id=query.get("source_id"),
            ))

        if op == "KB.ADD_ENTITY_PROPERTY":
            return _r(self.add_entity_property_value(
                database,
                value=query["value"],
                name=query.get("name"),
                description=query.get("description"),
                literal_type=query.get("literal_type"),
                entity_id=query.get("entity_id"),
                property_id=query.get("property_id"),
                text=query.get("text"),
                source_id=query.get("source_id"),
            ))

        if op == "KB.ADD_FACT_PROPERTY":
            return _r(self.add_fact_property_value(
                database,
                value=query["value"],
                name=query.get("name"),
                description=query.get("description"),
                literal_type=query.get("literal_type"),
                fact_id=query.get("fact_id"),
                property_id=query.get("property_id"),
                text=query.get("text"),
                source_id=query.get("source_id"),
            ))

        if op == "KB.UPDATE_ENTITY":
            _r(self.update_entity(database, query["entity_id"], query.get("name"), query.get("description")))
            return None

        if op == "KB.UPDATE_FACT":
            _r(self.update_fact(database, query["fact_id"], query.get("name"), query.get("description")))
            return None

        if op == "KB.UPDATE_ENTITY_PROPERTY":
            _r(self.update_entity_property_value(
                database,
                entity_id=query["entity_id"],
                property_id=query["property_id"],
                value=query.get("value"),
                name=query.get("name"),
                description=query.get("description"),
            ))
            return None

        if op == "KB.UPDATE_FACT_PROPERTY":
            _r(self.update_fact_property_value(
                database,
                fact_id=query["fact_id"],
                property_id=query["property_id"],
                value=query.get("value"),
                name=query.get("name"),
                description=query.get("description"),
            ))
            return None

        if op == "KB.DELETE_ENTITY":
            _r(self.delete_entity(database, query["entity_id"], cascade=query.get("cascade", False)))
            return None

        if op == "KB.DELETE_FACT":
            _r(self.delete_fact(database, query["fact_id"]))
            return None

        if op == "KB.DELETE_ENTITY_PROPERTY":
            _r(self.delete_entity_property_value(database, query["entity_id"], query["property_id"]))
            return None

        if op == "KB.DELETE_FACT_PROPERTY":
            _r(self.delete_fact_property_value(database, query["fact_id"], query["property_id"]))
            return None

        if op == "KB.GET_DATA":
            return _r(self.fetch_database_data(
                database,
                include_provenance=query.get("include_provenance", False),
                compact=query.get("compact", False),
            ))

        # -- Provenance ----------------------------------------------------

        if op == "KB.ADD_SOURCE":
            return _r(self.add_source(
                database,
                source=query["source"],
                name=query.get("name"),
                description=query.get("description"),
                node_id=query.get("node_id"),
            ))

        if op == "KB.ADD_EXTRACTION":
            return _r(self.add_extraction(
                database,
                text=query["text"],
                name=query.get("name"),
                description=query.get("description"),
                node_id=query.get("node_id"),
                source_id=query.get("source_id"),
            ))

        if op == "KB.LINK_EXTRACTION":
            _r(self.add_extracted_from(database, query["element_id"], query["extraction_id"]))
            return None

        if op == "KB.UPDATE_SOURCE":
            _r(self.update_source(database, query["source_id"], query.get("name"), query.get("description")))
            return None

        if op == "KB.DELETE_SOURCE":
            _r(self.delete_source(database, query["source_id"]))
            return None

        # -- Fall through to GRAPH.* ops ----------------------------------

        if op.startswith("GRAPH."):
            return super().execute_query(query, database=database, collection=collection, optional_properties=optional_properties)

        raise ValueError(
            f"Unsupported KBSource operation: {op!r}. "
            "Supported KB ops: KB.DECLARE_CLASS, KB.DECLARE_RELATION, KB.DECLARE_CLASS_PROPERTY, "
            "KB.DECLARE_RELATION_PROPERTY, KB.UPDATE_CLASS, KB.UPDATE_RELATION, KB.UPDATE_PROPERTY, "
            "KB.DELETE_CLASS, KB.DELETE_RELATION, KB.DELETE_PROPERTY, KB.ADD_SUBCLASS, KB.REMOVE_SUBCLASS, "
            "KB.GET_SCHEMA, KB.SEARCH_SCHEMA, KB.ADD_ENTITY, KB.ADD_FACT, KB.ADD_ENTITY_PROPERTY, "
            "KB.ADD_FACT_PROPERTY, KB.UPDATE_ENTITY, KB.UPDATE_FACT, KB.UPDATE_ENTITY_PROPERTY, "
            "KB.UPDATE_FACT_PROPERTY, KB.DELETE_ENTITY, KB.DELETE_FACT, KB.DELETE_ENTITY_PROPERTY, "
            "KB.DELETE_FACT_PROPERTY, KB.GET_DATA, KB.ADD_SOURCE, KB.ADD_EXTRACTION, KB.LINK_EXTRACTION, "
            "KB.UPDATE_SOURCE, KB.DELETE_SOURCE. Also accepts GRAPH.* ops."
        )

    def execute_query_nl(
        self,
        query: str,
        database: str | None = None,
        collection: str | None = None,
        schema: dict | None = None,
        llm_fn=None,
        optional_properties: dict | None = None,
    ) -> list[dict]:
        """Answer a natural-language question against the KB.

        Delegates to ``answer`` with ``natural_language=False`` so the result is
        always a structured ``list[dict]``.  The model is taken from
        ``optional_properties["model"]``; defaults to ``"gpt-4o"``.
        ``llm_fn``, ``collection``, and ``schema`` are ignored.
        """
        model = (optional_properties or {}).get("model", "gpt-4o")
        return asyncio.run(self.answer(database, question=query, model=model, natural_language=False))
