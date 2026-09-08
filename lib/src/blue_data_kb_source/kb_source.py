from __future__ import annotations

import asyncio
from abc import ABC
from typing import Any, Callable, Literal

from blue_data.core.schema import DataSchema
from blue_data.source.graph.graph_source import GraphSource
from blue_data.source.source import DataSource

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
from blue_data_kb_source.ingestion.manager import IngestionManager
from blue_data_kb_source.knowledge.manager import KnowledgeManager
from blue_data_kb_source.notification.notifier import ChangeNotifier
from blue_data_kb_source.provenance.tracker import ProvenanceTracker
from blue_data_kb_source.resolution.resolver import NodeResolver
from blue_data_kb_source.schema.manager import SchemaManager
from blue_data_kb_source.search.indexer import KBSearchIndexer
from blue_data_kb_source.types import (
    AssertResult,
    DataSearchResult,
    EnrichmentResult,
    IngestReport,
    KBError,
    SchemaSearchResult,
    ValidationResult,
)


def _build_embedding_fn(cfg: dict[str, Any]) -> Callable[[str], list[float]]:
    """Build an embedding callable from properties["search"]["embedding"] config
    (`{"provider": ..., "model": ...}`) — used when a caller supplies that
    declarative config instead of an already-live `embedding_fn` callable."""
    provider = cfg.get("provider", "openai")
    if provider == "openai":
        model = cfg.get("model", "text-embedding-3-small")
        _client: list[Any] = []  # lazy singleton, created on first call

        def _embed(text: str) -> list[float]:
            if not _client:
                import openai

                _client.append(openai.OpenAI())
            return _client[0].embeddings.create(input=text, model=model).data[0].embedding

        return _embed
    raise ValueError(f"Unsupported embedding provider: {provider!r}")


