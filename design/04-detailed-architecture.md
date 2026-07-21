# KBDB — Detailed Architecture

## External Interfaces

All four interfaces are thin adapters over the Python library. They each ultimately delegate to the same core operations on `KBSource`.

| Interface | Technology | Primary Use |
|-----------|-----------|-------------|
| Python Library | Native Python API | Embedding KBDB directly in Python applications and pipelines |
| MCP Server | MCP protocol | LLM agent tool use (e.g. Claude) |
| REST API | FastAPI (HTTP/JSON) | Non-Python consumers and microservice deployments |
| CLI | Click + Rich | Development, debugging, schema inspection, and scripting |

### Python Library

`KBSource` and related classes are the native interface. Direct method calls with no serialization overhead. All other interfaces wrap this layer.

### MCP Server

Exposes schema declaration, instance ingestion, text extraction, and query operations as MCP tools. Enables LLM agents to build and query a knowledge base directly within an agentic context.

### REST API

HTTP interface built with FastAPI. Endpoints map to the core schema and knowledge operations. Enables non-Python services and remote clients to interact with KBDB over the network.

### CLI

Command-line interface built with Click and Rich. Thin wrapper over the Python library for interactive development, schema inspection, ingestion runs, and scripting.

---

## Information Flow Overview

### Schema Path

1. Caller requests a schema operation (declare, consolidate, search, import, or export)
2. Schema Manager checks idempotency: if the element already exists by name, its ID is returned immediately
3. If new: Schema Manager creates the node, wires hierarchy and domain/range edges, returns the new ID
4. If consolidation: Schema Manager merges or splits the affected elements, then traverses and rewires all instances that reference them
5. If search: Schema Manager filters schema elements against the query and returns matching names, descriptions, and IDs

### Ingest Path

1. Caller requests an instance operation (add entity, fact, or property value)
2. Knowledge Manager creates the instance node and wires its typed schema edges (class membership, relation, property)
3. If `text` is provided: Provenance Tracker creates an EXTRACTION node capturing the verbatim span, wires it to the instance and optionally to a SOURCE node
4. Change Notifier fires all registered hooks for the database with an enriched event describing the change
5. The new instance ID is returned to the caller

### Extraction Path

1. Caller invokes `import_text` with free text, guiding queries, and an optional SOURCE node ID
2. Extraction Engine reads the current schema from the Schema Manager and passes it to the LLM Agent as context
3. LLM Agent reads the text and, guided by the queries, identifies entities, facts, and property values
4. For each new concept not covered by the existing schema, the LLM Agent calls Schema Manager tools to declare it before creating instances
5. For each identified item, the LLM Agent calls Knowledge Manager tools, passing the verbatim text span — triggering the full Ingest Path including provenance and change notification
6. The loop continues until the LLM Agent reports no further items to extract

### Answer Path

1. Caller submits a natural language question
2. Knowledge Manager delegates to Query Engine
3. Query Engine fetches the current schema and constructs a system prompt with schema context and the question
4. LLM agent loop: the agent calls read-only graph tools (`search_entities`, `get_entity_facts`, `get_entity_properties`, `get_fact_properties`, `get_neighbors`) until it has enough information
5. Agent calls `return_result` with a structured list of `{entity, relation, value, provenance}` items
6. Query Engine unpacks the payload into `AnswerResult` objects and returns them to the caller

### Validate Path

1. Caller submits a statement to be validated
2. Knowledge Manager delegates to Query Engine
3. Query Engine fetches the current schema and constructs a system prompt directing the agent to find supporting and counter evidence
4. LLM agent loop: the agent uses graph tools to search for facts that confirm or contradict the statement
5. Agent calls `return_result` with `{supporting: [...], counter: [...]}` where each item is `{fact, head, tail, relation}`
6. Query Engine unpacks the payload into a `ValidationResult` and returns it to the caller

### Enrich Path

