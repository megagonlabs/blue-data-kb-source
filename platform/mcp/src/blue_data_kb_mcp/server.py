"""blue-data-kb-source MCP server.

Exposes KBSource operations as MCP tools so LLM clients (Claude Desktop, Claude
Code, etc.) can build and query a knowledge base. Read and additive-write only —
no update_*/delete_* tools; corrections go through the `kb` CLI or the library
directly.

Tool groups
-----------
Schema (write)     — declare_class, declare_relation, declare_class_property,
                      declare_relation_property, add_is_subclass_of, attach_property
Schema (read)      — search_schema, fetch_schema, fetch_meta_schema
Schema expansion   — expand_schema (LLM-grown schema, optionally grounded by
                      connected MCP servers, e.g. platform/wordnet-mcp — run
                      before import_text, or let import_text run it inline)
Knowledge (write)  — add_entity, add_fact, add_entity_property, add_fact_property,
                      add_entity_extraction, add_fact_extraction,
                      add_entity_property_extraction, add_fact_property_extraction
Knowledge (read)   — search_data, fetch_data, get_metadata
Query              — answer, validate, enrich
Extraction         — import_text (extraction only — schema mutation lives in
                      expand_schema)
Provenance         — add_source

Quick start
-----------
    # Install
    pip install -e ../../lib
    pip install -e .

    # Run (stdio transport for Claude Desktop / Claude Code)
    blue-data-kb-mcp

    # With a persistent file-backed KB and BM25 search
    BLUE_DATA_KB_SOURCE_DIRECTORY=/path/to/kb BLUE_DATA_KB_SOURCE_SEARCH=bm25 \\
        blue-data-kb-mcp

Claude Desktop config (~/.config/claude-desktop/claude_desktop_config.json):
    {
      "mcpServers": {
        "blue-data-kb": {
          "command": "blue-data-kb-mcp",
          "env": {
            "BLUE_DATA_KB_SOURCE_DIRECTORY": "/path/to/kb",
            "BLUE_DATA_KB_SOURCE_SEARCH": "bm25"
          }
        }
      }
    }
"""
from __future__ import annotations

import dataclasses
import json
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from blue_data_kb_mcp.config import default_model, get_kb

