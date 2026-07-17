# blue-data-kb-cli

Command-line interface for the Blue Data Knowledge-Base. A thin adapter over the `blue-data-kb-source` Python library built with Click and Rich.

## Requirements

- Python 3.10+
- [`blue-data`](../../../blue-data/lib) (local dependency)
- [`blue-data-kb-source`](../../lib) (local dependency)

## Installation

```bash
cd platform/cli
bash scripts/install.sh
```

Or manually:

```bash
pip install -e ../../../blue-data/lib
pip install -e ../../lib
pip install -e .
```

For LLM features (`answer`, `validate`, `extract`), also install the `openai` extra on the library:

```bash
pip install -e "../../lib[openai]"
```

## State persistence

Each database is registered in a registry file:

```
~/.kbdb/registry.json
```

For the default `file` graph backend, data is automatically persisted as a JSON file — no explicit save step is needed:

```
~/.kbdb/{database}.json
```

`FileGraphSource` saves after every write operation, so IDs returned by one command can be safely passed to the next. Override the data directory:

```bash
export KBDB_DATA_DIR=/path/to/my-kbdb
```

## Registering a database

Use `kb database register` to create and configure a database:

```bash
# File backend, BM25 keyword search (index persisted to disk)
kb database register main --graph-backend file --search bm25

# File backend, semantic (vector) search with OpenAI embeddings, ChromaDB vector store
kb database register main \
    --graph-backend file \
    --search semantic \
    --vector-backend chromadb \
    --vector-path ~/.kbdb/chromadb \
    --embedding-provider openai \
    --embedding-model text-embedding-3-small

# Neo4j backend with Redis vector store
kb database register main \
    --graph-backend neo4j \
    --neo4j-uri bolt://localhost:7687 \
    --neo4j-user neo4j \
    --neo4j-password secret \
    --search semantic \
    --vector-backend redis \
    --redis-host localhost \
    --redis-port 6379

# Update search configuration for an existing database
kb --database main database search config set --search bm25

# View current configuration
kb --database main database config show
kb --database main database search config show

# Rebuild the search index after bulk imports or enabling search on an existing database
kb --database main data reindex
```

## Global option

```
kb [--database DB] COMMAND
```

`--database` selects the target database. The default is read from `~/.kbdb/.selected.database` (written by `kb database select`); if that file does not exist the database named `"default"` is used. Override for a single command with `--database`, or set `KBDB_DATABASE` to make it the environment-level default.

## Walkthrough

