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

`KBSource` is abstract — subclass it together with a concrete `GraphSource` backend (e.g. `MemoryGraphSource`, `FileGraphSource`, or `Neo4jGraphSource`).

```python
from blue_data_kb_source import KBSource
from blue_data.source.graph.memory_graph_source import MemoryGraphSource

class MemoryKBSource(KBSource, MemoryGraphSource):
    pass

kb = MemoryKBSource(name="my-kb")
kb.create_database("main")

# --- declare schema ---
person   = kb.declare_class("main", name="PERSON",   description="A human individual")
person_name = kb.declare_class_property("main", name="NAME", description="Name of the individual", class_id=person)

organization  = kb.declare_class("main", name="ORGANIZATION",  description="A organization")

company  = kb.declare_class("main", name="COMPANY",  description="A business organization")
company_name = kb.declare_class_property("main", name="NAME", description="Name of the company", class_id=company)

employee = kb.declare_class("main", name="EMPLOYEE", description="A person employed by an organization")

kb.add_is_subclass_of("main", employee, person)

worked_at = kb.declare_relation_type(
    "main", name="WORKED_AT",
    description="Employment relationship between a person and a company",
    is_symmetric=False,
    domain="EMPLOYEE",   # wires DOMAIN edge inline
    range_="COMPANY",    # wires RANGE edge inline
)

start_date_prop = kb.declare_relation_property(
    "main", name="START_DATE",
    description="Date when employment began",
    relation_id=worked_at,
)

# --- add provenance source ---
src = kb.add_source("main", source="resume_alice", name="Alice's résumé", description="PDF résumé")

# --- add instances (with inline provenance) ---
alice = kb.add_entity("main", name="Alice", entity_id="alice", class_id=employee,
                      text="Alice is a software engineer.", source_id=src)
kb.add_entity_property_value("main", "Alice", literal_type="STRING",
                             entity_id=alice, property_id=person_name,
                             text="Alice is a software engineer.", source_id=src)

ibm = kb.add_entity("main", name="IBM", entity_id="ibm", class_id=company)

# --- add a fact ---
fact = kb.add_fact("main", relation_id=worked_at, head_entity_id=alice, tail_entity_id=ibm,
                   text="Alice joined IBM in June 1998.", source_id=src)

# --- attach a property value to the fact ---
kb.add_fact_property_value("main", value="1998-06-01", literal_type="DATE",
                           fact_id=fact, property_id=start_date_prop,
                           text="Alice joined IBM in June 1998.", source_id=src)

# --- provenance wiring without inline text: use add_extraction + add_extracted_from ---
extraction = kb.add_extraction("main", text="Alice joined IBM in June 1998.", source_id=src)
kb.add_extracted_from("main", ibm, extraction)
```

### Extracting knowledge from text (LLM)

`import_text` runs an OpenAI agent that reads a text passage, expands the schema as needed, and populates entities, facts, and property values. Each created node is automatically wired to its own `:EXTRACTION` node containing the verbatim text span the LLM identified.

```python
import os
os.environ["OPENAI_API_KEY"] = "sk-..."

# Create a source node first (optional but recommended for provenance)
src = kb.add_source("main", source="resume_alice", name="Alice's résumé")

kb.import_text(
    "main",
    text="Alice joined IBM as a software engineer in June 1998. She later moved to Google in 2005.",
    queries=["Where did Alice worked?", "When did she start?"],
    source_id=src,   # id of an existing :SOURCE node (optional)
    model="gpt-4o",  # default
)
```

The agent will:
1. Inspect the current schema (classes and relations already declared).
2. Declare any missing `CLASS`, `RELATION`, or `PROPERTY` nodes.
3. Call `add_entity`, `add_fact`, `add_entity_property_value`, etc. to populate the KB.
4. For each item, pass the verbatim source span as `text` so that individual `:EXTRACTION` nodes are created and linked via `EXTRACTED_FROM`.

Install the optional OpenAI extra first:

