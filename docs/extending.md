# Extending blue-data-kb-source

`KBSource` itself has no plugin/hook mechanism for its own behavior — schema and knowledge operations, provenance, and change hooks are fixed. The two things this library is designed to have swapped out are the **graph storage backend** and the **vector search backend**, both via the sibling [`blue-data`](../../blue-data) library's abstractions. This page covers implementing a new one of each, plus what to know if you want to change how LLM calls are made.

## Implementing a new GraphSource backend

Subclass `GraphSource` (from `blue_data.source.graph.graph_source`) and implement its abstract methods:

```python
from blue_data.source.graph.graph_source import GraphSource

class MyGraphSource(GraphSource):
    def add_node(self, database, label, properties=None, node_id=None, transaction_id=None) -> str: ...
    def get_node(self, database, node_id, transaction_id=None) -> dict | None: ...
    def update_node(self, database, node_id, properties, transaction_id=None) -> None: ...
    def delete_node(self, database, node_id, transaction_id=None) -> None: ...
    def match_nodes(self, database, label=None, properties=None, transaction_id=None) -> list[dict]: ...

    def add_edge(self, database, source_id, edge_type, target_id, properties=None, transaction_id=None) -> str: ...
    def update_edge(self, database, source_id, edge_type, target_id, properties, transaction_id=None) -> None: ...
    def delete_edge(self, database, source_id, edge_type, target_id, transaction_id=None) -> None: ...
    def match_edges(self, database, edge_type=None, source_id=None, target_id=None, properties=None, transaction_id=None) -> list[dict]: ...

    def get_neighbors(self, database, node_id, edge_type=None, direction="both", transaction_id=None) -> list[dict]: ...
```

`GraphSource` extends `DataSource`, which provides no-op defaults for `create_database`/`fetch_databases`/etc. — override `create_database` at minimum so `kb.create_database(name)` actually provisions storage for a new logical database.

**Node/edge shape.** `add_node` returns a new node id; `get_node`/`match_nodes` return dicts shaped `{"id": ..., "label": ..., "properties": {...}}`. `update_node` **merges** the given properties into the existing dict rather than replacing it wholesale — `KBSource`'s timestamp helpers (`created`/`modified`, see [api-reference.md](api-reference.md#timestamps-and-provenance)) depend on this: an update call only ever sends the changed keys plus a refreshed `modified`, and expects `created` and every untouched field to survive automatically via the merge.

**Transactions** (`begin_transaction`/`commit_transaction`/`rollback_transaction`/`transaction`) have base-class defaults — override them only if your backend can offer real atomicity across multiple writes; `KBSource` itself doesn't require it.

Once implemented, mix it into `KBSource` via Python MRO — no factory, no registry required for the mixing itself:

```python
from blue_data_kb_source import KBSource

class MyKBSource(KBSource, MyGraphSource):
    pass

kb = MyKBSource(name="my-kb", properties={"connection": {...}})
```

`KBSource.__init__(name, properties=None)` matches `DataSource`'s own signature exactly — `MyGraphSource` should too (no extra constructor kwargs of its own), so anything backend-specific belongs under `properties["connection"]`, read via your own `_initialize_connection_properties` override (see the three shipped mixins — `memory_kb_source.py`, `file_kb_source.py`, `neo4j/neo4j_kb_source.py` — for the pattern). Doing this also means your class can be constructed dynamically from a config-driven backend choice via `DataSource.instantiate("mypackage.MyKBSource", "my-kb", properties=...)`, the same mechanism the CLI's registry and MCP server use for the built-in backends.

The three existing backends (`MemoryGraphSource`, `FileGraphSource`, `NEO4JSource`, all in `blue_data.source.graph`) are the reference implementations to read alongside this — `FileGraphSource` in particular is a good template for a from-scratch persistent backend, since it implements the full interface without a database server dependency.

---

## Implementing a new VectorSource backend