1. Caller submits a query and an optional depth limit
2. Knowledge Manager delegates to Query Engine
3. Query Engine fetches the current schema and constructs a system prompt directing the agent to traverse the graph up to `depth` hops from seed entities
4. LLM agent loop: the agent uses `search_entities` to find seeds, then `get_neighbors` and `get_entity_facts` to explore outward
5. Agent calls `return_result` with a list of `{entity, relation, value, distance}` items
6. Query Engine unpacks the payload into `EnrichmentResult` objects and returns them to the caller

---

## 1. Schema Layer

The Schema Layer manages the typed vocabulary of the knowledge base. It is the authoritative source of truth for what kinds of entities, relations, and properties exist, and it enforces consistency between the schema and all instance data.

### Schema Manager

**Responsibilities**

- Declare classes, relation types, and properties (idempotent: existing names return their IDs without modification)
- Maintain class and relation hierarchies (subclass, sub-relation)
- Wire domain and range constraints from relation types to classes
- Export the schema to a portable JSON representation and import it from that representation into any database
- Expose the meta-schema — the fixed description of KBDB's own graph structure — for agent introspection
- Search schema elements by natural language query or keywords
- Consolidate schema elements: merge redundant classes, properties, or relations; split overloaded ones; propagate all changes to affected instances

**Schema Declaration**

When a caller declares a schema element, the Schema Manager:

1. Looks up the element by name in the Graph Store
2. If found: returns the existing ID (idempotent)
3. If not found: creates the node with the provided name and description, wires any optional edges (hierarchy, domain, range), and returns the new ID

**Schema Consolidation**

Consolidation is a multi-step transaction:

1. Identify all instances that reference the source element(s) via typed edges
2. Rewrite those edges to reference the target element
3. Remove the source element(s) from the schema
4. Validate that no dangling references remain

For a merge (two classes → one): all instances of both classes are re-typed to the merged class.
For a split (one class → two): instances are partitioned by a caller-supplied predicate and re-typed accordingly.

**Schema Search**

When search is enabled for the database, `search_schema` is delegated to the Search Indexer, which matches the query against classes, relations, and properties using either BM25 keyword ranking or embedding-based semantic similarity, optionally restricted to a single element type. When search is not enabled, the Schema Manager falls back to a direct name/description substring match with no ranking. Either way, results are returned as a list of (name, type, ID, description) tuples.

**Example**

Query: `"employment"`

Matching schema elements:
```
EMPLOYEE     CLASS      "A person employed by an organization"
COMPANY      CLASS      "A business organization"
WORKED_AT    RELATION   "Employment relationship between a person and a company"
START_DATE   PROPERTY   "Date when employment began"
```

---

## 2. Knowledge Layer

The Knowledge Layer populates the knowledge base with instance data and answers queries over it. It delegates all persistence to the Graph Store and all schema lookups to the Schema Layer.

### Knowledge Manager

**Responsibilities**

- Create entity, fact, and literal nodes; wire their typed schema edges
- Delegate answer, validate, and enrich queries to the Query Engine
- Delegate provenance creation to the Provenance Tracker
- Trigger the Change Notifier after each write
- Search entities and facts by keyword or semantic similarity — delegated to the Search Indexer when search is enabled, otherwise served directly with an unranked name/description match

**Instance Creation**

For each new entity, fact, or property value:

1. Create the instance node in the Graph Store
2. Wire schema edges: IS_INSTANCE_OF (entity → class), REPRESENTS (fact → relation), HAS_PROPERTY / IS_VALUE_OF (literal → entity or fact)
3. If `text` is provided: delegate to Provenance Tracker
4. Trigger Change Notifier with a fully enriched event

**Answer / Validate / Enrich**

All three operations are delegated directly to the Query Engine. `KnowledgeManager` exposes them as part of its public interface but contains no query logic itself.

### Query Engine

**Responsibilities**

