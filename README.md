# blue-data-kb-source

A typed knowledge-base graph layer with schema management, LLM-based knowledge extraction, and natural language querying.

→ **[Full documentation](docs/README.md)** · **[Quick start](docs/quickstart.md)** · **[Installation](docs/installation.md)** · **[Examples](examples/README.md)**

## What it does

- **Model** domain knowledge with a typed schema: classes, relations, properties, and subclass hierarchies.
- **Populate** the graph with entity and fact instances linked to their provenance sources, with automatic `created`/`modified` timestamps on every node.
- **Extract** entities, facts, and property values from free text using an OpenAI LLM agent that extends the schema as needed.
- **Query** the knowledge base in natural language — answer questions, validate statements, and enrich queries by traversing the graph.
- **Observe** writes via database-scoped change hooks that fire synchronously with enriched event payloads.
- **Connect an LLM agent** directly via the MCP server, or drive everything from the `kb` CLI.

See [docs/features.md](docs/features.md) for the full list with examples.

## Repository layout

```
blue-data-kb-source/
├── lib/                        # Python library (blue-data-kb-source)
│   ├── src/blue_data_kb_source/
│   └── tests/
├── platform/
│   ├── cli/                    # Command-line interface (kb)
│   │   ├── src/blue_data_kb_cli/
│   │   └── tests/
│   ├── mcp/                    # MCP server (blue-data-kb-mcp)
│   │   ├── src/blue_data_kb_mcp/
│   │   └── tests/
│   ├── wordnet-mcp/            # WordNet MCP server (blue-data-wordnet-mcp)
│   │   ├── src/blue_data_wordnet_mcp/
│   │   └── tests/
│   └── api/                    # REST API — planned, not yet implemented
├── docs/                       # User-facing documentation (start at docs/README.md)
├── examples/                   # Runnable examples (start at examples/README.md)
└── design/                     # Architecture and design-process documents
```

## Requirements

- Python 3.10+
- [`blue-data`](../blue-data/lib) (local dependency)
- OpenAI API key — needed for LLM extraction (`import_text` / `kb extract`) and LLM querying (`answer` / `validate` / `enrich`)

```bash
export OPENAI_API_KEY=sk-...
```

## Quick start

```bash
# Install the library (with OpenAI support for LLM features) and the CLI
cd lib && bash scripts/install.sh && cd ..
cd platform/cli && bash scripts/install.sh && cd ../..

# Databases persist under ~/.kbdb/ by default; override with:
export KBDB_DATA_DIR=/path/to/my-kbdb

# Create a database and declare a schema
kb database create main
kb --database main schema class add PERSON "A human individual"
kb --database main schema class add COMPANY "A business organization"
kb --database main schema relation add WORKED_AT "Employment relationship" \
    --domain PERSON --range COMPANY
kb --database main schema class property add AGE "Age of a person" --class PERSON

# Add entities and a fact by name
kb --database main data entity add --name Alice --class PERSON \
    --text "Alice is a software engineer."
kb --database main data entity add --name IBM --class COMPANY
kb --database main data fact add \
    --head Alice --relation WORKED_AT --tail IBM \
    --text "Alice joined IBM in June 1998."
kb --database main data entity property set 30 --entity Alice --property AGE --type NUMBER

# Show schema and instances
kb --database main schema list
kb --database main data list

# LLM query and extraction (both require OPENAI_API_KEY)
kb --database main data answer "Where did Alice work?"
kb --database main data validate "Alice worked at IBM"
kb --database main data extract \
    "Alice joined IBM as a software engineer in June 1998. She later moved to Google in 2005." \
    --query "Where did Alice work?" --query "When did she start?"
```

The full walkthrough — with expected output at each step, the Python-library equivalent, and how to connect an LLM agent via MCP — is in **[docs/quickstart.md](docs/quickstart.md)**.

For runnable scripts with real captured output — one per storage/search backend, one per library feature, LangGraph integrations, full end-to-end pipelines, and a scored before/after scenario — see **[examples/](examples/README.md)**.

## Packages

| Package | Description |
|---|---|
| [lib/](lib/README.md) | `blue-data-kb-source` — core Python library |
| [platform/cli/](platform/cli/README.md) | `blue-data-kb-cli` — `kb` CLI |
| [platform/mcp/](platform/mcp/README.md) | `blue-data-kb-mcp` — MCP server for LLM agent tool use |
| [platform/wordnet-mcp/](platform/wordnet-mcp/README.md) | `blue-data-wordnet-mcp` — standalone MCP server exposing WordNet lexical lookups; optionally connected by `expand_schema(reference="mcp")` to ground new schema names and hierarchy placement |