```bash
# 1. List and create databases
$ kb database list
$ kb database create main
Created database: main

# 2. Declare schema
$ kb --database main schema class add PERSON "A human individual"
Added class: PERSON (id: 3b2a906c-…)

$ kb --database main schema class property add AGE "Age of a person" --class PERSON
Added class property: AGE (id: 0d9eaaac-bf07-41e6-bd6d-34f60f4c90d7)

$ kb --database main schema class add COMPANY "A business organization"
Added class: COMPANY (id: 49710e2e-…)

$ kb --database main schema relation add WORKED_AT "Employment relationship" \
    --domain PERSON --range COMPANY
Added relation: WORKED_AT (id: 67a75f53-…)

$ kb --database main schema relation property add START_DATE "Start of employment" \
    --relation WORKED_AT
Added relation property: START_DATE (id: 8c3d0a1e-…)

# 3. Register a provenance source
$ kb --database main data source add resume_alice --name "Alice's résumé"
Added source: resume_alice (id: a1b2c3d4-…)

# 4. Add entity instances (class and source resolved by name)
$ kb --database main data entity add --name Alice --class PERSON \
    --text "Alice is a software engineer." --source "Alice's résumé"
Added entity: Alice (id: d52378c9-…)

$ kb --database main data entity add --name IBM --class COMPANY
Added entity: IBM (id: 5b1a47fb-…)

# 5. Set and list entity property values
$ kb --database main data entity property set 42 \
    --entity Alice --property AGE --type NUMBER
Set entity property (literal id: 1a2b3c4d-…)

$ kb --database main data entity property list --entity Alice
┏━━━━━━━━━━┳━━━━━━━┳━━━━━━━━┓
┃ Property ┃ Value ┃ Type   ┃
┡━━━━━━━━━━╇━━━━━━━╇━━━━━━━━┩
│ AGE      │ 42    │ NUMBER │
└──────────┴───────┴────────┘

# 6. Add a fact (head/tail/relation/source resolved by name or by ID)
$ kb --database main data fact add \
    --head Alice --relation WORKED_AT --tail IBM \
    --text "Alice joined IBM in June 1998." --source "Alice's résumé"
Added fact (id: 092733dd-…)

# 7. Set and list fact property values
$ kb --database main data fact property set "1998-06-01" \
    --fact-id 092733dd-… --property START_DATE --type DATE
Set fact property (literal id: 5e6f7a8b-…)

$ kb --database main data fact property list --fact-id 092733dd-…
┏━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┓
┃ Property   ┃ Value      ┃ Type   ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━┩
│ START_DATE │ 1998-06-01 │ DATE   │
└────────────┴────────────┴────────┘

# 8. List the full schema
$ kb --database main schema list
                      Classes
┏━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Name      ┃ Description             ┃ Properties ┃
┡━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ COMPANY   │ A business organization │            │
│ PERSON    │ A human individual      │ AGE        │
└───────────┴─────────────────────────┴────────────┘
                          Relations
┏━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Source ┃ Relation  ┃ Target  ┃ Description            ┃ Properties ┃
┡━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ PERSON │ WORKED_AT │ COMPANY │ Employment relationship │ START_DATE │
└────────┴───────────┴─────────┴────────────────────────┴────────────┘

# 9. List all instances
$ kb --database main data list
# optionally with provenance:
# $ kb --database main data list --show-provenance
      Entities
┏━━━━━━━┳━━━━━━━━━┓
┃ Name  ┃ Class   ┃
┡━━━━━━━╇━━━━━━━━━┩
│ IBM   │ COMPANY │
│ Alice │ PERSON  │
└───────┴─────────┘
     Property Values
┏━━━━━━━━┳━━━━━━━━━━┳━━━━━━━┓
┃ Entity ┃ Property ┃ Value ┃
┡━━━━━━━━╇━━━━━━━━━━╇━━━━━━━┩
│ Alice  │ AGE      │ 42    │
└────────┴──────────┴───────┘
        Facts
┏━━━━━━━┳━━━━━━━━━━━┳━━━━━━┓
┃ Head  ┃ Relation  ┃ Tail ┃
┡━━━━━━━╇━━━━━━━━━━━╇━━━━━━┩
│ Alice │ WORKED_AT │ IBM  │
└───────┴───────────┴──────┘

# 10. Search the schema
$ kb --database main schema search employment
┏━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Type       ┃ Name      ┃ Description             ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ RELATION   │ WORKED_AT │ Employment relationship  │
└────────────┴───────────┴─────────────────────────┘

# Search schema restricted to relations only
$ kb --database main schema search employment --type relation

# 10b. Search data (requires enable_search=True at KBSource init; returns empty otherwise)
$ kb --database main data search Alice
       Entities
┏━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ Name  ┃ Class    ┃ Description ┃
┡━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ Alice │ PERSON   │             │
└───────┴──────────┴─────────────┘

# Search facts only
$ kb --database main data search "IBM" --type fact

# 11. Answer a question
$ kb --database main data answer "Where did Alice work?"
┏━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Entity      ┃ Relation  ┃ Value   ┃ Provenance                     ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Alice       │           │         │                                │
│ Alice → IBM │ WORKED_AT │         │ Alice joined IBM in June 1998. │
└─────────────┴───────────┴─────────┴────────────────────────────────┘

# 12. Validate a statement
$ kb --database main data validate "Alice worked at IBM"

Supporting:
  • Alice —WORKED_AT→ IBM

Counter:
  (none)

# 13. LLM extraction (requires openai extra and OPENAI_API_KEY)
$ kb --database main data extract \
    "Alice later joined Google as a senior engineer in May 2002." \
    --query "Where did Alice work?" --query "When did she start?"
Extraction complete.

# 14. Delete a database
$ kb database delete main --yes
Deleted database: main
```

## Command reference