- Implement answer, validate, and enrich using a shared LLM agent loop
- Provide the agent with the current database schema as context and a read-only tool set over the instance graph
- Intercept the agent's `return_result` tool call to capture the structured payload and exit the loop
- Parse the payload into typed result objects (`AnswerResult`, `ValidationResult`, `EnrichmentResult`)

**Tool Set**

The LLM agent has access to the following read-only tools during each query:

| Tool | Description |
|------|-------------|
| `get_schema` | Return the current database schema (classes, relations, properties) |
| `search_schema` | Search classes, relations, and properties by keyword or semantic similarity, via the Search Indexer |
| `search_entities` | Search ENTITY nodes by keyword or semantic similarity against name, description, and property values, via the Search Indexer |
| `search_facts` | Search FACT nodes by keyword or semantic similarity against head/relation/tail names, description, and property values, via the Search Indexer |
| `get_entity_facts` | Return all facts involving an entity as head or tail |
| `get_entity_properties` | Return all property values attached to an entity |
| `get_fact_properties` | Return all property values attached to a fact |
| `get_neighbors` | BFS from an entity through facts up to a given depth |
| `return_result` | Terminal tool — delivers the final structured payload and ends the loop |

**Agent Loop**

1. Fetch the current schema via `SchemaManager.fetch_database_schema` and serialize it into the system prompt
2. Submit the system prompt and user message to the LLM with the full tool set
3. Receive tool calls from the LLM:
   - If `return_result`: capture the `result` payload, append a `"done"` tool response, and break
   - Otherwise: dispatch to the appropriate graph traversal and append the JSON result
4. Repeat from step 3 until `return_result` is called or the LLM stops issuing tool calls
5. Parse and return the captured payload as typed result objects

### Provenance Tracker

**Responsibilities**

- Create EXTRACTION nodes capturing verbatim text spans
- Wire EXTRACTED_FROM edges from instances to their EXTRACTION nodes
- Wire EXTRACTION nodes to SOURCE nodes when a source is provided

**Provenance Flow**

When an instance is created with a `text` argument:

1. Create an EXTRACTION node with the verbatim text span
2. If `source_id` is provided: wire an EXTRACTED_FROM edge from the EXTRACTION to the SOURCE
3. Wire an EXTRACTED_FROM edge from the instance to the EXTRACTION

One EXTRACTION node is created per item, giving per-item granularity. Multiple items can point to the same SOURCE but have distinct EXTRACTION nodes even when drawn from the same passage.

### Change Notifier

**Responsibilities**

- Maintain per-database hook registries
- Fire all registered hooks synchronously after each instance write
- Enrich events with human-readable names before delivery

**Event Structure**

Every event carries `type`, `database`, and `id`. Additional fields by type:

| Type | Additional Fields |
|------|------------------|
| `entity_added` | `name`, `class_id`, `class_name` |
| `fact_added` | `head_entity_id`, `head_entity_name`, `tail_entity_id`, `tail_entity_name`, `relation_id`, `relation_name` |
| `entity_property_added` | `entity_id`, `property_id`, `property_name`, `value` |
| `fact_property_added` | `fact_id`, `property_id`, `property_name`, `value`, `head_entity_name`, `tail_entity_name`, `relation_name` |

Hooks are scoped to a single database: a hook registered on database `"main"` does not fire for writes to any other database.

---

## 3. Extraction Engine

The Extraction Engine bridges free text and the structured knowledge base using an LLM as its reasoning core. It calls into the Schema Layer and Knowledge Layer using the same operations available to any other caller.

### LLM Agent

**Responsibilities**

- Read the current schema and guiding queries at the start of each extraction run
- Identify entities, facts, and property values in the text
- Extend the schema when new concepts are encountered
- Create instances with verbatim text spans for provenance
- Terminate when no further items can be extracted

**Tool Set**

The LLM Agent has access to the following tools during the extraction loop:

| Tool Group | Tools |
|------------|-------|
| Schema inspection | `get_schema`, `find_class`, `find_relation_type`, `find_entity` |
| Schema declaration | `declare_class`, `declare_relation_type`, `declare_class_property`, `declare_relation_property` |
| Instance creation | `add_entity`, `add_fact`, `add_entity_property_value`, `add_fact_property_value` |

`find_class`, `find_relation_type`, and `find_entity` look an element up by exact name so the LLM Agent can check idempotently before declaring or creating — they are distinct from the Search Indexer's keyword/semantic `search_schema` tool used by the Query Engine.

**Extraction Loop**

1. Fetch the current schema and present it to the LLM alongside the text and guiding queries
2. The LLM identifies the next item to extract and the verbatim span it comes from
3. If the item requires a new schema element: the LLM calls the appropriate declare tool first
4. The LLM calls the appropriate instance tool, passing the verbatim span as `text`
5. Repeat from step 2 until the LLM signals completion

**Example**

Text: `"Alice joined IBM as a software engineer in June 1998."`
Guiding query: `"Where did Alice work?"`

LLM sequence:
1. Checks schema — `EMPLOYEE`, `COMPANY`, `WORKED_AT` exist
2. Calls `add_entity("Alice", class="EMPLOYEE", text="Alice joined IBM...")`
3. Calls `add_entity("IBM", class="COMPANY", text="Alice joined IBM...")`
4. Calls `add_fact(head="Alice", relation="WORKED_AT", tail="IBM", text="Alice joined IBM...")`
5. Calls `add_entity_property_value(entity="Alice", property="START_DATE", value="1998-06-01", text="...in June 1998.")`
6. Reports no further items — loop exits

---

## 4. Search Indexer

The Search Indexer maintains a search index over schema elements and instance data so that `search_schema` and `search_data` can be answered by ranked lookup instead of an LLM call. It is optional per database: enabling it registers the indexer as a global change hook, so it stays in sync automatically as writes occur elsewhere in the system.

**Responsibilities**

- Maintain a BM25 keyword corpus, always available when search is enabled
- Maintain a vector index for semantic (embedding-based) search, when a vector store and embedding function are configured
- Incrementally update the index in response to change events (add / update / delete for classes, relations, properties, entities, and facts) rather than requiring a full rebuild after every write
- Support a full reindex on demand (`reindex_all`), rebuilding the corpus from every node currently in the Graph Store — used after bulk imports or when the embedding model changes

**Index Structure**

The indexer keeps two collections per database: `schema` (classes, relations, properties) and `data` (entities, facts). Each indexed node stores a composite text built from its own name/description plus relevant neighbors — for example, an entity's text includes its class name, and a fact's text includes its head, relation, and tail names — so a query can match on context the node doesn't literally contain in its own name.

**Search**

`search_schema` and `search_data` both accept a `mode` (`keyword` or `semantic`), an optional `type_filter`, and a result limit `k`. In `keyword` mode, results are ranked by BM25 score against the corpus; in `semantic` mode, results are ranked by embedding similarity via the configured vector store. Each result carries the node's ID, type, name, and description (plus head/relation/tail names for facts), and a similarity `score` when in semantic mode.

---

## 5. Graph Store

The Graph Store persists all schema and instance data as a typed graph of nodes and edges. It is fully abstract: the same KBDB operations run without modification against any supported backend.

**Responsibilities**

- Store and retrieve nodes by ID, label, and property values
- Store and retrieve edges by type and direction
- Support neighbor traversal (outgoing and incoming edges by type)
- Support node lookup by label and property filter

**Backends**

| Backend | Characteristics | Typical Use |
|---------|----------------|------------|
| Memory | In-process, non-persistent | Development, testing, short-lived pipelines |
| File | JSON file per database, persistent, single-process | CLI default (`~/.kbdb/` or `KBDB_DATA_DIR`), no server required |
| Neo4j | Persistent, concurrent, queryable with Cypher | Production deployments |