```bash
pip install "blue-data-kb-source[openai]"
```

### Change hooks

Register a callback on the KB instance to be notified whenever an entity, fact, or property value is added to a specific database. Multiple hooks can be registered for the same database; all fire in registration order.

```python
def on_kb_change(event: dict) -> None:
    t = event["type"]
    if t == "entity_added":
        print(f"entity  {event['name']!r}  (class: {event['class_name']})")
    elif t == "fact_added":
        print(f"fact    {event['head_entity_name']} —{event['relation_name']}→ {event['tail_entity_name']}")
    elif t == "entity_property_added":
        print(f"entity property  {event['property_name']} = {event['value']!r}")
    elif t == "fact_property_added":
        print(f"fact property    {event['head_entity_name']} —{event['relation_name']}→ {event['tail_entity_name']}"
              f"  {event['property_name']} = {event['value']!r}")

kb.add_change_hook("main", on_kb_change)

# Now every write to "main" fires the hook:
alice = kb.add_entity("main", name="Alice", class_id=employee)
# → entity  'Alice'  (class: EMPLOYEE)

fact = kb.add_fact("main", relation_id=worked_at, head_entity_id=alice, tail_entity_id=ibm)
# → fact    Alice —WORKED_AT→ IBM

kb.add_entity_property_value("main", "1990-03-15", entity_id=alice, property_id=person_name)
# → entity property  NAME = '1990-03-15'

kb.add_fact_property_value("main", "1998-06-01", fact_id=fact, property_id=start_date_prop)
# → fact property    Alice —WORKED_AT→ IBM  START_DATE = '1998-06-01'

# Writes to other databases are not observed:
kb.add_entity("other", name="Bob", class_id=employee)   # hook does not fire

# Remove when no longer needed:
kb.remove_change_hook("main", on_kb_change)
```

Each event dict always contains `type`, `database`, and `id` (the created node id). Extra fields per type:

| `type` | extra fields |
|---|---|
| `entity_added` | `name`, `class_id`, `class_name` |
| `fact_added` | `head_entity_id`, `head_entity_name`, `tail_entity_id`, `tail_entity_name`, `relation_id`, `relation_name` |
| `entity_property_added` | `entity_id`, `property_id`, `property_name`, `value` |
| `fact_property_added` | `fact_id`, `property_id`, `property_name`, `value`, `head_entity_name`, `tail_entity_name`, `relation_name` |

Name fields are `None` when the corresponding id was not provided.

### Reading and importing a schema

Export the schema of an existing database to JSON and reload it into a fresh KB:

```python
# --- export ---
main_schema_json = kb.fetch_database_schema("main").to_json()

# --- import into a new database---
kb.create_database("copy")
kb.import_database_schema("copy", main_schema_json)
```

## API

### `KBSource`

Abstract base class. Extend it alongside a concrete `GraphSource` implementation. Inherits all `GraphSource` primitives (`add_node`, `add_edge`, `get_node`, `get_neighbors`, …).

---

#### Schema node builders

All four `declare_*` methods are **idempotent**: if a node with the same name already exists they return its id without inserting a duplicate.

| Method | Returns | Description |
|---|---|---|
| `declare_class(database, name, description, class_id?)` | `str` | Add a `:CLASS` node |
| `declare_relation_type(database, name, description, is_symmetric?, relation_id?, domain?, range_?)` | `str` | Add a `:RELATION` node; `domain`/`range_` accept a class name or list of class names and wire `DOMAIN`/`RANGE` edges |
| `declare_class_property(database, name, description, property_id?, class_id?)` | `str` | Add a `:PROPERTY` node; optionally wires `DEFINE_PROPERTY` from a class |
| `declare_relation_property(database, name, description, property_id?, relation_id?)` | `str` | Add a `:PROPERTY` node; optionally wires `DEFINE_PROPERTY` from a relation |

#### Instance node builders

