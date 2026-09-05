# blue-data-kb-mcp

MCP server that exposes `KBSource` operations as tools for LLM clients (Claude
Desktop, Claude Code, and any MCP-compatible host).

Read and additive-write only — there are no `update_*`/`delete_*` tools. An LLM
agent connected through this server can declare schema, add entities/facts/
property values with provenance, search, answer questions, validate statements,
and enrich around a topic — but it cannot rewrite or delete existing data.
Corrections go through the `kb` CLI or the Python library directly.

---

## Installation

```bash
cd platform/mcp

# Install the core library first
pip install -e ../../lib

# Install the MCP server
pip install -e .

# Optional: Neo4j graph backend, ChromaDB/Redis vector backends, BM25 search
pip install -e ".[neo4j,chromadb,redis,search]"
```

---

## Running

```bash
# Default: ephemeral file-backed KB at ~/.kbdb, no search
blue-data-kb-mcp

# Persistent KB in a specific directory, with BM25 keyword search
BLUE_DATA_KB_SOURCE_DIRECTORY=/path/to/kb \
BLUE_DATA_KB_SOURCE_SEARCH=bm25 \
    blue-data-kb-mcp

# Neo4j backend
BLUE_DATA_KB_SOURCE_GRAPH_BACKEND=neo4j \
BLUE_DATA_KB_SOURCE_NEO4J_URI=bolt://localhost:7687 \
BLUE_DATA_KB_SOURCE_NEO4J_PASSWORD=secret \
    blue-data-kb-mcp

# Semantic search (OpenAI embeddings + in-memory vector store)
BLUE_DATA_KB_SOURCE_SEARCH=semantic OPENAI_API_KEY=sk-... \
    blue-data-kb-mcp
```

`OPENAI_API_KEY` is required for the `answer`/`validate`/`enrich`/`expand_schema`/`import_text`
tools (each runs its own LLM agent loop) and for `BLUE_DATA_KB_SOURCE_SEARCH=semantic`
(embeddings).

Note: this server does **not** read the `kb` CLI's `~/.kbdb/registry.json` — it
owns one independently-configured `KBSource` for the process lifetime (see
Architecture below). `BLUE_DATA_KB_SOURCE_DIRECTORY` defaults to the same `~/.kbdb`
directory the CLI's file backend uses by default, so pointing both at their
defaults gives practical (not automatic) continuity between `kb` and this server.

---

## Claude Desktop integration

Add to `~/.config/claude-desktop/claude_desktop_config.json`:

```json
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
```

If `blue-data-kb-mcp` is not on the system PATH, use the full path:

```json
{
  "mcpServers": {
    "blue-data-kb": {
      "command": "/path/to/venv/bin/blue-data-kb-mcp"
    }
  }
}
```

---

## Configuration reference

All configuration is via environment variables — there is no config file.

### Graph backend

| Variable | Default | Description |
|---|---|---|
| `BLUE_DATA_KB_SOURCE_GRAPH_BACKEND` | `file` | `file` — JSON files on disk. `neo4j` — Neo4j graph database. |
| `BLUE_DATA_KB_SOURCE_DIRECTORY` | `~/.kbdb` | File backend directory. Same default the `kb` CLI's file backend uses. |
| `BLUE_DATA_KB_SOURCE_NEO4J_URI` | — | Neo4j bolt URI, e.g. `bolt://localhost:7687`. Used when `BLUE_DATA_KB_SOURCE_GRAPH_BACKEND=neo4j`. |
| `BLUE_DATA_KB_SOURCE_NEO4J_USER` | `neo4j` | Neo4j username. |
| `BLUE_DATA_KB_SOURCE_NEO4J_PASSWORD` | — | Neo4j password. |

### Search

