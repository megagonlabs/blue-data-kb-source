# blue-data-kb-source

Python library for building and querying knowledge-base graphs with a typed schema layer on top of `blue-data`'s `GraphSource`.

## Requirements

- Python 3.10+
- [`blue-data`](../../blue-data/lib) (local dependency)

## Installation

The package is not yet published to PyPI. Use the provided install script, which installs `blue-data` from its local path first:

```bash
cd lib
bash scripts/install.sh
```

Or manually:

```bash
pip install -e ../../blue-data/lib
pip install -e ".[dev]"
```

## Development setup

```bash
cd lib
python -m venv .venv
source .venv/bin/activate
bash scripts/install.sh
```

## Usage

`KBSource` is abstract. Pick a ready-made backend: `MemoryKBSource`, `FileKBSource` (both `from blue_data_kb_source import ...`), or `NEO4JKBSource` (`from blue_data_kb_source.neo4j.neo4j_kb_source import NEO4JKBSource` — kept out of the top-level package so plain `import blue_data_kb_source` never requires the `neo4j` driver). To wire up a new backend from scratch, subclass `KBSource` together with a concrete `GraphSource` directly.

> **All I/O-bound methods are `async def`.** Use them inside an `async` function and run with `asyncio.run(…)` or an async framework.

```python
import asyncio
from blue_data_kb_source import MemoryKBSource

async def main():
    kb = MemoryKBSource(name="my-kb")
    kb.create_database("main")

    # --- declare schema ---
    person      = await kb.declare_class("main", name="PERSON",   description="A human individual")
    person_name = await kb.declare_class_property("main", name="NAME", description="Name of the individual", class_id=person)

    company     = await kb.declare_class("main", name="COMPANY",  description="A business organization")
    employee    = await kb.declare_class("main", name="EMPLOYEE", description="A person employed by an organization")

    await kb.add_is_subclass_of("main", employee, person)

    worked_at = await kb.declare_relation_type(
        "main", name="WORKED_AT",
        description="Employment relationship between a person and a company",
        is_symmetric=False,
        domain="EMPLOYEE",   # required; wires DOMAIN edge inline
        range="COMPANY",     # required; wires RANGE edge inline
    )

    start_date_prop = await kb.declare_relation_property(
        "main", name="START_DATE",
        description="Date when employment began",
        relation_id=worked_at,
    )

    # --- add provenance source ---
    src = await kb.add_source("main", source="resume_alice", name="Alice's résumé", description="PDF résumé")

    # --- add instances (with inline provenance) ---
    alice = await kb.add_entity("main", name="Alice", entity_id="alice", class_id=employee,
                                text="Alice is a software engineer.", source_id=src)
    await kb.add_entity_property_value("main", "Alice", literal_type="STRING",
                                       entity_id=alice, property_id=person_name,
                                       text="Alice is a software engineer.", source_id=src)

    ibm  = await kb.add_entity("main", name="IBM", entity_id="ibm", class_id=company)

    # --- add a fact ---
    fact = await kb.add_fact("main", relation_id=worked_at, head_entity_id=alice, tail_entity_id=ibm,
                             text="Alice joined IBM in June 1998.", source_id=src)

    # --- attach a property value to the fact ---
    await kb.add_fact_property_value("main", value="1998-06-01", literal_type="DATE",
                                     fact_id=fact, property_id=start_date_prop,
                                     text="Alice joined IBM in June 1998.", source_id=src)

    # --- provenance wiring without inline text: add_extraction can link in one call ---
    extraction = await kb.add_extraction("main", text="Alice joined IBM in June 1998.",
                                         source_id=src, element_id=ibm)

asyncio.run(main())
```

### Querying the knowledge base

`answer`, `validate`, and `enrich` run an LLM agent (via `QueryEngine`) that is given the current schema and a set of read-only graph tools. The agent reasons over the graph, calls tools to gather evidence, and returns a structured result. Requires `openai` and `OPENAI_API_KEY`. Pass `on_tool_call(name, args)` to observe each tool call as it happens (e.g. to drive a progress indicator). `max_turns` (default `25`) caps the number of agent turns; if the agent hasn't finished by then, the loop stops silently and returns an empty/default result rather than raising.

