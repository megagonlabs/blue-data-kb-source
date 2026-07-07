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
2. Knowledge Manager interprets the question using the schema (classes, relations, and property types as context)
3. Knowledge Manager retrieves matching entities, facts, and property values from the Graph Store
4. Results are assembled into a structured or natural language response and returned

### Validate Path

1. Caller submits a statement to be validated
2. Knowledge Manager searches the instance graph for facts and property values that bear on the statement
3. Supporting evidence (facts that confirm the statement) and counter evidence (facts that contradict it) are collected
4. Evidence is returned alongside confidence indicators derived from provenance and source count

### Enrich Path

1. Caller submits a query
2. Knowledge Manager identifies entities and relations relevant to the query
3. Knowledge Manager traverses the graph outward from those entities, collecting adjacent facts, related entities, and contextual property values
4. The enriched context — related but not necessarily directly answering the query — is returned for use by the caller

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

The Schema Manager filters nodes by matching the query against element names and descriptions. Results are ranked by relevance and returned as a list of (name, type, ID, description) tuples.

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
- Execute answer, validate, and enrich queries over the instance graph
- Delegate provenance creation to the Provenance Tracker
- Trigger the Change Notifier after each write

**Instance Creation**

For each new entity, fact, or property value:

1. Create the instance node in the Graph Store
2. Wire schema edges: IS_INSTANCE_OF (entity → class), REPRESENTS (fact → relation), HAS_PROPERTY / IS_VALUE_OF (literal → entity or fact)
3. If `text` is provided: delegate to Provenance Tracker
4. Trigger Change Notifier with a fully enriched event

**Answer**

1. Parse the question to identify the target entity class, relation, or property type
2. Retrieve all matching instances and their connected values from the Graph Store
3. Rank by relevance and return a structured result set

**Validate**

1. Parse the statement into a subject, predicate, and object
2. Traverse the instance graph for facts where subject and predicate match
3. Compare the object of each retrieved fact against the stated value
4. Classify each as supporting or counter evidence
5. Return both sets with provenance references

**Enrich**

1. Identify anchor entities in the query (by name or class)
2. Traverse outgoing and incoming facts from each anchor up to a configurable depth
3. Collect adjacent entities, facts, and property values as enrichment context
4. Return the enrichment set, deduplicated and ranked by proximity to the anchor

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
| Schema inspection | `get_schema`, `search_schema` |
| Schema declaration | `declare_class`, `declare_relation_type`, `declare_class_property`, `declare_relation_property` |
| Instance creation | `add_entity`, `add_fact`, `add_entity_property_value`, `add_fact_property_value` |

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

## 4. Graph Store

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
| Neo4j | Persistent, concurrent, queryable with Cypher | Production deployments |