Semantic search (`mode="semantic"` on `search_schema`/`search_data`, or the `answer`/`validate`/`enrich` agents' tools) is backed by a pluggable `VectorSource`, independent of the graph backend. All implementations share one `execute_query` interface with four operations:

```python
# Create an index
source.execute_query({
    "op": "VECTOR.CREATE_INDEX", "index": "kb_index", "collection": "entities",
    "dim": 1536, "distance_metric": "COSINE",
})

# Upsert a document with its embedding
source.execute_query({
    "op": "VECTOR.UPSERT", "collection": "entities", "id": "<node-id>",
    "doc": {"embedding": [...], "name": "Alice", ...}, "embedding_field": "embedding",
})

# Delete
source.execute_query({"op": "VECTOR.DELETE", "collection": "entities", "id": "<node-id>"})

# KNN search
results = source.execute_query({
    "op": "VECTOR.KNN", "index": "kb_index", "vector": [...], "k": 10,
    "return_fields": ["name", "description"],
})
# → [{"id": "...", "score": 0.12, "name": "...", ...}, ...]  (score = cosine distance, lower = closer)
```

Implement this interface (see `blue_data.source.vector.memory_vector_source.MemoryVectorSource` for the simplest reference, or `redis_vector_source.RedisVectorSource` for a production-shaped one). `KBSource.__init__(name, properties=None)` takes no other constructor arguments — matching `DataSource`'s own signature — so search config lives entirely under `properties["search"]`, either as an already-live object or as a declarative `_class` path `DataSource.instantiate()` constructs internally:

```python
# Live object -- for in-memory/programmatic use, nothing here needs to be JSON-safe
kb = MyKBSource(
    name="my-kb",
    properties={
        "search": {
            "mode": "semantic",
            "vector_source": MyVectorSource(),
            "embedding_fn": my_embedding_fn,
            "vector_dim": 1536,
        }
    },
)

# Declarative -- reconstructible from nothing but this dict (e.g. persisted in a registry)
kb2 = MyKBSource(
    name="my-kb",
    properties={
        "search": {
            "mode": "semantic",
            "vector_dim": 1536,
            "vector": {"_class": "mypackage.my_vector_source.MyVectorSource", "connection": {}},
            "embedding": {"provider": "openai", "model": "text-embedding-3-small"},
        }
    },
)
```

`embedding_fn` is called **synchronously** from `KBSearchIndexer` — if your embedding call is naturally async (e.g. an async HTTP client), wrap it in a sync call (`asyncio.run`, or a sync SDK client) rather than passing an `async def`. The `"embedding"` declarative form only supports `provider: "openai"` today (built by `KBSource.__init__`'s own `_build_embedding_fn` helper) — a custom provider needs the live-callable form.

The CLI and MCP server both already wire up `memory`/`file`/`chromadb`/`redis` vector backends behind a `--vector-backend`/`BLUE_DATA_KB_SOURCE_VECTOR_BACKEND` option — see `platform/cli/src/blue_data_kb_cli/config.py`'s `_vector_properties` for the exact `_class` path table if you're adding a new backend there too.

---

## Swapping the LLM provider

`ExtractionEngine` (`import_text`) and `QueryEngine` (`answer`/`validate`/`enrich`) call the OpenAI SDK directly — there is no provider-agnostic abstraction layer, by deliberate design choice (see ADR-005 in [`design/13-decision-log.md`](../design/13-decision-log.md)): KBDB's LLM usage is narrow (one tool-use loop per engine), so the overhead of an abstraction layer wasn't judged worth it. There's no `llm_fn` injection point like `blue-data`'s `MetaData` enricher has.

To use a different provider today, the two engines (`lib/src/blue_data_kb_source/extraction/engine.py`, `lib/src/blue_data_kb_source/knowledge/engine.py`) are where you'd change the `openai.AsyncOpenAI()` client construction and the tool-calling loop — both are self-contained, so the change doesn't ripple into `SchemaManager`/`KnowledgeManager`/the rest of the library. If you do this, keep the tool-calling contract intact (`return_result` as the terminal tool, `tool_choice="auto"` semantics) since the system prompts and result-parsing logic assume it.