class KBSource(GraphSource, ABC):
    """Abstract knowledge-base source built on top of GraphSource.

    Provides typed builders for the KB schema:
      Nodes  — CLASS, RELATION, PROPERTY, ENTITY, FACT, LITERAL, SOURCE, EXTRACTION
      Edges  — IS_SUBCLASS_OF, IS_INSTANCE_OF, DEFINE_PROPERTY, IS_VALUE_OF,
               HAS_PROPERTY, REPRESENTS, HEAD, TAIL, DOMAIN, RANGE, EXTRACTED_FROM

    Backend is selected by mixing with a concrete GraphSource via Python MRO.
    Ready-made mixins ship alongside this module — MemoryKBSource (memory_kb_source.py),
    FileKBSource (file_kb_source.py), NEO4JKBSource (neo4j/neo4j_kb_source.py, imported
    separately so `import blue_data_kb_source` never requires the `neo4j` package).
    Each overrides `_initialize_connection_properties` to report its own protocol
    (`kb/memory`, `kb/file`, `kb/neo4j`) rather than inheriting the mixed-in
    GraphSource backend's `graph/*` protocol. Callers that pick a backend from a
    config value (a string, not a fixed choice at write-time) should construct via
    `DataSource.instantiate("blue_data_kb_source.file_kb_source.FileKBSource", ...)`
    instead of importing the class directly — see platform/cli/config.py and
    platform/mcp/config.py.

    Subclasses must implement the abstract GraphSource primitives:
    add_node, get_node, delete_node, add_edge, delete_edge, get_neighbors.

    Constructor matches DataSource's own `(name, properties=None)` exactly — no
    extra keyword arguments. Everything backend/search-specific (graph connection,
    BM25/semantic search mode, vector backend, embedding provider) is config under
    `properties`, e.g. `properties["connection"]["directory"]` for FileKBSource,
    `properties["search"]["mode"]` / `["vector"]` / `["embedding"]` for search. See
    design/06-library.md for the full properties shape.
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

    def __init__(self, name: str, properties: dict[str, Any] | None = None) -> None:
        super().__init__(name, properties=properties)
        self._notifier = ChangeNotifier()
        self._provenance = ProvenanceTracker(self, self._notifier)

        # Search/vector/embedding config lives entirely under properties["search"] —
        # matching DataSource.__init__(name, properties=None): no extra constructor
        # kwargs. This makes a KBSource fully reconstructible from nothing but
        # DataSource.instantiate(class_path, name, properties=stored_properties),
        # e.g. from the CLI's registry — see design/06-library.md for the shape.
        # "vector_source"/"embedding_fn" may also be passed as already-live objects
        # nested in properties["search"] for in-memory/programmatic use (they're
        # never meant to be persisted that way — only the declarative "vector"/
        # "embedding" config sub-dicts are JSON-safe).
        search_cfg = self.properties.get("search") or {}
        mode = search_cfg.get("mode", "none")
        vector_source = search_cfg.get("vector_source")
        embedding_fn = search_cfg.get("embedding_fn")

        self._search_indexer: KBSearchIndexer | None = None
        if mode in ("bm25", "semantic") or vector_source is not None:
            if mode == "semantic" and vector_source is None:
                vcfg = search_cfg.get("vector")
                if vcfg:
                    vector_source = DataSource.instantiate(
                        vcfg["_class"], vcfg.get("name", "kb_vectors"), properties=vcfg
                    )
            if embedding_fn is None and search_cfg.get("embedding"):
                embedding_fn = _build_embedding_fn(search_cfg["embedding"])

            self._search_indexer = KBSearchIndexer(
                self,
                vector_source=vector_source,
                embedding_fn=embedding_fn,
                vector_dim=search_cfg.get("vector_dim", 1536),
                corpus_dir=search_cfg.get("bm25_index_dir"),
            )
            self._notifier.add_global_hook(self._search_indexer.on_change)

        # SchemaManager is built after the search indexer so its expansion engine
        # (SchemaExpansionEngine) can use it for search-mode dedup, same as
        # KnowledgeManager/ExtractionEngine below.
        self._schema = SchemaManager(self, self._notifier, self._search_indexer)

        self._knowledge = KnowledgeManager(
            self, self._provenance, self._notifier, self._schema, self._search_indexer
        )
        self._extraction = ExtractionEngine(
            self, self._schema, self._knowledge, self._provenance, self._search_indexer
        )
        self._resolver = NodeResolver(self, self._search_indexer)
        self._ingestion = IngestionManager(
            self, self._schema, self._knowledge, self._provenance, self._resolver
        )

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
        *,
        domain: str | list[str],
        range: str | list[str],
    ) -> str:
        return await self._schema.declare_relation_type(
            database, name, description, is_symmetric, relation_id, domain=domain, range=range
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

    async def attach_property(self, database: str, target_id: str, property_id: str) -> None:
        await self._schema.attach_property(database, target_id, property_id)

    async def expand_schema(
        self,
        database: str,
        text: str,
        depth: int = 1,
        reference: Literal["llm", "mcp"] = "llm",
        mcp_servers: list[dict[str, Any]] | None = None,
        dedup_mode: Literal["search", "llm", "none"] = "search",
        model: str = "gpt-4o",
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None = None,
        max_turns: int = 25,
    ) -> None:
        await self._schema.expand_schema(
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

    async def attach_relation_domain(self, database: str, relation_id: str, class_id: str) -> None:
        await self._schema.attach_relation_domain(database, relation_id, class_id)

    async def attach_relation_range(self, database: str, relation_id: str, class_id: str) -> None:
        await self._schema.attach_relation_range(database, relation_id, class_id)

    async def get_class_ancestors(
        self,
        database: str,
        class_id: str,
        max_hops: int | None = 5,
    ) -> list[dict]:
        return await self._schema.get_class_ancestors(database, class_id, max_hops)

    async def get_class_descendants(
        self,
        database: str,
        class_id: str,
        max_hops: int | None = 5,
    ) -> list[dict]:
        return await self._schema.get_class_descendants(database, class_id, max_hops)

    async def get_relation_ancestors(
        self,
        database: str,
        relation_id: str,
        max_hops: int | None = 5,
    ) -> list[dict]:
        return await self._schema.get_relation_ancestors(database, relation_id, max_hops)

    async def get_relation_descendants(
        self,
        database: str,
        relation_id: str,
        max_hops: int | None = 5,
    ) -> list[dict]:
        return await self._schema.get_relation_descendants(database, relation_id, max_hops)

    async def get_class_entities(
        self,
        database: str,
        class_id: str,
        limit: int | None = 10,
    ) -> list[dict]:
        return await self._schema.get_class_entities(database, class_id, limit)

    async def get_relation_domain_range(
        self,
        database: str,
        relation_id: str,
    ) -> dict[str, list[dict]]:
        return await self._schema.get_relation_domain_range(database, relation_id)

    async def get_allowed_relations_between_classes(
        self,
        database: str,
        head_class_id: str,
        tail_class_id: str,
        expand: bool = True,
        max_hops: int | None = 5,
    ) -> list[dict]:
        return await self._schema.get_allowed_relations_between_classes(
            database, head_class_id, tail_class_id, expand, max_hops
        )

    async def find_class(self, database: str, name: str) -> dict | None:
        return await self._schema.find_class(database, name)

    async def find_relation_type(self, database: str, name: str) -> dict | None:
        return await self._schema.find_relation_type(database, name)

    async def find_property(self, database: str, name: str) -> dict | None:
        return await self._schema.find_property(database, name)

    async def fetch_database_schema(self, database: str) -> DataSchema:
        return await self._schema.fetch_database_schema(database)

    async def export_database_schema(self, database: str) -> dict:
        return await self._schema.export_database_schema(database)

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
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "text_span",
    ) -> str:
        return await self._knowledge.add_entity(
            database, name, description, entity_id, class_id, text, source_id,
            confidence, extraction_type,
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
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "text_span",
    ) -> str:
        return await self._knowledge.add_fact(
            database, name, description, fact_id,
            head_entity_id, tail_entity_id, relation_id, text, source_id,
            confidence, extraction_type,
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
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "text_span",
    ) -> str:
        return await self._knowledge.add_entity_property_value(
            database, value, name, description, literal_type, entity_id, property_id, text, source_id,
            confidence, extraction_type,
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
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "text_span",
    ) -> str:
        return await self._knowledge.add_fact_property_value(
            database, value, name, description, literal_type, fact_id, property_id, text, source_id,
            confidence, extraction_type,
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

    async def attach_entity_class(self, database: str, entity_id: str, class_id: str) -> None:
        await self._knowledge.attach_entity_class(database, entity_id, class_id)

    async def fetch_database_data(
        self,
        database: str,
        include_provenance: bool = False,
        compact: bool = False,
        detailed: bool = False,
    ) -> dict | list:
        return await self._knowledge.fetch_database_data(database, include_provenance, compact, detailed)

    async def export_database_data(self, database: str, include_provenance: bool = False) -> dict:
        return await self._knowledge.export_database_data(database, include_provenance)

    async def import_database_data(self, database: str, data: dict) -> None:
        await self._knowledge.import_database_data(database, data)

    async def get_metadata(self, database: str, node_id: str) -> dict:
        return await self._knowledge.get_metadata(database, node_id)

    async def get_entity_classes(self, database: str, entity_id: str) -> list[dict]:
        return await self._knowledge.get_entity_classes(database, entity_id)

    async def get_fact_parts(self, database: str, fact_id: str) -> dict:
        return await self._knowledge.get_fact_parts(database, fact_id)

    async def get_property_values(self, database: str, owner_id: str) -> list[dict]:
        return await self._knowledge.get_property_values(database, owner_id)

    async def is_fact_allowed(
        self,
        database: str,
        head_entity_id: str,
        relation_id: str,
        tail_entity_id: str,
        max_hops: int | None = 5,
    ) -> dict:
        return await self._knowledge.is_fact_allowed(
            database, head_entity_id, relation_id, tail_entity_id, max_hops
        )

    async def is_property_allowed(
        self,
        database: str,
        owner_id: str,
        property_id: str,
        max_hops: int | None = 5,
    ) -> dict:
        return await self._knowledge.is_property_allowed(database, owner_id, property_id, max_hops)

    async def find_entity(self, database: str, name: str) -> dict | None:
        return await self._knowledge.find_entity(database, name)

    async def find_fact(
        self,
        database: str,
        head_entity_id: str,
        relation_id: str,
        tail_entity_id: str,
    ) -> dict | None:
        return await self._knowledge.find_fact(
            database, head_entity_id, relation_id, tail_entity_id
        )

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
        return await self._knowledge.answer(
            database, question, model, natural_language, include_metadata,
            on_tool_call, on_tool_result, max_turns=max_turns,
        )

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
        return await self._knowledge.validate(
            database, statement, model, natural_language, include_metadata,
            on_tool_call, on_tool_result, max_turns=max_turns,
        )

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
        return await self._knowledge.enrich(
            database, query, depth, model, on_tool_call, on_tool_result, max_turns=max_turns
        )

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
        original: str | None = None,
    ) -> str:
        return await self._provenance.add_source(database, source, name, description, node_id, original)

    async def add_extraction(
        self,
        database: str,
        text: str,
        name: str | None = None,
        description: str | None = None,
        node_id: str | None = None,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "text_span",
        element_id: str | None = None,
    ) -> str:
        """Add an EXTRACTION node.

        Optionally wires EXTRACTED_FROM to a SOURCE (source_id) and/or to an
        existing entity/fact/literal (element_id), so a single call can both
        create the extraction and link it in place.
        """
        return await self._provenance.add_extraction(
            database, text, name, description, node_id, source_id, confidence,
            extraction_type, element_id
        )

    async def attach_extraction(
        self, database: str, element_id: str, extraction_id: str
    ) -> None:
        await self._provenance.attach_extraction(database, element_id, extraction_id)

    async def add_entity_extraction(
        self,
        database: str,
        entity_id: str,
        text: str,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "text_span",
    ) -> str:
        return await self._knowledge.add_entity_extraction(
            database, entity_id, text, source_id, confidence, extraction_type
        )

    async def add_fact_extraction(
        self,
        database: str,
        fact_id: str,
        text: str,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "text_span",
    ) -> str:
        return await self._knowledge.add_fact_extraction(
            database, fact_id, text, source_id, confidence, extraction_type
        )

    async def add_entity_property_value_extraction(
        self,
        database: str,
        entity_id: str,
        property_id: str,
        text: str,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "text_span",
    ) -> str:
        return await self._knowledge.add_entity_property_value_extraction(
            database, entity_id, property_id, text, source_id, confidence, extraction_type
        )

    async def add_fact_property_value_extraction(
        self,
        database: str,
        fact_id: str,
        property_id: str,
        text: str,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "text_span",
    ) -> str:
        return await self._knowledge.add_fact_property_value_extraction(
            database, fact_id, property_id, text, source_id, confidence, extraction_type
        )

    # ------------------------------------------------------------------ #
    # Ingest operations -> IngestionManager                                #
    # ------------------------------------------------------------------ #

    async def assert_class(
        self,
        database: str,
        name: str,
        description: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "assertion",
        schema_creation_mode: Literal["llm", "none"] = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        return await self._ingestion.assert_class(
            database, name, description,
            source_id=source_id, confidence=confidence, extraction_type=extraction_type,
            schema_creation_mode=schema_creation_mode, model=model,
        )

    async def assert_relation_type(
        self,
        database: str,
        name: str,
        domain: str | list[str],
        range: str | list[str],
        description: str | None = None,
        is_symmetric: bool = False,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "assertion",
        schema_creation_mode: Literal["llm", "none"] = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        return await self._ingestion.assert_relation_type(
            database, name, domain, range, description, is_symmetric,
            source_id=source_id, confidence=confidence, extraction_type=extraction_type,
            schema_creation_mode=schema_creation_mode, model=model,
        )

    async def assert_class_property(
        self,
        database: str,
        name: str,
        class_name: str,
        description: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "assertion",
        schema_creation_mode: Literal["llm", "none"] = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        return await self._ingestion.assert_class_property(
            database, name, class_name, description,
            source_id=source_id, confidence=confidence, extraction_type=extraction_type,
            schema_creation_mode=schema_creation_mode, model=model,
        )

    async def assert_relation_property(
        self,
        database: str,
        name: str,
        relation_name: str,
        description: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "assertion",
        schema_creation_mode: Literal["llm", "none"] = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        return await self._ingestion.assert_relation_property(
            database, name, relation_name, description,
            source_id=source_id, confidence=confidence, extraction_type=extraction_type,
            schema_creation_mode=schema_creation_mode, model=model,
        )

    async def assert_subclass(
        self,
        database: str,
        child: str,
        parent: str,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "assertion",
        schema_creation_mode: Literal["llm", "none"] = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        return await self._ingestion.assert_subclass(
            database, child, parent,
            source_id=source_id, confidence=confidence, extraction_type=extraction_type,
            schema_creation_mode=schema_creation_mode, model=model,
        )

    async def assert_subrelation(
        self,
        database: str,
        child: str,
        parent: str,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "assertion",
        schema_creation_mode: Literal["llm", "none"] = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        return await self._ingestion.assert_subrelation(
            database, child, parent,
            source_id=source_id, confidence=confidence, extraction_type=extraction_type,
            schema_creation_mode=schema_creation_mode, model=model,
        )

    async def assert_entity(
        self,
        database: str,
        name: str,
        class_name: str | None = None,
        description: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "assertion",
        schema_creation_mode: Literal["llm", "none"] = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        return await self._ingestion.assert_entity(
            database, name, class_name, description,
            source_id=source_id, confidence=confidence, extraction_type=extraction_type,
            schema_creation_mode=schema_creation_mode, model=model,
        )

    async def assert_fact(
        self,
        database: str,
        head: str,
        relation: str,
        tail: str,
        name: str | None = None,
        description: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "assertion",
        schema_creation_mode: Literal["llm", "none"] = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        return await self._ingestion.assert_fact(
            database, head, relation, tail, name, description,
            source_id=source_id, confidence=confidence, extraction_type=extraction_type,
            schema_creation_mode=schema_creation_mode, model=model,
        )

    async def assert_entity_property(
        self,
        database: str,
        entity: str,
        property: str,
        value: str | int | float | bool,
        literal_type: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "assertion",
        schema_creation_mode: Literal["llm", "none"] = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        return await self._ingestion.assert_entity_property(
            database, entity, property, value, literal_type,
            source_id=source_id, confidence=confidence, extraction_type=extraction_type,
            schema_creation_mode=schema_creation_mode, model=model,
        )

    async def assert_fact_property(
        self,
        database: str,
        head: str,
        relation: str,
        tail: str,
        property: str,
        value: str | int | float | bool,
        literal_type: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "assertion",
        schema_creation_mode: Literal["llm", "none"] = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        return await self._ingestion.assert_fact_property(
            database, head, relation, tail, property, value, literal_type,
            source_id=source_id, confidence=confidence, extraction_type=extraction_type,
            schema_creation_mode=schema_creation_mode, model=model,
        )

    async def ingest(
        self,
        database: str,
        payload: dict,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "assertion",
        schema_creation_mode: Literal["llm", "none"] = "llm",
        model: str = "gpt-4o",
    ) -> IngestReport:
        return await self._ingestion.ingest(
            database, payload,
            source_id=source_id, confidence=confidence, extraction_type=extraction_type,
            schema_creation_mode=schema_creation_mode, model=model,
        )

    async def update_source(
        self,
        database: str,
        source_id: str,
        name: str | None = None,
        description: str | None = None,
        original: str | None = None,
    ) -> None:
        await self._provenance.update_source(database, source_id, name, description, original)

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
        expand_schema: bool = True,
        schema_expansion_target: list[Literal["text", "queries"]] | None = None,
        schema_expansion_depth: int = 1,
        schema_expansion_mode: Literal["llm", "mcp"] = "llm",
        schema_expansion_mcp_servers: list[dict[str, Any]] | None = None,
        schema_expansion_dedup_mode: Literal["search", "llm", "none"] = "search",
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, dict, Any], None] | None = None,
        max_turns: int = 25,
        related_data_k: int = 10,
        effort: Literal["low", "medium", "high"] = "low",
        max_concurrency: int = 1,
    ) -> None:
        if expand_schema:
            target = set(schema_expansion_target) if schema_expansion_target else {"text", "queries"}
            parts = []
            if "text" in target:
                parts.append(text)
            if "queries" in target and queries:
                parts.append("\n".join(queries))
            expansion_context = "\n\n".join(parts)
            await self._schema.expand_schema(
                database,
                expansion_context,
                depth=schema_expansion_depth,
                reference=schema_expansion_mode,
                mcp_servers=schema_expansion_mcp_servers,
                dedup_mode=schema_expansion_dedup_mode,
                model=model,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                max_turns=max_turns,
            )
        await self._extraction.import_text(
            database, text, queries, model, source_id,
            on_tool_call, on_tool_result, max_turns=max_turns, related_data_k=related_data_k,
            effort=effort, max_concurrency=max_concurrency,
        )

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
                "KB.ADD_ENTITY"                name, class_id (opt), description (opt), entity_id (opt), text (opt), source_id (opt), confidence (opt), extraction_type (opt)
                "KB.ADD_FACT"                  head_entity_id (opt), relation_id (opt), tail_entity_id (opt), name (opt), description (opt), fact_id (opt), text (opt), source_id (opt), confidence (opt), extraction_type (opt)
                "KB.ADD_ENTITY_PROPERTY"       entity_id, property_id, value, literal_type (opt), name (opt), description (opt), text (opt), source_id (opt), confidence (opt), extraction_type (opt)
                "KB.ADD_FACT_PROPERTY"         fact_id, property_id, value, literal_type (opt), name (opt), description (opt), text (opt), source_id (opt), confidence (opt), extraction_type (opt)
                "KB.UPDATE_ENTITY"             entity_id, name (opt), description (opt)
                "KB.UPDATE_FACT"               fact_id, name (opt), description (opt)
                "KB.UPDATE_ENTITY_PROPERTY"    entity_id, property_id, value (opt), name (opt), description (opt)
                "KB.UPDATE_FACT_PROPERTY"      fact_id, property_id, value (opt), name (opt), description (opt)
                "KB.DELETE_ENTITY"             entity_id, cascade (opt, default False)
                "KB.DELETE_FACT"               fact_id
                "KB.DELETE_ENTITY_PROPERTY"    entity_id, property_id
                "KB.DELETE_FACT_PROPERTY"      fact_id, property_id
                "KB.GET_DATA"                  include_provenance (opt), compact (opt), detailed (opt)
                "KB.ADD_ENTITY_EXTRACTION"          entity_id, text, source_id (opt), confidence (opt), extraction_type (opt)
                "KB.ADD_FACT_EXTRACTION"            fact_id, text, source_id (opt), confidence (opt), extraction_type (opt)
                "KB.ADD_ENTITY_PROPERTY_EXTRACTION" entity_id, property_id, text, source_id (opt), confidence (opt), extraction_type (opt)
                "KB.ADD_FACT_PROPERTY_EXTRACTION"   fact_id, property_id, text, source_id (opt), confidence (opt), extraction_type (opt)

            Provenance:
                "KB.ADD_SOURCE"                source, name (opt), description (opt), node_id (opt), original (opt)
                "KB.ADD_EXTRACTION"            text, name (opt), description (opt), source_id (opt), node_id (opt), confidence (opt), extraction_type (opt), element_id (opt)
                "KB.UPDATE_SOURCE"             source_id, name (opt), description (opt), original (opt)
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
                range=query.get("range"),
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
                confidence=query.get("confidence"),
                extraction_type=query.get("extraction_type", "text_span"),
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
                confidence=query.get("confidence"),
                extraction_type=query.get("extraction_type", "text_span"),
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
                confidence=query.get("confidence"),
                extraction_type=query.get("extraction_type", "text_span"),
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
                confidence=query.get("confidence"),
                extraction_type=query.get("extraction_type", "text_span"),
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
                detailed=query.get("detailed", False),
            ))

        if op == "KB.ADD_ENTITY_EXTRACTION":
            return _r(self.add_entity_extraction(
                database,
                entity_id=query["entity_id"],
                text=query["text"],
                source_id=query.get("source_id"),
                confidence=query.get("confidence"),
                extraction_type=query.get("extraction_type", "text_span"),
            ))

        if op == "KB.ADD_FACT_EXTRACTION":
            return _r(self.add_fact_extraction(
                database,
                fact_id=query["fact_id"],
                text=query["text"],
                source_id=query.get("source_id"),
                confidence=query.get("confidence"),
                extraction_type=query.get("extraction_type", "text_span"),
            ))

        if op == "KB.ADD_ENTITY_PROPERTY_EXTRACTION":
            return _r(self.add_entity_property_value_extraction(
                database,
                entity_id=query["entity_id"],
                property_id=query["property_id"],
                text=query["text"],
                source_id=query.get("source_id"),
                confidence=query.get("confidence"),
                extraction_type=query.get("extraction_type", "text_span"),
            ))

        if op == "KB.ADD_FACT_PROPERTY_EXTRACTION":
            return _r(self.add_fact_property_value_extraction(
                database,
                fact_id=query["fact_id"],
                property_id=query["property_id"],
                text=query["text"],
                source_id=query.get("source_id"),
                confidence=query.get("confidence"),
                extraction_type=query.get("extraction_type", "text_span"),
            ))

        # -- Provenance ----------------------------------------------------

        if op == "KB.ADD_SOURCE":
            return _r(self.add_source(
                database,
                source=query["source"],
                name=query.get("name"),
                description=query.get("description"),
                node_id=query.get("node_id"),
                original=query.get("original"),
            ))

        if op == "KB.ADD_EXTRACTION":
            return _r(self.add_extraction(
                database,
                text=query["text"],
                name=query.get("name"),
                description=query.get("description"),
                node_id=query.get("node_id"),
                source_id=query.get("source_id"),
                confidence=query.get("confidence"),
                extraction_type=query.get("extraction_type", "text_span"),
                element_id=query.get("element_id"),
            ))

        if op == "KB.UPDATE_SOURCE":
            _r(self.update_source(
                database, query["source_id"], query.get("name"), query.get("description"),
                query.get("original"),
            ))
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
            "KB.DELETE_FACT_PROPERTY, KB.GET_DATA, KB.ADD_ENTITY_EXTRACTION, KB.ADD_FACT_EXTRACTION, "
            "KB.ADD_ENTITY_PROPERTY_EXTRACTION, KB.ADD_FACT_PROPERTY_EXTRACTION, KB.ADD_SOURCE, "
            "KB.ADD_EXTRACTION, KB.UPDATE_SOURCE, KB.DELETE_SOURCE. Also accepts GRAPH.* ops."
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
        ``optional_properties["model"]``; defaults to ``"gpt-4o"``. ``max_turns``
        is taken from ``optional_properties["max_turns"]``; defaults to ``25``.
        ``llm_fn``, ``collection``, and ``schema`` are ignored.
        """
        model = (optional_properties or {}).get("model", "gpt-4o")
        max_turns = (optional_properties or {}).get("max_turns", 25)
        return asyncio.run(
            self.answer(database, question=query, model=model, natural_language=False, max_turns=max_turns)
        )