mcp = FastMCP(
    "blue-data-kb",
    instructions=(
        "Knowledge base — declare schema (classes, relations, properties), add entities, "
        "facts, and property values with provenance, then search, answer questions, "
        "validate statements, and enrich around a topic. Read and additive-write only; "
        "no update or delete tools."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(data: Any) -> str:
    if dataclasses.is_dataclass(data) and not isinstance(data, type):
        data = dataclasses.asdict(data)
    elif isinstance(data, list):
        data = [dataclasses.asdict(item) if dataclasses.is_dataclass(item) else item for item in data]
    return json.dumps(data, indent=2, default=str)


def _err(e: Exception) -> str:
    return json.dumps({"error": str(e)}, indent=2)


# ---------------------------------------------------------------------------
# Schema tools (write)
# ---------------------------------------------------------------------------

@mcp.tool()
async def declare_class(
    name: str, description: str, database: str = "default", class_id: Optional[str] = None
) -> str:
    """Declare a class in the knowledge base schema. Idempotent — if a class with
    this name already exists in *database*, its id is returned unchanged.

    Args:
        name:        Class name, e.g. PERSON, COMPANY.
        description: Human-readable description of the class.
        database:    Target database (default 'default').
        class_id:    Id to use if creating a new node (optional).
    """
    try:
        cid = await get_kb().declare_class(database, name, description, class_id)
        return _ok({"id": cid})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def declare_relation(
    name: str,
    description: str,
    domain: str,
    range: str,
    is_symmetric: bool = False,
    database: str = "default",
    relation_id: Optional[str] = None,
) -> str:
    """Declare a relation type in the schema. Idempotent. Domain and range must
    name existing classes (declare them first with declare_class).

    Args:
        name:         Relation name, e.g. WORKED_AT.
        description:  Human-readable description of the relation.
        domain:       Name of the head (source) entity class.
        range:        Name of the tail (target) entity class.
        is_symmetric: Whether the relation reads the same in both directions (default False).
        database:     Target database (default 'default').
        relation_id:  Id to use if creating a new node (optional).
    """
    try:
        rid = await get_kb().declare_relation_type(
            database, name, description, is_symmetric, relation_id, domain=domain, range=range
        )
        return _ok({"id": rid})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def declare_class_property(
    name: str,
    description: str,
    class_id: Optional[str] = None,
    database: str = "default",
    property_id: Optional[str] = None,
) -> str:
    """Declare a property on a class. Idempotent per (class_id, name).

    Args:
        name:        Property name, e.g. AGE, START_DATE.
        description: Human-readable description of the property.
        class_id:    Id of the class this property belongs to (optional — omit for
                     a free-standing property not yet wired to a class).
        database:    Target database (default 'default').
        property_id: Id to use if creating a new node (optional).
    """
    try:
        pid = await get_kb().declare_class_property(database, name, description, property_id, class_id)
        return _ok({"id": pid})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def declare_relation_property(
    name: str,
    description: str,
    relation_id: Optional[str] = None,
    database: str = "default",
    property_id: Optional[str] = None,
) -> str:
    """Declare a property on a relation. Idempotent per (relation_id, name).

    Args:
        name:        Property name, e.g. START_DATE.
        description: Human-readable description of the property.
        relation_id: Id of the relation this property belongs to (optional).
        database:    Target database (default 'default').
        property_id: Id to use if creating a new node (optional).
    """
    try:
        pid = await get_kb().declare_relation_property(database, name, description, property_id, relation_id)
        return _ok({"id": pid})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def add_is_subclass_of(child_id: str, parent_id: str, database: str = "default") -> str:
    """Declare a subclass relationship between two classes or two relations
    (both ids must be the same kind — both classes or both relations).

    Args:
        child_id:  Id of the child (more specific) class or relation.
        parent_id: Id of the parent (more general) class or relation.
        database:  Target database (default 'default').
    """
    try:
        await get_kb().add_is_subclass_of(database, child_id, parent_id)
        return _ok({"status": "ok"})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def attach_property(target_id: str, property_id: str, database: str = "default") -> str:
    """Attach an existing property to a class or relation (idempotent). Use to reuse a
    property already declared elsewhere instead of declaring a same-meaning duplicate
    under a new name — e.g. found via search_schema.

    Args:
        target_id:   Id of the class or relation to attach the property to.
        property_id: Id of the existing property to reuse.
        database:    Target database (default 'default').
    """
    try:
        await get_kb().attach_property(database, target_id, property_id)
        return _ok({"status": "ok"})
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Schema tools (read)
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_schema(
    query: str, mode: str = "keyword", type: Optional[str] = None, database: str = "default"
) -> str:
    """Search schema elements by keyword or semantic similarity. Use before
    declaring new classes/relations/properties to check if a suitable element
    already exists.

    Args:
        query:    Keywords or natural language query.
        mode:     'keyword' (default) or 'semantic' (requires the database to be
                  configured for semantic search).
        type:     Restrict to one element type: 'class', 'relation', or 'property'
                  (optional — omit to search all).
        database: Target database (default 'default').
    """
    try:
        results = await get_kb().search_schema(database, query, mode=mode, type_filter=type)
        return _ok(results)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def fetch_schema(database: str = "default") -> str:
    """Return the full declared schema (classes, relations, properties, and
    their domain/range/hierarchy) for a database.

    Args:
        database: Target database (default 'default').
    """
    try:
        schema = await get_kb().fetch_database_schema(database)
        return _ok(schema.to_json())
    except Exception as e:
        return _err(e)


@mcp.tool()
async def fetch_meta_schema() -> str:
    """Return the fixed schema describing the knowledge base's own node and
    edge type taxonomy (CLASS, RELATION, PROPERTY, ENTITY, FACT, LITERAL,
    SOURCE, EXTRACTION and how they connect). Not specific to any database."""
    try:
        schema = await get_kb().fetch_meta_schema()
        return _ok(schema.to_json())
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Knowledge tools (write, additive only)
# ---------------------------------------------------------------------------

@mcp.tool()
async def add_entity(
    name: str,
    class_id: Optional[str] = None,
    description: Optional[str] = None,
    text: Optional[str] = None,
    source_id: Optional[str] = None,
    database: str = "default",
) -> str:
    """Add an entity instance to the knowledge base.

    Args:
        name:        Entity name, e.g. Alice.
        class_id:    Id of the entity's class (optional).
        description: Optional description.
        text:        Verbatim source text span this entity was drawn from (optional
                     — creates a per-item EXTRACTION node for provenance).
        source_id:   Id of the source document node (optional, used with text).
        database:    Target database (default 'default').
    """
    try:
        eid = await get_kb().add_entity(database, name, description, None, class_id, text, source_id)
        return _ok({"id": eid})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def add_fact(
    head_entity_id: Optional[str] = None,
    relation_id: Optional[str] = None,
    tail_entity_id: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    text: Optional[str] = None,
    source_id: Optional[str] = None,
    database: str = "default",
) -> str:
    """Add a fact (relation instance) between two entities.

    Args:
        head_entity_id: Id of the subject (from) entity.
        relation_id:    Id of the relation type this fact instantiates.
        tail_entity_id: Id of the object (to) entity.
        name:           Fact name (auto-generated from head/relation/tail if omitted).
        description:    Optional description.
        text:           Verbatim source text span this fact was drawn from (optional).
        source_id:      Id of the source document node (optional, used with text).
        database:       Target database (default 'default').
    """
    try:
        fid = await get_kb().add_fact(
            database, name, description, None, head_entity_id, tail_entity_id, relation_id, text, source_id
        )
        return _ok({"id": fid})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def add_entity_property(
    entity_id: str,
    property_id: str,
    value: Any,
    literal_type: Optional[str] = None,
    text: Optional[str] = None,
    source_id: Optional[str] = None,
    database: str = "default",
) -> str:
    """Add a property value to an entity.

    Args:
        entity_id:    Id of the entity.
        property_id:  Id of the property.
        value:        Value to store (string, number, or boolean).
        literal_type: 'NUMBER', 'DATE', 'BOOLEAN', or 'STRING' (inferred from value if omitted).
        text:         Verbatim source text span this value was drawn from (optional).
        source_id:    Id of the source document node (optional, used with text).
        database:     Target database (default 'default').
    """
    try:
        lid = await get_kb().add_entity_property_value(
            database, value, None, None, literal_type, entity_id, property_id, text, source_id
        )
        return _ok({"id": lid})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def add_fact_property(
    fact_id: str,
    property_id: str,
    value: Any,
    literal_type: Optional[str] = None,
    text: Optional[str] = None,
    source_id: Optional[str] = None,
    database: str = "default",
) -> str:
    """Add a property value to a fact.

    Args:
        fact_id:      Id of the fact.
        property_id:  Id of the property.
        value:        Value to store (string, number, or boolean).
        literal_type: 'NUMBER', 'DATE', 'BOOLEAN', or 'STRING' (inferred from value if omitted).
        text:         Verbatim source text span this value was drawn from (optional).
        source_id:    Id of the source document node (optional, used with text).
        database:     Target database (default 'default').
    """
    try:
        lid = await get_kb().add_fact_property_value(
            database, value, None, None, literal_type, fact_id, property_id, text, source_id
        )
        return _ok({"id": lid})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def add_entity_extraction(
    entity_id: str, text: str, source_id: Optional[str] = None, database: str = "default"
) -> str:
    """Link an additional source text span to an entity that already exists.

    Unlike the text argument on add_entity, this does not create a new node —
    it adds another EXTRACTED_FROM edge, so multiple spans can accumulate over time.

    Args:
        entity_id: Id of the existing entity.
        text:      New source text span.
        source_id: Id of the source document node (optional).
        database:  Target database (default 'default').
    """
    try:
        xid = await get_kb().add_entity_extraction(database, entity_id, text, source_id)
        return _ok({"id": xid})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def add_fact_extraction(
    fact_id: str, text: str, source_id: Optional[str] = None, database: str = "default"
) -> str:
    """Link an additional source text span to a fact that already exists.

    Args:
        fact_id:   Id of the existing fact.
        text:      New source text span.
        source_id: Id of the source document node (optional).
        database:  Target database (default 'default').
    """
    try:
        xid = await get_kb().add_fact_extraction(database, fact_id, text, source_id)
        return _ok({"id": xid})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def add_entity_property_extraction(
    entity_id: str, property_id: str, text: str, source_id: Optional[str] = None, database: str = "default"
) -> str:
    """Link an additional source text span to an entity's existing property value.

    Args:
        entity_id:   Id of the entity.
        property_id: Id of the property.
        text:        New source text span.
        source_id:   Id of the source document node (optional).
        database:    Target database (default 'default').
    """
    try:
        xid = await get_kb().add_entity_property_value_extraction(database, entity_id, property_id, text, source_id)
        return _ok({"id": xid})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def add_fact_property_extraction(
    fact_id: str, property_id: str, text: str, source_id: Optional[str] = None, database: str = "default"
) -> str:
    """Link an additional source text span to a fact's existing property value.

    Args:
        fact_id:     Id of the fact.
        property_id: Id of the property.
        text:        New source text span.
        source_id:   Id of the source document node (optional).
        database:    Target database (default 'default').
    """
    try:
        xid = await get_kb().add_fact_property_value_extraction(database, fact_id, property_id, text, source_id)
        return _ok({"id": xid})
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Knowledge tools (read)
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_data(
    query: str, mode: str = "keyword", type: Optional[str] = None, database: str = "default"
) -> str:
    """Search entities and facts by keyword or semantic similarity.

    Args:
        query:    Keywords or natural language query.
        mode:     'keyword' (default) or 'semantic' (requires the database to be
                  configured for semantic search).
        type:     Restrict to one type: 'entity' or 'fact' (optional — omit to search both).
        database: Target database (default 'default').
    """
    try:
        results = await get_kb().search_data(database, query, mode=mode, type_filter=type)
        return _ok(results)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def fetch_data(
    database: str = "default",
    compact: bool = True,
    include_provenance: bool = False,
    detailed: bool = False,
) -> str:
    """Return the instance data (entities, facts, property values) in a database.

    Args:
        database:            Target database (default 'default').
        compact:              Flat, denormalized rows (default True) — the readable
                              shape. False returns the raw {nodes, edges} graph form.
        include_provenance:  Include each row's extraction text and source name (compact
                              form only).
        detailed:             Include each row's own description/created/modified, plus
                              (together with include_provenance) the linked source's
                              original date (compact form only).
    """
    try:
        data = await get_kb().fetch_database_data(database, include_provenance, compact, detailed)
        return _ok(data)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_metadata(node_id: str, database: str = "default") -> str:
    """Return created/modified timestamps and provenance (extraction text, source
    name, source original date) for a specific entity, fact, or property value id.

    Args:
        node_id:  Id of an entity, fact, or property value (literal).
        database: Target database (default 'default').
    """
    try:
        meta = await get_kb().get_metadata(database, node_id)
        return _ok(meta)
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Query tools (each runs its own LLM tool-calling loop against the KB)
# ---------------------------------------------------------------------------

@mcp.tool()
async def answer(
    question: str,
    model: Optional[str] = None,
    natural_language: bool = False,
    include_metadata: bool = False,
    database: str = "default",
) -> str:
    """Answer a natural language question using the knowledge base.

    Runs an LLM agent that searches and traverses the instance graph. Returns a
    list of flat result rows with LLM-chosen column names by default.

    Args:
        question:          The question to answer.
        model:             OpenAI model (default: server's configured default model).
        natural_language:  Return a prose answer instead of structured rows.
        include_metadata:  Also collect created/modified/confidence/evidence metadata
                           for cited entities/facts/properties; with natural_language,
                           folds a provenance section into the prose.
        database:          Target database (default 'default').
    """
    try:
        result = await get_kb().answer(
            database, question, model or default_model(), natural_language, include_metadata
        )
        if include_metadata:
            payload, metadata = result
            return _ok({"result": payload, "metadata": metadata})
        return _ok({"result": result})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def validate(
    statement: str,
    model: Optional[str] = None,
    natural_language: bool = False,
    include_metadata: bool = False,
    database: str = "default",
) -> str:
    """Return supporting and counter evidence from the knowledge base for a statement.

    Args:
        statement:         The statement to check.
        model:             OpenAI model (default: server's configured default model).
        natural_language:  Return a prose assessment instead of supporting/counter lists.
        include_metadata:  Also collect created/modified/confidence/evidence metadata
                           for cited facts; with natural_language, folds a provenance
                           section into the prose.
        database:          Target database (default 'default').
    """
    try:
        result = await get_kb().validate(
            database, statement, model or default_model(), natural_language, include_metadata
        )
        if include_metadata:
            payload, metadata = result
            if natural_language:
                return _ok({"result": payload, "metadata": metadata})
            return _ok({"supporting": payload.supporting, "counter": payload.counter, "metadata": metadata})
        if natural_language:
            return _ok({"result": result})
        return _ok({"supporting": result.supporting, "counter": result.counter})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def enrich(
    query: str, depth: int = 2, model: Optional[str] = None, database: str = "default"
) -> str:
    """Traverse the graph up to *depth* hops from entities matching *query* and
    return related knowledge.

    Args:
        query:    Seed query — an LLM agent finds starting entities matching this.
        depth:    Maximum hops to traverse (default 2).
        model:    OpenAI model (default: server's configured default model).
        database: Target database (default 'default').
    """
    try:
        results = await get_kb().enrich(database, query, depth, model or default_model())
        return _ok(results)
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Schema expansion
# ---------------------------------------------------------------------------

@mcp.tool()
async def expand_schema(
    text: str,
    depth: int = 1,
    reference: str = "llm",
    mcp_servers: Optional[list[dict]] = None,
    dedup_mode: str = "search",
    model: Optional[str] = None,
    database: str = "default",
) -> str:
    """Grow the schema (classes, relations, properties) grounded by *text* —
    without extracting any data. Run once, then import_text (with
    expand_schema=False) can extract many documents against the schema this
    produces.

    Args:
        text:        Passage to ground schema proposals in.
        depth:       Hierarchy levels beyond what's directly evidenced in *text*
                     to also propose (default 1 = immediate parent/child category).
        reference:   'llm' (default) proposes schema freely; 'mcp' additionally
                     gives the model tools from *mcp_servers* (e.g. WordNet
                     hypernyms/synonyms) to ground names and hierarchy placement.
        mcp_servers: MCP servers to connect when reference='mcp' — each
                     {"transport": "stdio", "command": ..., "args": [...], "env": {...}}
                     or {"transport": "sse"|"streamable_http", "url": ..., "headers": {...}}.
        dedup_mode:  How to check for an existing schema element before declaring a
                     new one: 'search' (default, uses the configured search backend),
                     'llm' (asks the model to judge synonyms), or 'none' (disables
                     the check).
        model:       OpenAI model (default: server's configured default model).
        database:    Target database (default 'default').
    """
    try:
        await get_kb().expand_schema(
            database, text,
            depth=depth,
            reference=reference,
            mcp_servers=mcp_servers,
            dedup_mode=dedup_mode,
            model=model or default_model(),
        )
        return _ok({"status": "ok"})
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

@mcp.tool()
async def import_text(
    text: str,
    queries: Optional[list[str]] = None,
    model: Optional[str] = None,
    source_id: Optional[str] = None,
    expand_schema: bool = True,
    schema_expansion_target: Optional[list[str]] = None,
    schema_expansion_depth: int = 1,
    schema_expansion_mode: str = "llm",
    schema_expansion_mcp_servers: Optional[list[dict]] = None,
    schema_expansion_dedup_mode: str = "search",
    database: str = "default",
) -> str:
    """Extract entities, facts, and property values from free text into the
    knowledge base using an LLM agent. By default this also expands the schema
    first (see expand_schema for its own params, mirrored here with a
    schema_expansion_ prefix) — pass expand_schema=False to only extract
    against the schema as it currently stands.

    Args:
        text:                          Free-text passage to extract from.
        queries:                       Guiding questions to focus extraction (optional).
        model:                         OpenAI model (default: server's configured default model).
        source_id:                     Id of an existing source node for provenance (optional).
        expand_schema:                 Run schema expansion before extracting (default True).
        schema_expansion_target:       What to ground expansion in: any combination of
                                       'text'/'queries' (default: both) — only used when
                                       expand_schema=True.
        schema_expansion_depth:        See expand_schema's depth.
        schema_expansion_mode:         See expand_schema's reference ('llm' or 'mcp').
        schema_expansion_mcp_servers:  See expand_schema's mcp_servers.
        schema_expansion_dedup_mode:   See expand_schema's dedup_mode.
        database:                      Target database (default 'default').
    """
    try:
        await get_kb().import_text(
            database, text,
            queries=queries,
            model=model or default_model(),
            source_id=source_id,
            expand_schema=expand_schema,
            schema_expansion_target=schema_expansion_target,
            schema_expansion_depth=schema_expansion_depth,
            schema_expansion_mode=schema_expansion_mode,
            schema_expansion_mcp_servers=schema_expansion_mcp_servers,
            schema_expansion_dedup_mode=schema_expansion_dedup_mode,
        )
        return _ok({"status": "ok"})
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Provenance (write, additive only)
# ---------------------------------------------------------------------------

@mcp.tool()
async def add_source(
    source: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    original: Optional[str] = None,
    database: str = "default",
) -> str:
    """Register a source document for provenance tracking.

    Args:
        source:      Source identifier, e.g. a filename or document id.
        name:        Human-readable label (defaults to *source*).
        description: Optional description.
        original:    The source's own date, e.g. when it was originally published
                     (distinct from when it was added to the KB).
        database:    Target database (default 'default').
    """
    try:
        sid = await get_kb().add_source(database, source, name, description, None, original)
        return _ok({"id": sid})
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
