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

`KBSource` is abstract — subclass it together with a concrete `GraphSource` backend (e.g. `MemoryGraphSource` or `Neo4jGraphSource`).

> **All I/O-bound methods are `async def`.** Use them inside an `async` function and run with `asyncio.run(…)` or an async framework.

```python
import asyncio
from blue_data_kb_source import KBSource
from blue_data.source.graph.memory_graph_source import MemoryGraphSource

class MemoryKBSource(KBSource, MemoryGraphSource):
    pass

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
        domain="EMPLOYEE",   # wires DOMAIN edge inline
        range_="COMPANY",    # wires RANGE edge inline
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

    # --- provenance wiring without inline text: use add_extraction + add_extracted_from ---
    extraction = await kb.add_extraction("main", text="Alice joined IBM in June 1998.", source_id=src)
    await kb.add_extracted_from("main", ibm, extraction)

asyncio.run(main())
```

### Querying the knowledge base

`answer`, `validate`, and `enrich` run an LLM agent (via `QueryEngine`) that is given the current schema and a set of read-only graph tools. The agent reasons over the graph, calls tools to gather evidence, and returns a structured result. Requires `openai` and `OPENAI_API_KEY`. Pass `on_tool_call(name, args)` to observe each tool call as it happens (e.g. to drive a progress indicator).

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
2. Declare any missing `CLASS`, `RELATION`, or `PROPERTY` nodes.
3. Call `add_entity`, `add_fact`, `add_entity_property_value`, etc. to populate the KB.
4. For each item, pass the verbatim source span as `text` so that individual `:EXTRACTION` nodes are created and linked via `EXTRACTED_FROM`.
5. When an entity or fact already exists, call `add_evidence` instead of re-creating it, adding a new `EXTRACTED_FROM` edge alongside any existing ones.

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

worked_at = await kb.declare_relation_type("main", "WORKED_AT", "Employment", domain="PERSON", range_="COMPANY")
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

### Reading and importing a schema

Export the schema of an existing database to JSON and reload it into a fresh KB:

```python
# --- export ---
main_schema_json = (await kb.fetch_database_schema("main")).to_json()

# --- import into a new database ---
kb.create_database("copy")
await kb.import_database_schema("copy", main_schema_json)
```

## API

### `KBSource`

Abstract base class. Extend it alongside a concrete `GraphSource` implementation. Inherits all `GraphSource` primitives (`add_node`, `add_edge`, `get_node`, `get_neighbors`, …).

All I/O-bound methods are `async def`. `add_change_hook` and `remove_change_hook` are synchronous (hooks themselves must also be synchronous `def` functions).

---

#### Schema operations (→ `SchemaManager`)

All `declare_*` methods are **idempotent**: if a node with the same name already exists they return its id without inserting a duplicate.

| Method | Returns | Description |
|---|---|---|
| `await declare_class(database, name, description, class_id?)` | `str` | Add a `:CLASS` node |
| `await declare_relation_type(database, name, description, is_symmetric?, relation_id?, domain?, range_?)` | `str` | Add a `:RELATION` node; `domain`/`range_` accept a class name or list of class names and wire `DOMAIN`/`RANGE` edges |
| `await declare_class_property(database, name, description, property_id?, class_id?)` | `str` | Add a `:PROPERTY` node; optionally wires `DEFINE_PROPERTY` from a class |
| `await declare_relation_property(database, name, description, property_id?, relation_id?)` | `str` | Add a `:PROPERTY` node; optionally wires `DEFINE_PROPERTY` from a relation |
| `await add_is_subclass_of(database, child_id, parent_id)` | `None` | Wire `IS_SUBCLASS_OF` between two CLASS or RELATION nodes |
| `await fetch_database_schema(database)` | `DataSchema` | Build a `DataSchema` from the `:CLASS` and `:RELATION` nodes declared in *database* |
| `await fetch_meta_schema()` | `DataSchema` | Return the fixed `DataSchema` describing the KB graph schema itself (node and edge type taxonomy) |
| `await import_database_schema(database, schema_json)` | `None` | Populate *database* from a `DataSchema.to_json()` dict; declares classes, properties, relations, and domain/range edges |
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
| `await answer(database, question, model?, natural_language?, on_tool_call?)` | `list[AnswerResult] \| str` | LLM agent answers a natural language question by traversing the instance graph |
| `await validate(database, statement, model?, natural_language?, on_tool_call?)` | `ValidationResult \| str` | LLM agent returns supporting and counter evidence for a statement |
| `await enrich(database, query, depth?, model?, on_tool_call?)` | `list[EnrichmentResult]` | LLM agent traverses the graph up to *depth* hops from seed entities and returns related knowledge |

---

#### Provenance operations (→ `ProvenanceTracker`)

| Method | Returns | Description |
|---|---|---|
| `await add_source(database, source, name?, description?, node_id?)` | `str` | Add a `:SOURCE` node; `name` defaults to `source` if omitted |
| `await add_extraction(database, text, name?, description?, node_id?, source_id?)` | `str` | Add an `:EXTRACTION` node; optionally wires `EXTRACTED_FROM` to a source |
| `await add_extracted_from(database, element_id, extraction_id)` | `None` | Wire `EXTRACTED_FROM` from an entity, fact, or literal to an extraction |
| `await add_evidence(database, element_id, text, source_id?)` | `str` | Create an `:EXTRACTION` from *text* and wire it to an *existing* entity, fact, or literal — adds a new `EXTRACTED_FROM` edge without touching the element's node, so multiple pieces of evidence can accumulate over time |

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
| `await import_text(database, text, queries?, model?, source_id?, on_tool_call?)` | `None` | Use an OpenAI LLM to extract entities, facts, and properties from *text* into *database*; expands the schema as needed; each created node gets its own `:EXTRACTION` wired to `source_id`. `on_tool_call(name, args)`, if given, is invoked synchronously before each tool dispatch. Requires `openai`. |

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
| `SchemaError` | Schema validation or consistency failures (e.g. missing domain class) |
| `KnowledgeError` | Knowledge-layer write or query failures |
| `ExtractionError` | LLM extraction failures (e.g. missing `openai` package) |
| `ProvenanceError` | Provenance-tracking failures |

### Literal types

`literal_type` must be one of: `"NUMBER"`, `"DATE"`, `"BOOLEAN"`, `"STRING"`. When omitted, it is inferred from the Python type of `value` (`bool` → `BOOLEAN`, `int`/`float` → `NUMBER`, everything else → `STRING`).

## Running tests

Tests require `pytest-asyncio` (configured with `asyncio_mode = "auto"`). LLM calls in `answer`/`validate`/`enrich` tests are mocked via `unittest.mock.patch` — no API key needed to run the suite.

```bash
cd lib
pytest
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
    └── unit/
```