| Variable | Default | Description |
|---|---|---|
| `BLUE_DATA_KB_SOURCE_SEARCH` | `none` | `none` — substring fallback. `bm25` — keyword ranking. `semantic` — BM25 + vector similarity (requires `OPENAI_API_KEY`). |
| `BLUE_DATA_KB_SOURCE_BM25_INDEX_DIR` | `BLUE_DATA_KB_SOURCE_DIRECTORY` | Directory for the BM25 corpus files. |
| `BLUE_DATA_KB_SOURCE_VECTOR_BACKEND` | `memory` | `memory` \| `file` \| `chromadb` \| `redis`. Used when `BLUE_DATA_KB_SOURCE_SEARCH=semantic`. |
| `BLUE_DATA_KB_SOURCE_EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI embedding model. |

### LLM tools

| Variable | Default | Description |
|---|---|---|
| `BLUE_DATA_KB_SOURCE_DEFAULT_MODEL` | `gpt-4o` | Default model for `answer`/`validate`/`enrich`/`expand_schema`/`import_text` when a call omits `model`. |

---

## Tool reference

### Schema tools (write)

Idempotent — declaring an already-existing name returns its id unchanged.

#### `declare_class`

| Parameter | Type | Description |
|---|---|---|
| `name` | `str` | Class name, e.g. PERSON, COMPANY |
| `description` | `str` | Human-readable description |
| `database` | `str` | Target database (default `"default"`) |
| `class_id` | `str \| None` | Id to use if creating a new node |

Returns `{"id": "..."}`.

#### `declare_relation`

| Parameter | Type | Description |
|---|---|---|
| `name` | `str` | Relation name, e.g. WORKED_AT |
| `description` | `str` | Human-readable description |
| `domain` | `str` | Name of the head (source) entity class — must already exist |
| `range` | `str` | Name of the tail (target) entity class — must already exist |
| `is_symmetric` | `bool` | Default `False` |
| `database` | `str` | Target database (default `"default"`) |
| `relation_id` | `str \| None` | Id to use if creating a new node |

Returns `{"id": "..."}`.

#### `declare_class_property` / `declare_relation_property`

Same shape: `name`, `description`, `class_id`/`relation_id` (optional owner),
`database`, `property_id`. Returns `{"id": "..."}`.

#### `add_is_subclass_of`

`child_id`, `parent_id`, `database` — both ids must be the same kind (both
classes or both relations). Returns `{"status": "ok"}`.

#### `attach_property`

`target_id`, `property_id`, `database` — wires an existing PROPERTY to another
CLASS or RELATION (idempotent). Use to reuse a property found via `search_schema`
instead of declaring a same-meaning duplicate under a new name. Returns
`{"status": "ok"}`.

---

### Schema tools (read)

#### `search_schema`

`query`, `mode` (`keyword` default / `semantic`), `type` (`class`/`relation`/`property`,
optional), `database`. Returns a list of `{id, type, name, description, score}`.

#### `fetch_schema`

`database` — returns the full declared schema (classes, relations, properties,
domain/range, hierarchy) as JSON (`DataSchema.to_json()` shape).

#### `fetch_meta_schema`

No arguments — returns the fixed schema describing the KB's own node/edge
taxonomy (CLASS, RELATION, PROPERTY, ENTITY, FACT, LITERAL, SOURCE, EXTRACTION).

---

### Schema expansion

#### `expand_schema`

`text`, `depth` (default `1` — hierarchy levels beyond what's directly evidenced
in `text` to also propose), `reference` (`llm` default / `mcp`), `mcp_servers`
(list of `{"transport": "stdio", "command", "args", "env"}` or
`{"transport": "sse"|"streamable_http", "url", "headers"}` — required when
`reference="mcp"`; each server's tools are additionally offered to the model,
e.g. WordNet hypernyms/synonyms via `platform/wordnet-mcp`), `dedup_mode`
(`search` default / `llm` / `none` — how it checks for an existing schema
element before declaring a new one; for `declare_class`/`declare_relation_type`
this check also runs automatically before creation, even if the model skips
`search_schema`), `model`, `database`. Runs an LLM agent that grows the schema
(CLASS/RELATION/PROPERTY), using `add_subclass_of`/`attach_property` internally
to place new elements under existing ones and reuse existing properties, and
never declaring a taxonomic (is-a/type-of/kind-of) name as a RELATION — never
touches entities, facts, or property values. Returns `{"status": "ok"}`.

---

### Knowledge tools (write — additive only)

#### `add_entity`

`name`, `class_id`, `description`, `text`, `source_id`, `database`. `text` +
`source_id` record provenance (creates an EXTRACTION node). Returns `{"id": "..."}`.

#### `add_fact`

`head_entity_id`, `relation_id`, `tail_entity_id`, `name`, `description`, `text`,
`source_id`, `database`. Returns `{"id": "..."}`.

#### `add_entity_property` / `add_fact_property`

`entity_id`/`fact_id`, `property_id`, `value`, `literal_type` (`NUMBER`/`DATE`/
`BOOLEAN`/`STRING`, inferred if omitted), `text`, `source_id`, `database`.
Returns `{"id": "..."}` (the created literal's id).

#### `add_entity_extraction` / `add_fact_extraction` / `add_entity_property_extraction` / `add_fact_property_extraction`

Link an *additional* source text span to something that already exists —
unlike `text` on the `add_*` tools above, these don't create a new node, just
another `EXTRACTED_FROM` edge, so multiple spans accumulate over time. Take the
relevant id(s), `text`, `source_id`, `database`. Returns `{"id": "..."}`.

---

### Knowledge tools (read)

#### `search_data`

`query`, `mode`, `type` (`entity`/`fact`, optional), `database`. Returns a list
of matching entities/facts.

#### `fetch_data`

`database`, `compact` (default `True` — flat readable rows; `False` — raw
`{nodes, edges}` graph form), `include_provenance`, `detailed`. With
`include_provenance`, each row carries an `evidence` list, one record per
EXTRACTION. Returns rows or the raw graph.

#### `get_metadata`

`node_id` (an entity, fact, or property-value id), `database`. Returns
`created`/`modified` timestamps, a FACT's derived `confidence`, and an
`evidence` list carrying one record per EXTRACTION.

---

### Query tools

Each of these runs its own LLM tool-calling loop against the KB — a tool call
here triggers a nested agent, not a direct lookup.

#### `answer`

`question`, `model`, `natural_language` (prose instead of structured rows),
`include_metadata` (also collect `get_metadata` for cited entities/facts/
properties — with `natural_language`, folds a provenance section into the
prose), `database`. Returns `{"result": [...]}`, or
`{"result": [...], "metadata": {...}}` with `include_metadata=True`.

#### `validate`

`statement`, `model`, `natural_language`, `include_metadata`, `database`.
Returns `{"supporting": [...], "counter": [...]}` (plus `metadata` with
`include_metadata=True`).

#### `enrich`

`query`, `depth` (default 2), `model`, `database`. Returns a list of
`{entity, relation, value, distance}`.

---

### Extraction

#### `import_text`

`text`, `queries` (guiding questions, optional), `model`, `source_id`,
`expand_schema` (default `True` — runs `expand_schema` first),
`schema_expansion_target` (any combination of `"text"`/`"queries"`, default
both — only used when `expand_schema=True`), `schema_expansion_depth`,
`schema_expansion_mode`, `schema_expansion_mcp_servers`,
`schema_expansion_dedup_mode` (these five mirror `expand_schema`'s own
`depth`/`reference`/`mcp_servers`/`dedup_mode` one-to-one), `database`. Runs an
LLM agent that extracts entities/facts/property values from `text` against the
schema as it currently stands — never declares new schema itself (a mention
with no matching element is skipped); pass `expand_schema=False` to extract
only, e.g. against a schema already grown by a prior `expand_schema` call.
Returns `{"status": "ok"}`.

---

### Provenance

#### `add_source`

`source`, `name`, `description`, `original` (the source's own date, e.g. a
document's publication date — distinct from when it was added to the KB),
`database`. Returns `{"id": "..."}`.

---

## Typical session

```
# 1. Build a small schema
declare_class("PERSON", "A human individual")
declare_class("COMPANY", "A business organization")
declare_relation("WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY")

