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

Each database is automatically persisted as a JSON file inside the data directory — no explicit save step is needed:

```
~/.kbdb/{database}.json
```

`FileGraphSource` saves after every write operation, so IDs returned by one command can be safely passed to the next. Override the directory:

```bash
export KBDB_DATA_DIR=/path/to/my-kbdb
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
$ kb --database main schema declare class PERSON "A human individual"
Declared class: PERSON (id: 3b2a906c-…)

$ kb --database main schema declare property AGE "Age of a person" --class PERSON
Declared property: AGE (id: 0d9eaaac-bf07-41e6-bd6d-34f60f4c90d7)

$ kb --database main schema declare class COMPANY "A business organization"
Declared class: COMPANY (id: 49710e2e-…)

$ kb --database main schema declare relation WORKED_AT "Employment relationship" \
    --domain PERSON --range COMPANY
Declared relation: WORKED_AT (id: 67a75f53-…)

$ kb --database main schema declare property START_DATE "Start of employment" \
    --relation WORKED_AT
Declared property: START_DATE (id: 8c3d0a1e-…)

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
| `kb database list [--format table\|json]` | List all databases |
| `kb database create NAME` | Create a database |
| `kb database delete NAME [--yes]` | Delete a database (prompts unless --yes) |
| `kb database select NAME` | Set the default target database |
| `kb schema list [--format table\|json]` | List classes and relations for the current database |
| `kb schema declare class NAME DESC` | Declare a CLASS (idempotent) |
| `kb schema declare relation NAME DESC [--domain C] [--range C]` | Declare a RELATION |
| `kb schema declare property NAME DESC (--class NAME\|--class-id ID\|--relation NAME\|--relation-id ID)` | Declare a PROPERTY (owner required) |
| `kb schema search QUERY [--format table\|json]` | Keyword search over schema nodes |
| `kb data list [--show-provenance] [--format table\|json]` | List all entity, property-value, and fact instances |
| `kb data source add SOURCE [--name N] [--description D]` | Register a provenance source |
| `kb data source list [--format table\|json]` | List all registered provenance sources |
| `kb data entity add [--name NAME] [--class NAME\|--class-id ID] [--description D] [--text T] [--source NAME\|--source-id ID]` | Add an entity instance |
| `kb data entity list [--show-provenance] [--format table\|json]` | List all entity instances |
| `kb data entity property set VALUE [--name N] [--description D] --entity NAME\|--entity-id ID --property NAME [--type T] [--source NAME\|--source-id ID]` | Set a property value on an entity |
| `kb data entity property list --entity NAME\|--entity-id ID [--show-provenance] [--format table\|json]` | List property values for an entity |
| `kb data fact add [--name N] [--description D] [--head NAME\|--head-id ID] [--relation NAME\|--relation-id ID] [--tail NAME\|--tail-id ID] [--source NAME\|--source-id ID]` | Add a fact |
| `kb data fact list [--show-provenance] [--format table\|json]` | List all facts |
| `kb data fact property set VALUE [--name N] [--description D] --fact-id ID --property NAME [--type T] [--source NAME\|--source-id ID]` | Set a property value on a fact |
| `kb data fact property list --fact-id ID [--show-provenance] [--format table\|json]` | List property values for a fact |
| `kb data answer QUESTION [--format table\|json\|nl]` | LLM answer query; `--format nl` returns a prose answer |
| `kb data validate STATEMENT [--natural-language]` | LLM supporting / counter evidence; `--natural-language` returns a prose assessment |
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
│       └── commands.py   # Click command groups
└── tests/
    └── unit/
```
