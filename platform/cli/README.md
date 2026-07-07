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

For LLM extraction support, also install the `openai` extra on the library:

```bash
pip install -e "../../lib[openai]"
```

## State persistence

Graph state is serialized to `~/.kbdb/state.json` between invocations so that IDs returned by one command can be passed to the next. Override the path:

```bash
export KBDB_STATE_FILE=/path/to/my-kb.json
```

## Global option

```
kb [--database DB] COMMAND
```

`--database` selects the target database and defaults to `"default"`. Set it permanently via the `KBDB_DATABASE` environment variable.

## Walkthrough

```bash
# 1. Create a database
$ kb database create main
Created database: main

# 2. Declare schema
$ kb --database main schema declare class PERSON "A human individual"
Declared class: PERSON (id: 3b2a906c-…)

$ kb --database main schema declare class COMPANY "A business organization"
Declared class: COMPANY (id: 49710e2e-…)

$ kb --database main schema declare relation WORKED_AT "Employment relationship" \
    --domain PERSON --range COMPANY
Declared relation: WORKED_AT (id: 67a75f53-…)

# 3. Register a provenance source
$ kb --database main source add resume_alice --name "Alice's résumé"
Added source: resume_alice (id: a1b2c3d4-…)

# 4. Add entity instances
$ kb --database main entity add Alice --class 3b2a906c-… \
    --text "Alice is a software engineer." --source a1b2c3d4-…
Added entity: Alice (id: d52378c9-…)

$ kb --database main entity add IBM --class 49710e2e-…
Added entity: IBM (id: 5b1a47fb-…)

# 5. Add a fact
$ kb --database main fact add \
    --head d52378c9-… --relation 67a75f53-… --tail 5b1a47fb-… \
    --text "Alice joined IBM in June 1998." --source a1b2c3d4-…
Added fact (id: 092733dd-…)

# 6. Search the schema
$ kb --database main schema search employment
┏━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Type       ┃ Name      ┃ Description             ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ RELATION   │ WORKED_AT │ Employment relationship  │
└────────────┴───────────┴─────────────────────────┘

# 7. Answer a question
$ kb --database main answer "Where did Alice work?"
┏━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Entity      ┃ Relation  ┃ Value ┃ Provenance                     ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Alice       │           │       │                                │
│ Alice → IBM │ WORKED_AT │       │ Alice joined IBM in June 1998. │
└─────────────┴───────────┴───────┴────────────────────────────────┘

# 8. Validate a statement
$ kb --database main validate "Alice worked at IBM"

Supporting:
  • Alice —WORKED_AT→ IBM

Counter:
  (none)

# 9. LLM extraction (requires openai extra and OPENAI_API_KEY)
$ kb --database main extract \
    "Alice joined IBM as a software engineer in June 1998." \
    --query "Where did Alice work?" --query "When did she start?"
Extraction complete.
```

## Command reference

| Command | Description |
|---|---|
| `kb database create NAME` | Create a database |
| `kb schema declare class NAME DESC` | Declare a CLASS (idempotent) |
| `kb schema declare relation NAME DESC [--domain C] [--range C]` | Declare a RELATION |
| `kb schema declare property NAME DESC [--class ID\|--relation ID]` | Declare a PROPERTY |
| `kb schema search QUERY [--format table\|json]` | Keyword search over schema nodes |
| `kb source add SOURCE [--name N] [--description D]` | Register a provenance source |
| `kb entity add NAME [--class ID] [--text T] [--source ID]` | Add an entity instance |
| `kb fact add --head ID --relation ID --tail ID [--text T] [--source ID]` | Add a fact |
| `kb answer QUESTION [--format table\|json]` | Keyword answer query |
| `kb validate STATEMENT` | Supporting / counter evidence |
| `kb extract TEXT [--query Q]… [--source ID] [--model M]` | LLM extraction |

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
