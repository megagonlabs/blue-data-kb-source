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
company  = kb.declare_class("main", name="COMPANY",  description="A business organization")
employee = kb.declare_class("main", name="EMPLOYEE", description="A person employed by an organization")
kb.add_is_subclass_of("main", employee, person)

worked_at = kb.declare_relation_type(
    "main", name="WORKED_AT",
    description="Employment relationship between a person and a company",
    is_symmetric=False,
)
kb.add_domain("main", worked_at, employee)
kb.add_range("main",  worked_at, company)

start_date_prop = kb.declare_relation_property(
    "main", name="START_DATE",
    description="Date when employment began",
    relation_id=worked_at,
)

# --- add instances ---
alice = kb.add_entity("main", name="Alice", entity_id="alice", class_id=employee)
ibm   = kb.add_entity("main", name="IBM",   entity_id="ibm",   class_id=company)

# --- add a fact ---
fact = kb.add_fact("main", relation_id=worked_at, head_entity_id=alice, tail_entity_id=ibm)

# --- attach a property value to the fact ---
kb.add_fact_property_value("main", value="1998-06-01", literal_type="DATE",
                           fact_id=fact, property_id=start_date_prop)

# --- add provenance ---
src        = kb.add_source("main", source="resume_alice", name="Alice's résumé", description="PDF résumé")
extraction = kb.add_extraction("main", text="Alice joined IBM in June 1998", source_id=src)
kb.add_extracted_from("main", fact, extraction)
```

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
| `declare_relation_type(database, name, description, is_symmetric?, relation_id?)` | `str` | Add a `:RELATION` node |
| `declare_class_property(database, name, description, property_id?, class_id?)` | `str` | Add a `:PROPERTY` node; optionally wires `DEFINE_PROPERTY` from a class |
| `declare_relation_property(database, name, description, property_id?, relation_id?)` | `str` | Add a `:PROPERTY` node; optionally wires `DEFINE_PROPERTY` from a relation |

#### Instance node builders

| Method | Returns | Description |
|---|---|---|
| `add_entity(database, name, description?, entity_id?, class_id?)` | `str` | Add an `:ENTITY` node; optionally wires `IS_INSTANCE_OF` to a class |
| `add_fact(database, name?, description?, fact_id?, head_entity_id?, tail_entity_id?, relation_id?)` | `str` | Add a `:FACT` node; optionally wires `HEAD`, `TAIL`, and `REPRESENTS` |
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
| `add_define_property(database, class_or_relation_id, property_id)` | `DEFINE_PROPERTY` | Declares that a class/relation has a given property |
| `add_domain(database, relation_id, class_id)` | `DOMAIN` | Valid HEAD entity type for a relation |
| `add_range(database, relation_id, class_id)` | `RANGE` | Valid TAIL entity type for a relation |

#### Instance edge builders

| Method | Edge | Description |
|---|---|---|
| `add_is_instance_of(database, entity_id, class_id)` | `IS_INSTANCE_OF` | Asserts an entity is an instance of a class |
| `add_represents(database, fact_id, relation_id)` | `REPRESENTS` | Grounds a fact in its relation type |
| `add_head(database, fact_id, entity_id)` | `HEAD` | Subject (from) entity of a fact |
| `add_tail(database, fact_id, entity_id)` | `TAIL` | Object (to) entity of a fact |
| `add_has_property(database, entity_or_fact_id, literal_id)` | `HAS_PROPERTY` | Assigns a literal value to an entity or fact |
| `add_is_value_of(database, literal_id, property_id)` | `IS_VALUE_OF` | Links a literal to the property it instantiates |

#### Provenance edge builder

| Method | Edge | Description |
|---|---|---|
| `add_extracted_from(database, element_id, extraction_id)` | `EXTRACTED_FROM` | Links an entity, literal, or fact to its source extraction |

---

#### Composite helpers

| Method | Returns | Description |
|---|---|---|
| `add_entity_property_value(database, value, name?, description?, literal_type?, entity_id?, property_id?)` | `str` | Creates a `:LITERAL`, wires `IS_VALUE_OF` to a property and `HAS_PROPERTY` from an entity; returns the literal node id |
| `add_fact_property_value(database, value, name?, description?, literal_type?, fact_id?, property_id?)` | `str` | Creates a `:LITERAL`, wires `IS_VALUE_OF` to a property and `HAS_PROPERTY` from a fact; returns the literal node id |

---

#### Schema query and import

| Method | Returns | Description |
|---|---|---|
| `fetch_database_schema(database)` | `DataSchema` | Build a `DataSchema` from the `:CLASS` and `:RELATION` nodes declared in *database* |
| `fetch_meta_schema()` *(static)* | `DataSchema` | Return the fixed `DataSchema` describing the KB graph schema itself (node and edge type taxonomy) |
| `import_database_schema(database, schema_json)` | `KBSource` | Populate *database* from a `DataSchema.to_json()` dict; declares classes, properties, relations, and domain/range edges; returns `self` |

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