| Command | Description |
|---|---|
| `kb database list [--format table\|json]` | List registered databases with backend and search mode |
| `kb database register NAME [options]` | Register a new database with graph and search backends |
| `kb database create NAME` | Create a database (auto-registers with file defaults) |
| `kb database delete NAME [--yes]` | Delete a database and remove it from the registry |
| `kb database select NAME` | Set the default target database |
| `kb database config show` | Print graph backend config for the current database |
| `kb database config set [--graph-backend …] [--directory …] [--neo4j-uri …] …` | Update graph backend config |
| `kb database search config show` | Print search config for the current database |
| `kb database search config set [--search none\|bm25\|semantic] [--bm25-index-dir …] [--vector-backend …] …` | Update search config |
| `kb schema list [--format table\|json]` | List classes, relations, properties, and hierarchy |
| `kb schema class add NAME DESC` | Declare a CLASS (idempotent) |
| `kb schema class list [--format table\|json]` | List all declared classes |
| `kb schema class update --class NAME\|--class-id ID [--name N] [--description D]` | Update the name and/or description of a class |
| `kb schema class parent add [--class NAME\|--class-id ID] [--parent NAME\|--parent-id ID]` | Wire IS_SUBCLASS_OF from child to parent |
| `kb schema class parent remove [--class NAME\|--class-id ID] [--parent NAME\|--parent-id ID]` | Remove IS_SUBCLASS_OF edge |
| `kb schema class parent list [--class NAME\|--class-id ID] [--recursive] [--format table\|json\|tree]` | List parent classes (direct or all ancestors) |
| `kb schema class child list [--class NAME\|--class-id ID] [--recursive] [--format table\|json\|tree]` | List child classes (direct subclasses or all descendants) |
| `kb schema class property add NAME DESC [--class NAME\|--class-id ID]` | Declare a property on a class |
| `kb schema class property list [--class NAME\|--class-id ID] [--format table\|json]` | List class properties (optionally filtered by class) |
| `kb schema class property update --property NAME\|--property-id ID [--class NAME\|--class-id ID] [--name N] [--description D]` | Update the name and/or description of a class property; --class disambiguates when the name is shared |
| `kb schema class property delete --property NAME\|--property-id ID [--class NAME\|--class-id ID] [--yes]` | Delete a class property (errors if values exist); --class disambiguates |
| `kb schema class delete --class NAME\|--class-id ID [--yes]` | Delete a class (errors if instances or subclasses exist) |
| `kb schema relation add NAME DESC [--domain C] [--range C]` | Declare a RELATION (idempotent) |
| `kb schema relation list [--format table\|json]` | List all declared relations |
| `kb schema relation update --relation NAME\|--relation-id ID [--name N] [--description D]` | Update the name and/or description of a relation |
| `kb schema relation parent add [--relation NAME\|--relation-id ID] [--parent NAME\|--parent-id ID]` | Wire IS_SUBCLASS_OF from child to parent relation |
| `kb schema relation parent remove [--relation NAME\|--relation-id ID] [--parent NAME\|--parent-id ID]` | Remove IS_SUBCLASS_OF edge between relations |
| `kb schema relation parent list [--relation NAME\|--relation-id ID] [--recursive] [--format table\|json\|tree]` | List parent relations (direct or all ancestors) |
| `kb schema relation child list [--relation NAME\|--relation-id ID] [--recursive] [--format table\|json\|tree]` | List child relations (direct sub-relations or all descendants) |
| `kb schema relation property add NAME DESC [--relation NAME\|--relation-id ID]` | Declare a property on a relation |
| `kb schema relation property list [--relation NAME\|--relation-id ID] [--format table\|json]` | List relation properties (optionally filtered by relation) |
| `kb schema relation property update --property NAME\|--property-id ID [--relation NAME\|--relation-id ID] [--name N] [--description D]` | Update the name and/or description of a relation property; --relation disambiguates when the name is shared |
| `kb schema relation property delete --property NAME\|--property-id ID [--relation NAME\|--relation-id ID] [--yes]` | Delete a relation property (errors if values exist); --relation disambiguates |
| `kb schema relation delete --relation NAME\|--relation-id ID [--yes]` | Delete a relation (errors if facts or sub-relations exist) |
| `kb schema search QUERY [--type class\|relation\|property]… [--mode keyword\|semantic] [--top-k N] [--format table\|json]` | Search schema nodes; `--mode semantic` requires database configured with `--search semantic`; `--type` is repeatable |
| `kb data list [--show-provenance] [--format table\|json]` | List all entity, property-value, and fact instances |
| `kb data source add SOURCE [--name N] [--description D]` | Register a provenance source |
| `kb data source list [--format table\|json]` | List all registered provenance sources |
| `kb data source update --source NAME\|--source-id ID [--name N] [--description D]` | Update the name and/or description of a source |
| `kb data source delete --source NAME\|--source-id ID [--yes]` | Delete a source and its extraction records; instances are preserved |
| `kb data entity add [--name NAME] [--class NAME\|--class-id ID] [--description D] [--text T] [--source NAME\|--source-id ID]` | Add an entity instance |
| `kb data entity list [--show-provenance] [--format table\|json]` | List all entity instances |
| `kb data entity update --entity NAME\|--entity-id ID [--name N] [--description D]` | Update the name and/or description of an entity |
| `kb data entity delete --entity NAME\|--entity-id ID [--cascade] [--yes]` | Delete an entity and its property values; with --cascade also deletes referencing facts |
| `kb data entity property set VALUE [--name N] [--description D] --entity NAME\|--entity-id ID --property NAME [--type T] [--source NAME\|--source-id ID]` | Set a property value on an entity |
| `kb data entity property list --entity NAME\|--entity-id ID [--show-provenance] [--format table\|json]` | List property values for an entity |
| `kb data entity property update --entity NAME\|--entity-id ID --property NAME\|--property-id ID [--value V] [--name N] [--description D]` | Update the value, name, and/or description of an entity property value |
| `kb data entity property delete --entity NAME\|--entity-id ID --property NAME\|--property-id ID [--yes]` | Delete a property value from an entity |
| `kb data fact add [--name N] [--description D] [--head NAME\|--head-id ID] [--relation NAME\|--relation-id ID] [--tail NAME\|--tail-id ID] [--source NAME\|--source-id ID]` | Add a fact |
| `kb data fact list [--show-provenance] [--format table\|json]` | List all facts |
| `kb data fact update --fact NAME\|--fact-id ID [--name N] [--description D]` | Update the name and/or description of a fact |
| `kb data fact delete --fact NAME\|--fact-id ID [--yes]` | Delete a fact and its property values |
| `kb data fact property set VALUE [--name N] [--description D] --fact-id ID --property NAME [--type T] [--source NAME\|--source-id ID]` | Set a property value on a fact |
| `kb data fact property list --fact-id ID [--show-provenance] [--format table\|json]` | List property values for a fact |
| `kb data fact property update --fact NAME\|--fact-id ID --property NAME\|--property-id ID [--value V] [--name N] [--description D]` | Update the value, name, and/or description of a fact property value |
| `kb data fact property delete --fact NAME\|--fact-id ID --property NAME\|--property-id ID [--yes]` | Delete a property value from a fact |
| `kb data reindex` | Rebuild the BM25/vector search index for the current database |
| `kb data search QUERY [--type entity\|fact]… [--mode keyword\|semantic] [--top-k N] [--format table\|json]` | Search entities and facts; `--mode semantic` requires database configured with `--search semantic`; `--type` is repeatable |
| `kb data answer QUESTION [--format table\|json\|nl] [--model M]` | LLM answer query; `--format nl` returns a prose answer |
| `kb data validate STATEMENT [--natural-language] [--model M]` | LLM supporting / counter evidence; `--natural-language` returns a prose assessment |
| `kb data extract TEXT [--query Q]… [--source NAME\|--source-id ID] [--model M]` | LLM extraction |

Exit codes: `0` success · `1` KBError · `2` bad arguments.

## Project structure

```
platform/cli/
├── pyproject.toml
├── scripts/
│   ├── install.sh   # installs blue-data, blue-data-kb-source, then this package
│   ├── build.sh     # python -m build
│   └── deploy.sh    # twine upload dist/*
├── src/
│   └── blue_data_kb_cli/
│       ├── __init__.py
│       ├── config.py     # Registry, _make_kb factory, vector/embedding factories
│       └── commands.py   # Click command groups
└── tests/
    └── unit/
```
