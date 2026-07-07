# blue-data-kb-source

A typed knowledge-base graph layer with schema management, LLM-based knowledge extraction, and natural language querying.

## What it does

- **Model** domain knowledge with a typed schema: classes, relations, properties, and subclass hierarchies.
- **Populate** the graph with entity and fact instances linked to their provenance sources.
- **Extract** entities, facts, and property values from free text using an OpenAI LLM agent that extends the schema as needed.
- **Query** the knowledge base in natural language — answer questions, validate statements, and enrich queries by traversing the graph.
- **Observe** writes via database-scoped change hooks that fire synchronously with enriched event payloads.

## Repository layout

```
blue-data-kb-source/
├── lib/                        # Python library (blue-data-kb-source)
│   ├── src/blue_data_kb_source/
│   └── tests/
├── platform/
│   └── cli/                    # Command-line interface (kb)
│       ├── src/blue_data_kb_cli/
│       └── tests/
└── design/                     # Architecture and design documents
```

## Requirements

- Python 3.10+
- [`blue-data`](../blue-data/lib) (local dependency)
- OpenAI API key — only needed for LLM extraction (`import_text` / `kb extract`)

```bash
export OPENAI_API_KEY=sk-...
```

## Quick start

```bash
# Install the library
cd lib && pip install -e ../../blue-data/lib && pip install -e . && cd ..

# Install the CLI
cd platform/cli && pip install -e . && cd ../..

# Create a database
kb database create main

# Declare a schema
kb --database main schema declare class PERSON "A human individual"
kb --database main schema declare class COMPANY "A business organization"
kb --database main schema declare relation WORKED_AT "Employment relationship" \
    --domain PERSON --range COMPANY

# Add entities and a fact
ALICE=$(kb --database main entity add Alice --class <person-id> | grep -oP '\(id: \K[^)]+')
IBM=$(kb --database main entity add IBM --class <company-id> | grep -oP '\(id: \K[^)]+')
kb --database main fact add --head "$ALICE" --relation <worked-at-id> --tail "$IBM" \
    --text "Alice joined IBM in June 1998."

# Query
kb --database main answer "Where did Alice work?"
kb --database main validate "Alice worked at IBM"

# LLM extraction (requires OPENAI_API_KEY)
kb --database main extract \
    "Alice joined IBM as a software engineer in June 1998. She later moved to Google in 2005." \
    --query "Where did Alice work?" --query "When did she start?"
```

## Packages

| Package | Description |
|---|---|
| [lib/](lib/README.md) | `blue-data-kb-source` — core Python library |
| [platform/cli/](platform/cli/README.md) | `blue-data-kb-cli` — `kb` CLI |