# 2. Register a source, then add data with provenance
add_source("resume_alice", name="Alice's résumé", original="2020-01-15")
add_entity("Alice", class_id="<person-id>", text="Alice is a software engineer.", source_id="<source-id>")
add_entity("IBM", class_id="<company-id>")
add_fact(head_entity_id="<alice-id>", relation_id="<worked_at-id>", tail_entity_id="<ibm-id>",
         text="Alice joined IBM in June 1998.", source_id="<source-id>")

# 3. Or let an LLM agent expand the schema and do the extraction (both by default)
import_text("Alice joined IBM as a software engineer in June 1998.",
            queries=["Where did Alice work?"])

# 3b. Or split it: expand once, then extract many documents against it
expand_schema("Alice joined IBM as a software engineer in June 1998.")
import_text("Alice later joined Google as a senior engineer in May 2002.", expand_schema=False)

# 4. Query
search_data("Alice")
answer("Where did Alice work?", include_metadata=True)
validate("Alice worked at IBM.")
enrich("Alice", depth=2)

# 5. Inspect provenance for one item
get_metadata("<alice-id>")
```

For a LangGraph agent that connects to this server and picks tools itself (rather than a
hand-typed session like above), see
[examples/framework-demos/langgraph_mcp_demo.py](../../examples/framework-demos/langgraph_mcp_demo.py).

---

## Architecture

```
Claude / MCP client
        │  MCP (stdio transport)
        ▼
blue_data_kb_mcp.server   ← FastMCP app, 26 tool handlers (async, await KBSource directly)
        │
blue_data_kb_mcp.config   ← singleton KBSource factory (BLUE_DATA_KB_SOURCE_* env vars)
        │  DataSource.instantiate(dotted_class_path, ...)
        ▼
   KBSource                ← lib/src/blue_data_kb_source/
   ├── FileKBSource | NEO4JKBSource   (pre-built KBSource + GraphSource mixins)
   └── KBSearchIndexer     ← BM25 | BM25 + semantic (optional)
```

The server holds a single `KBSource` instance per process. All tool calls share
that instance; `database` is a plain string argument selecting a logical
database within it (multiple databases are supported on one backend). Unlike
the `kb` CLI, this server does not read `~/.kbdb/registry.json` — it depends
only on `blue-data-kb-source`, not on `blue-data-kb-cli`, so it stays an
independently deployable unit (see `design/07-repo-organization.md`).

With `BLUE_DATA_KB_SOURCE_GRAPH_BACKEND=file` (default), state persists across
server restarts in `BLUE_DATA_KB_SOURCE_DIRECTORY`. With `neo4j`, state persists
in the connected Neo4j instance.

---

## Running tests

```bash
cd platform/mcp
python3 -m pytest tests/ -v
```

Tests inject an in-memory `KBSource` directly — no file I/O, no network, no
`OPENAI_API_KEY` required, except the `answer`/`validate`/`enrich`/`expand_schema`/`import_text`
tests, which mock `openai.AsyncOpenAI`.