| Method | Returns | Description |
|---|---|---|
| `add_entity(database, name, description?, entity_id?, class_id?, text?, source_id?)` | `str` | Add an `:ENTITY` node; optionally wires `IS_INSTANCE_OF`; when `text` is given creates an `:EXTRACTION` (linked to `source_id`) and wires `EXTRACTED_FROM` |
| `add_fact(database, name?, description?, fact_id?, head_entity_id?, tail_entity_id?, relation_id?, text?, source_id?)` | `str` | Add a `:FACT` node; optionally wires `HEAD`, `TAIL`, `REPRESENTS`; provenance via `text`/`source_id` |
| `add_literal(database, value, name?, description?, literal_type?, property_id?, node_id?)` | `str` | Add a `:LITERAL` node; `literal_type` is inferred from value type if omitted; optionally wires `IS_VALUE_OF` |

#### Provenance node builders

| Method | Returns | Description |
|---|---|---|
| `add_source(database, source, name?, description?, node_id?)` | `str` | Add a `:SOURCE` node; `name` defaults to `source` if omitted |
| `add_extraction(database, text, name?, description?, node_id?, source_id?)` | `str` | Add an `:EXTRACTION` node; optionally wires `EXTRACTED_FROM` to a source |

---

#### Schema edge builders

| Method | Edge | Description |
|---|---|---|
| `add_is_subclass_of(database, child_id, parent_id)` | `IS_SUBCLASS_OF` | Hierarchical inheritance between classes or relations |
| `add_domain(database, relation_id, class_id)` | `DOMAIN` | Valid HEAD entity type for a relation |
| `add_range(database, relation_id, class_id)` | `RANGE` | Valid TAIL entity type for a relation |
| `add_extracted_from(database, element_id, extraction_id)` | `EXTRACTED_FROM` | Links an entity, fact, or literal to its source extraction |

---

#### Composite helpers

| Method | Returns | Description |
|---|---|---|
| `add_entity_property_value(database, value, name?, description?, literal_type?, entity_id?, property_id?, text?, source_id?)` | `str` | Creates a `:LITERAL`, wires `IS_VALUE_OF` and `HAS_PROPERTY` from an entity; provenance via `text`/`source_id` |
| `add_fact_property_value(database, value, name?, description?, literal_type?, fact_id?, property_id?, text?, source_id?)` | `str` | Creates a `:LITERAL`, wires `IS_VALUE_OF` and `HAS_PROPERTY` from a fact; provenance via `text`/`source_id` |

---

#### Change hooks

| Method | Description |
|---|---|
| `add_change_hook(database, fn)` | Register *fn* to be called after every instance-level write to *database*; *fn* receives a single event `dict` |
| `remove_change_hook(database, fn)` | Deregister a previously added hook for *database* (no-op if not found) |

---

#### Schema query and import

| Method | Returns | Description |
|---|---|---|
| `fetch_database_schema(database)` | `DataSchema` | Build a `DataSchema` from the `:CLASS` and `:RELATION` nodes declared in *database* |
| `fetch_meta_schema()` *(static)* | `DataSchema` | Return the fixed `DataSchema` describing the KB graph schema itself (node and edge type taxonomy) |
| `import_database_schema(database, schema_json)` | `KBSource` | Populate *database* from a `DataSchema.to_json()` dict; declares classes, properties, relations, and domain/range edges; returns `self` |
| `import_text(database, text, queries?, model?, source_id?)` | `None` | Use an OpenAI LLM to extract entities, facts, and properties from *text* into *database*; expands the schema as needed; each created node gets its own `:EXTRACTION` wired to `source_id`. Requires `openai`. |

---

### Literal types

`literal_type` must be one of: `"NUMBER"`, `"DATE"`, `"BOOLEAN"`, `"STRING"`. When omitted, it is inferred from the Python type of `value` (`int`/`float` → `NUMBER`, `bool` → `BOOLEAN`, everything else → `STRING`).

## Running tests

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
│       ├── __init__.py   # public exports
│       └── kb_source.py  # KBSource abstract class
└── tests/
    ├── unit/
    └── integration/
```