```python
import os
os.environ["OPENAI_API_KEY"] = "sk-..."

async def query(kb):
    # Answer a natural language question — LLM agent traverses entities and facts
    answers = await kb.answer("main", "Where did Alice work?")
    for a in answers:
        print(a.entity, a.relation, a.value, a.provenance)

    # Validate a statement — LLM agent returns supporting and counter evidence
    result = await kb.validate("main", "Alice worked at IBM")
    print(result.supporting)   # list of {fact, head, tail, relation} dicts
    print(result.counter)      # list of contradicting evidence

    # Enrich — LLM agent BFS from seed entities up to depth hops
    related = await kb.enrich("main", "Alice", depth=2)
    for r in related:
        print(r.entity, r.relation, r.distance)

    # All three accept an optional model parameter (default: "gpt-4o")
    answers = await kb.answer("main", "Where did Alice work?", model="gpt-4o-mini")

    # Search the schema by keyword (no LLM — direct name/description match)
    hits = await kb.search_schema("main", "employment")
    for h in hits:
        print(h.type, h.name, h.description)

    # Merge two schema elements and rewire their instances to the target
    await kb.consolidate_schema("main", operation="merge",
                                source_names=["EMPLOYEE", "WORKER"],
                                target_name="EMPLOYEE")
```

### Extracting knowledge from text (LLM)

`import_text` runs an OpenAI agent that reads a text passage, expands the schema as needed, and populates entities, facts, and property values. Each created node is automatically wired to its own `:EXTRACTION` node containing the verbatim text span the LLM identified. If an entity or fact is found to already exist (via `find_entity`/`find_fact`), the agent attaches the new text as additional evidence instead of creating a duplicate, so an item can end up with multiple `EXTRACTED_FROM` edges as more evidence is discovered across extraction runs. Pass `on_tool_call(name, args)` to observe each tool call as it happens (e.g. to drive a progress indicator).

```python
import os
os.environ["OPENAI_API_KEY"] = "sk-..."

async def extract(kb):
    # Create a source node first (optional but recommended for provenance)
    src = await kb.add_source("main", source="resume_alice", name="Alice's résumé")

    await kb.import_text(
        "main",
        text="Alice joined IBM as a software engineer in June 1998. She later moved to Google in 2005.",
        queries=["Where did Alice work?", "When did she start?"],
        source_id=src,   # id of an existing :SOURCE node (optional)
        model="gpt-4o",  # default
    )
```

The agent will:
1. Inspect the current schema (classes and relations already declared).
2. Before declaring a new `CLASS`, `RELATION`, or `PROPERTY`, call `search_schema` to check whether an existing element already covers the same meaning under a different name (e.g. a `JOIN_DATE` property when `START_DATE` already exists) — see `schema_discovery_mode` below.
3. Declare any missing `CLASS`, `RELATION`, or `PROPERTY` nodes.
4. Call `add_entity`, `add_fact`, `add_entity_property_value`, etc. to populate the KB.
5. For each item, pass the verbatim source span as `text` so that individual `:EXTRACTION` nodes are created and linked via `EXTRACTED_FROM`.
6. When an entity or fact already exists, call `link_extraction` instead of re-creating it, adding a new `EXTRACTED_FROM` edge alongside any existing ones.

`schema_discovery_mode` controls how the agent's `search_schema` tool call (step 2) is answered:
- `"search"` (default) — uses the database's configured search backend (keyword substring match, or semantic/embedding search if the database was registered with `--search semantic`).
- `"llm"` — asks the same model to judge whether any existing schema element means the same thing as the proposed one, even under different wording. More reliable for synonyms than keyword/semantic search over short names, at the cost of one extra model call per check.
- `"none"` — the `search_schema` tool isn't offered to the agent at all (pre-existing behavior).

Install the optional OpenAI extra first:

```bash
pip install "blue-data-kb-source[openai]"
```

### Change hooks

Register a callback on the KB instance to be notified whenever the schema or instance data changes in a specific database. Hooks cover both schema declarations and instance writes. Multiple hooks can be registered for the same database; all fire in registration order. Hooks are synchronous (`def`, not `async def`).

Schema events fire only when a new element is actually created — idempotent repeated declarations do not re-fire.

```python
def on_kb_change(event: dict) -> None:
    t = event["type"]
    # --- schema events ---
    if t == "class_declared":
        print(f"class      {event['name']!r}")
    elif t == "relation_declared":
        print(f"relation   {event['name']!r}  ({event['domain']} → {event['range']})")
    elif t == "class_property_declared":
        print(f"property   {event['name']!r}  on class {event['class_name']!r}")
    elif t == "relation_property_declared":
        print(f"property   {event['name']!r}  on relation {event['relation_name']!r}")
    elif t == "class_subclass":
        print(f"subclass   {event['child_name']} IS_SUBCLASS_OF {event['parent_name']}")
    elif t == "relation_subclass":
        print(f"subrelation {event['child_name']} IS_SUBCLASS_OF {event['parent_name']}")
    # --- instance events ---
    elif t == "entity_added":
        print(f"entity     {event['name']!r}  (class: {event['class_name']})")
    elif t == "fact_added":
        print(f"fact       {event['head_entity_name']} —{event['relation_name']}→ {event['tail_entity_name']}")
    elif t == "entity_property_added":
        print(f"entity property  {event['entity_name']}.{event['property_name']} = {event['value']!r}")
    elif t == "fact_property_added":
        print(f"fact property    {event['fact_name']}  {event['property_name']} = {event['value']!r}"
              f"  ({event['head_entity_name']} —{event['relation_name']}→ {event['tail_entity_name']})")

kb.add_change_hook("main", on_kb_change)

# Schema declarations fire the hook:
person   = await kb.declare_class("main", "PERSON", "A human individual")
# → class      'PERSON'

worked_at = await kb.declare_relation_type("main", "WORKED_AT", "Employment", domain="PERSON", range="COMPANY")
# → relation   'WORKED_AT'  (['PERSON'] → ['COMPANY'])

await kb.declare_class_property("main", "AGE", "Age", class_id=person)
# → property   'AGE'  on class 'PERSON'

employee = await kb.declare_class("main", "EMPLOYEE", "")
await kb.add_is_subclass_of("main", employee, person)
# → subclass   EMPLOYEE IS_SUBCLASS_OF PERSON

# Repeated declarations are idempotent and do NOT re-fire:
await kb.declare_class("main", "PERSON", "ignored")   # hook does not fire

# Instance writes fire the hook:
alice = await kb.add_entity("main", name="Alice", class_id=person)
# → entity     'Alice'  (class: PERSON)

fact = await kb.add_fact("main", relation_id=worked_at, head_entity_id=alice, tail_entity_id=ibm)
# → fact       Alice —WORKED_AT→ IBM

# Writes to other databases are not observed:
await kb.add_entity("other", name="Bob", class_id=person)   # hook does not fire

# Remove when no longer needed:
kb.remove_change_hook("main", on_kb_change)
```

Each event dict always contains `type`, `database`, and `id`. Extra fields by type:

**Schema events**

| `type` | extra fields |
|---|---|
| `class_declared` | `name`, `description` |
| `relation_declared` | `name`, `description`, `is_symmetric`, `domain` (list), `range` (list) |
| `class_property_declared` | `name`, `description`, `class_id`, `class_name` |
| `relation_property_declared` | `name`, `description`, `relation_id`, `relation_name` |
| `class_subclass` | `child_id`, `child_name`, `parent_id`, `parent_name` |
| `relation_subclass` | `child_id`, `child_name`, `parent_id`, `parent_name` |

**Instance events**

| `type` | extra fields |
|---|---|
| `entity_added` | `name`, `class_id`, `class_name` |
| `fact_added` | `name`, `head_entity_id`, `head_entity_name`, `tail_entity_id`, `tail_entity_name`, `relation_id`, `relation_name` |
| `entity_property_added` | `entity_id`, `entity_name`, `property_id`, `property_name`, `value` |
| `fact_property_added` | `fact_id`, `fact_name`, `property_id`, `property_name`, `value`, `head_entity_id`, `head_entity_name`, `tail_entity_id`, `tail_entity_name`, `relation_id`, `relation_name` |

Name and id fields are `None` when the corresponding node was not provided.

### Reading and importing a schema and its data

Export the schema of an existing database to JSON and reload it into a fresh KB. `export_database_schema` is a thin `fetch_database_schema(...).to_json()` wrapper — the symmetric counterpart to `import_database_schema`, which takes that same JSON shape back in:

```python
# --- export ---
main_schema_json = await kb.export_database_schema("main")

# --- import into a new database ---
kb.create_database("copy")
await kb.import_database_schema("copy", main_schema_json)
```

`export_database_data`/`import_database_data` do the same for instance data (entities, facts, property values) — schema first, then data, since data import resolves classes/relations/properties **by name** against whatever schema is already in the target database (schema import mints new node ids rather than preserving the originals, so the data export can't rely on the old ones):

```python
main_data_json = await kb.export_database_data("main", include_provenance=True)
await kb.import_database_data("copy", main_data_json)
```

Entities/facts in the export carry their original id as a `ref` purely as an in-payload join key (entity names aren't guaranteed unique — `add_entity` never dedups — so facts/property values reference their owner by `ref`, not by name, to avoid mis-linking when two entities share a name).

The CLI exposes the same round trip: `kb schema export -o schema.json` / `kb schema import schema.json`, then `kb data export -o data.json` / `kb data import data.json` (see [CLI usage](../platform/cli/README.md)).

For runnable versions of the patterns above with real captured output, see
[examples/backends/](../examples/backends/) (one script per `GraphSource`/`VectorSource`
combination) and [examples/feature-demos/](../examples/feature-demos/) (one script per
feature — schema declaration, provenance, change hooks, export/import, LLM extraction and
querying).

## API

### `KBSource`

Abstract base class. Extend it alongside a concrete `GraphSource` implementation. Inherits all `GraphSource` primitives (`add_node`, `add_edge`, `get_node`, `get_neighbors`, …).

All I/O-bound methods are `async def`. `add_change_hook` and `remove_change_hook` are synchronous (hooks themselves must also be synchronous `def` functions).

Every node (`CLASS`, `RELATION`, `PROPERTY`, `ENTITY`, `FACT`, `LITERAL`, `SOURCE`, `EXTRACTION`) carries `created` and `modified` timestamps (ISO-8601 UTC) alongside its other attributes: `created` is set once when the node is created, `modified` is refreshed whenever one of the `update_*` methods changes the node's own attributes (wiring an edge to/from the node does not count as a modification). Every edge also carries a `created` timestamp (no `modified` — edges aren't updated in place). `SOURCE` nodes additionally support an `original` attribute (see `add_source` below) for a caller-supplied original date, distinct from `created`.

---

#### Schema operations (→ `SchemaManager`)

All `declare_*` methods are **idempotent**: if a node with the same name already exists they return its id without inserting a duplicate.

| Method | Returns | Description |
|---|---|---|
| `await declare_class(database, name, description, class_id?)` | `str` | Add a `:CLASS` node |
| `await declare_relation_type(database, name, description, is_symmetric?, relation_id?, *, domain, range)` | `str` | Add a `:RELATION` node; `domain`/`range` are required (keyword-only), accept a class name or list of class names, and wire `DOMAIN`/`RANGE` edges |
| `await declare_class_property(database, name, description, property_id?, class_id?)` | `str` | Add a `:PROPERTY` node; optionally wires `DEFINE_PROPERTY` from a class |
| `await declare_relation_property(database, name, description, property_id?, relation_id?)` | `str` | Add a `:PROPERTY` node; optionally wires `DEFINE_PROPERTY` from a relation |
| `await add_is_subclass_of(database, child_id, parent_id)` | `None` | Wire `IS_SUBCLASS_OF` between two CLASS or RELATION nodes |
| `await fetch_database_schema(database)` | `DataSchema` | Build a `DataSchema` from the `:CLASS` and `:RELATION` nodes declared in *database* |
| `await fetch_meta_schema()` | `DataSchema` | Return the fixed `DataSchema` describing the KB graph schema itself (node and edge type taxonomy) |
| `await export_database_schema(database)` | `dict` | `fetch_database_schema(database).to_json()` — portable JSON, symmetric with `import_database_schema` |
| `await import_database_schema(database, schema_json)` | `None` | Populate *database* from a `DataSchema.to_json()`/`export_database_schema` dict; declares classes, properties, relations, and domain/range edges |
| `await search_schema(database, query)` | `list[SchemaSearchResult]` | Return CLASS/RELATION/PROPERTY nodes whose name or description contains *query* (case-insensitive) |
| `await consolidate_schema(database, operation, source_names, target_name, predicate?)` | `None` | Merge or split schema elements and rewire instances; `operation="merge"` supported |

---

#### Knowledge operations (→ `KnowledgeManager`)

| Method | Returns | Description |
|---|---|---|
| `await add_entity(database, name, description?, entity_id?, class_id?, text?, source_id?)` | `str` | Add an `:ENTITY` node; optionally wires `IS_INSTANCE_OF`; when `text` is given creates a per-item `:EXTRACTION` (linked to `source_id`) and wires `EXTRACTED_FROM` |
| `await add_fact(database, name?, description?, fact_id?, head_entity_id?, tail_entity_id?, relation_id?, text?, source_id?)` | `str` | Add a `:FACT` node; auto-names from head/relation/tail if `name` is omitted; optionally wires `HEAD`, `TAIL`, `REPRESENTS`; provenance via `text`/`source_id` |
| `await add_entity_property_value(database, value, name?, description?, literal_type?, entity_id?, property_id?, text?, source_id?)` | `str` | Create a `:LITERAL`, wire `IS_VALUE_OF` and `HAS_PROPERTY` from an entity; provenance via `text`/`source_id` |
| `await add_fact_property_value(database, value, name?, description?, literal_type?, fact_id?, property_id?, text?, source_id?)` | `str` | Create a `:LITERAL`, wire `IS_VALUE_OF` and `HAS_PROPERTY` from a fact; provenance via `text`/`source_id` |
| `await add_entity_extraction(database, entity_id, text, source_id?)` | `str` | Link a new text span to an *existing* entity — adds a new `EXTRACTED_FROM` edge without touching the entity's node, so multiple text spans can accumulate over time |
| `await add_fact_extraction(database, fact_id, text, source_id?)` | `str` | Link a new text span to an *existing* fact, same as above |
| `await add_entity_property_value_extraction(database, entity_id, property_id, text, source_id?)` | `str` | Link a new text span to an entity's existing property value (resolved via `entity_id`+`property_id`); raises if no such value exists |
| `await add_fact_property_value_extraction(database, fact_id, property_id, text, source_id?)` | `str` | Link a new text span to a fact's existing property value (resolved via `fact_id`+`property_id`); raises if no such value exists |
| `await fetch_database_data(database, include_provenance?, compact?, detailed?)` | `dict \| list` | `compact=False` (default): raw `{nodes, edges}` graph form (already includes each node's full `properties`, so `detailed` has no effect here). `compact=True`: flat denormalized rows (entity/class, property/value, fact head/relation/tail), the shape `kb data list`/`kb data list --format json` uses; `include_provenance=True` adds each row's `extraction` text and linked `source` name (via its EXTRACTED_FROM chain, if any); `detailed=True` adds each row's own `description`/`created`/`modified` (the entity for entity rows, the literal for property rows, the fact for fact rows); `include_provenance=True` and `detailed=True` together additionally add `source_original` (the linked source's `original` attribute, if any) |
| `await export_database_data(database, include_provenance?)` | `dict` | Portable, name-based export of entities/facts/property values (schema not included — export/import that separately first); symmetric with `import_database_data` |
| `await import_database_data(database, data)` | `None` | Populate *database* from an `export_database_data` dict; resolves classes/relations/properties **by name** against the target database's current schema, raising `KnowledgeError` if a reference can't be resolved |
| `await get_metadata(database, node_id)` | `dict` | created/modified/extraction/source metadata for a single entity, fact, or property-value (literal) id — raises `KnowledgeError` if `node_id` isn't one of those three types |
| `await answer(database, question, model?, natural_language?, include_metadata?, on_tool_call?, max_turns?)` | `list[dict] \| str \| tuple[..., dict]` | LLM agent answers a natural language question by traversing the instance graph. `include_metadata=True` has the agent call `get_metadata` for entities/facts/properties it cites and returns `(result, metadata)` instead of just `result` — `metadata` is a `dict[node_id, dict]`. With `natural_language=True` too, the prose answer gets a natural-language provenance section appended after the main answer (still paired with the raw `metadata` dict as element 1). `max_turns` (default `25`) caps agent turns; hitting the limit stops the loop silently and returns an empty/default result |
| `await validate(database, statement, model?, natural_language?, include_metadata?, on_tool_call?, max_turns?)` | `ValidationResult \| str \| tuple[..., dict]` | LLM agent returns supporting and counter evidence for a statement; `include_metadata`/`natural_language`/`max_turns` behave as in `answer` |
| `await enrich(database, query, depth?, model?, on_tool_call?, max_turns?)` | `list[EnrichmentResult]` | LLM agent traverses the graph up to *depth* hops from seed entities and returns related knowledge; `max_turns` (default `25`) behaves as in `answer` |

---

#### Provenance operations (→ `ProvenanceTracker`)

| Method | Returns | Description |
|---|---|---|
| `await add_source(database, source, name?, description?, node_id?, original?)` | `str` | Add a `:SOURCE` node; `name` defaults to `source` if omitted; `original` records the source's original date (e.g. publication date), separate from `created` (when it was added to the KB) |
| `await add_extraction(database, text, name?, description?, node_id?, source_id?, element_id?)` | `str` | Add an `:EXTRACTION` node; optionally wires `EXTRACTED_FROM` to a source (`source_id`) and/or links it to an existing entity/fact/literal (`element_id`) in the same call |

---

#### Change hooks (→ `ChangeNotifier`)

| Method | Description |
|---|---|
| `add_change_hook(database, fn)` | Register *fn* (a sync `def`) to be called after every instance-level write to *database*; *fn* receives a single event `dict` |
| `remove_change_hook(database, fn)` | Deregister a previously added hook for *database* (no-op if not found) |

---

#### Extraction (→ `ExtractionEngine`)

| Method | Returns | Description |
|---|---|---|
| `await import_text(database, text, queries?, model?, source_id?, schema_discovery_mode?, on_tool_call?, max_turns?)` | `None` | Use an OpenAI LLM to extract entities, facts, and properties from *text* into *database*; expands the schema as needed; each created node gets its own `:EXTRACTION` wired to `source_id`. When `source_id` resolves to an existing `:SOURCE` node, its `name`/`description` are included in the agent's system prompt as context (e.g. "Alice's résumé — submitted for her job application"), not just used for provenance wiring. `schema_discovery_mode` (`"search"` default, `"llm"`, or `"none"`) controls how the agent checks for existing schema elements before declaring near-duplicates. `on_tool_call(name, args)`, if given, is invoked synchronously before each tool dispatch. `max_turns` (default `25`) caps agent turns; hitting the limit stops the loop silently, keeping whatever was extracted before the cutoff. Requires `openai`. |

---

### Result types

| Type | Fields |
|---|---|
| `SchemaSearchResult` | `id`, `type`, `name`, `description` |
| `AnswerResult` | `entity`, `relation`, `value`, `provenance` |
| `ValidationResult` | `supporting: list[dict]`, `counter: list[dict]` |
| `EnrichmentResult` | `entity`, `relation`, `value`, `distance` |

### Exceptions

| Exception | When raised |
|---|---|
| `KBError` | Base class for all KBDB errors |
| `SchemaError` | Schema validation or consistency failures (e.g. missing domain class, a `class_id` that isn't a CLASS node) |
| `KnowledgeError` | Knowledge-layer write or query failures (e.g. an `entity_id` that doesn't exist, or refers to a FACT instead) |
| `ExtractionError` | LLM extraction failures (e.g. missing `openai` package) |
| `ProvenanceError` | Provenance-tracking failures |

`SchemaManager`/`KnowledgeManager` write methods that take an id parameter referencing an existing node
(`class_id`, `relation_id`, `property_id`, `entity_id`, `fact_id`, `source_id`, ...) validate that the
node exists and is the expected type before doing anything, raising `SchemaError`/`KnowledgeError` with
the offending id and expected type otherwise. Ids that name a *new* node being created (e.g. `class_id`
passed to `declare_class` to force a specific id) are not checked this way.

### Literal types

`literal_type` must be one of: `"NUMBER"`, `"DATE"`, `"BOOLEAN"`, `"STRING"`. When omitted, it is inferred from the Python type of `value` (`bool` → `BOOLEAN`, `int`/`float` → `NUMBER`, everything else → `STRING`).

## Running tests

Tests require `pytest-asyncio` (configured with `asyncio_mode = "auto"`). LLM calls in `answer`/`validate`/`enrich` tests are mocked via `unittest.mock.patch` — no API key needed to run the suite.

```bash
cd lib
pytest
```

### Extraction benchmark suite

`tests/benchmarks/extraction/` scores `import_text()`'s actual extraction quality against a set of gold-standard cases in `tests/benchmarks/extraction/cases/*.yaml`, spanning several domains (business, healthcare, finance, legal) and categories (entity typing, multi-type/multi-valued relations and attributes, negation/false-positive traps, intra- and cross-document coreference, schema reuse/expansion). It is **not** part of the default `pytest` run (`testpaths = ["tests/unit"]`) and is **report-only** — it never fails the build on a metric regression, only on a harness error. Requires the `openai` extra installed alongside `dev` (`pip install -e '.[dev,openai]'`).

Scoring combines two techniques: entity/fact/property recall & precision use an LLM-judge alignment (semantic, tolerant of wording differences), while schema-reuse/expansion, value **normalization** (does a matched property hold the correctly normalized value/type, not just something an LLM judge calls "close enough"), **provenance** (does every created item's text span actually appear in the source, catching fabricated evidence), and **domain/range compliance** (does every fact's head/tail entity class actually satisfy the relation's domain/range) are checked deterministically by reading the resulting graph directly. Each case file is a single `input_text`/`queries`, or a `steps: [{input_text, queries}, ...]` list for cases that run several `import_text` passages in sequence against the same database (e.g. cross-document entity fusion) — `expected` describes the cumulative graph after all steps.

```bash
cd lib
pytest tests/benchmarks -s          # replays committed VCR cassettes; prints a score table
python -m tests.benchmarks.extraction.run --model gpt-4o --model gpt-4o-mini   # live, real API calls
python -m tests.benchmarks.extraction.run --record   # refresh the cassettes the pytest suite replays
```

The live paths require `OPENAI_API_KEY`. Without it and without a cassette already recorded for a given (case, `schema_discovery_mode`) pair, `pytest tests/benchmarks` skips that case rather than failing.

Each run writes two files under `.reports/` (gitignored): a JSON score report (`latest.json` for the pytest suite; `live-<timestamp>.json` for `run.py`, timestamped so successive live runs don't overwrite each other) and a companion **detailed log** (`latest.detail.jsonl` / `live-<timestamp>.detail.jsonl`) with one JSON object per (case, model, mode) run — the task spec (case description, steps, seed schema, expected results), every LLM call's full prompt messages and response (including tool calls), the raw tool-call trace (inputs and outputs), and pre-/post-run KB snapshots (schema and data, captured separately) — everything the summary table/score JSON leaves out, for debugging a specific run.

### Querying benchmark suite

`tests/benchmarks/querying/` scores `answer()`/`validate()`/`enrich()` — the read-only query agents, as opposed to extraction's write agent — against gold-standard cases in `tests/benchmarks/querying/cases/*.yaml` that seed a fixed graph (`seed_schema` + `seed_data`) and then ask one question/statement/traversal query of it. Same report-only philosophy as the extraction suite (never fails the build, only errors on a harness bug). Requires the `openai` **and** `search` extras alongside `dev` (`pip install -e '.[dev,openai,search]'`) — `search` (`rank-bm25`) is needed because cases run with `properties={"search": {"mode": "bm25"}}` (real in-memory BM25), not the raw substring fallback used when no search index is configured at all.

Cases span business, healthcare, finance, legal, and supply-chain domains, and cover categories chosen because `answer`/`validate`/`enrich` are agentic tool-callers (`search_entities`, `get_entity_facts`, `get_neighbors`, ...), not a text-to-query-language (Cypher/SPARQL) system — so there's no filter/aggregate/sort *tool*, and these categories test whether the agent does that work correctly itself over raw retrieved data: aggregation (COUNT/AVG via multi-call reasoning), constraint interpretation (temporal/numeric/categorical filters), ranking/top-k, multi-hop reasoning through `answer` itself (not just `enrich`'s BFS), complex logical composition (AND/OR/NOT), business-vocabulary grounding ("premium" → `tier=Gold`), boolean questions, and ambiguous-entity handling (scored by rewarding the agent for surfacing all plausible candidates, since there's no clarification tool in the product to fall back on — see `answer_ambiguous_entity_reference.yaml`). Graph analytics, access-control-aware querying, temporal validity intervals, confidence/data-quality-aware querying, multi-source federation, and conversational/multi-turn refinement are **not** covered — the product doesn't support them today (or, for multi-turn, `answer`/`validate`/`enrich` are single-shot with no session state), not a benchmark-design gap.

Each case runs under one or more `search_modes` (`keyword` | `semantic`, defaulting to `keyword` — a case-level knob analogous to extraction's `schema_discovery_modes`, since `search_schema`/`search_entities`/`search_facts` all take a `mode` the LLM chooses per call). `semantic` mode wires up an in-memory vector backend (`blue_data.source.vector.memory_vector_source.MemoryVectorSource`) with a real OpenAI embeddings call for `embedding_fn` — a second, separate kind of API call from the chat-completions agent loop, not wrapped by the token/cost instrumentation the way chat completions are, though VCR cassette replay covers it transparently since interception happens at the HTTP layer regardless of which client made the call.

Scoring reuses the same LLM-judge alignment (`judge_alignment`/`PRF1`) the extraction suite uses — it's already generic — plus two new checks specific to free-form query output: **schema match** (for `answer`, whose result rows have LLM-chosen column names — "choose natural column names based on the question" — a deterministic key-set-overlap check over the alignment's matched pairs catches "right values, oddly named columns" independently of value correctness; doesn't apply to `validate`/`enrich`, whose dict shapes are prompt-specified, not free-form) and **NL fidelity** (cases can set `test_natural_language: true` to also exercise `answer`/`validate`'s `natural_language=True` prose-output path — costing roughly 2x the LLM calls for that case, since the structured result is always re-fetched first regardless of the flag — scored by an LLM judge checking the prose doesn't drop or contradict the gold-standard facts).

```bash
cd lib
pytest tests/benchmarks -s
python -m tests.benchmarks.querying.run --model gpt-4o --model gpt-4o-mini
python -m tests.benchmarks.querying.run --record
```

## Project structure

```
lib/
├── pyproject.toml
├── scripts/
│   ├── build.sh       # python -m build
│   ├── install.sh     # installs blue-data (local) then this package
│   └── publish.sh     # twine upload dist/*
├── src/
│   └── blue_data_kb_source/
│       ├── __init__.py         # public exports
│       ├── _constants.py       # node label and edge type string constants
│       ├── types.py            # result types and exception hierarchy
│       ├── kb_source.py        # KBSource abstract class (coordinator)
│       ├── schema/             # SchemaManager
│       ├── knowledge/          # KnowledgeManager + QueryEngine (LLM agent)
│       ├── provenance/         # ProvenanceTracker
│       ├── notification/       # ChangeNotifier
│       └── extraction/         # ExtractionEngine (LLM agent)
└── tests/
    ├── conftest.py           # kb / populated_kb fixtures shared by unit/ and benchmarks/
    ├── unit/
    └── benchmarks/
        ├── common/            # shared harness: capture, (query_)scoring, (query_)runner, (query_)reporting
        ├── extraction/        # gold-standard cases + pytest suite + live run.py CLI (import_text)
        └── querying/          # gold-standard cases + pytest suite + live run.py CLI (answer/validate/enrich)
```
