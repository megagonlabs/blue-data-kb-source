# blue-data-kb-source

A typed knowledge-base graph layer with schema management, LLM-based knowledge extraction, and natural language querying.

Agents need knowledge they can trust and explain: a schema that says what kinds of things exist, facts that trace back to the document they came from, and answers that change when the underlying evidence does. blue-data-kb-source keeps that knowledge in a typed graph that grows from free text and structured data alike, and lets people and LLM agents query it in plain language, through a Python library, the `blue-kb` CLI, or an MCP server.

→ **[Full documentation](docs/README.md)** · **[Quick start](docs/quickstart.md)** · **[Installation](docs/installation.md)** · **[Examples](examples/README.md)** · **[CLI](docs/cli.md)** · **[MCP](docs/mcp.md)**

## What it does

- **Model** domain knowledge with a typed schema: classes, relations, properties, and subclass hierarchies, with integrity checks on every declaration, rename, and merge.
- **Populate** the graph with entities, facts, and property values, each linked to the source text and document it came from, with confidence and timestamps.
- **Extract** knowledge from free text with an LLM agent that grows the schema where the text needs it, then fills in instances that fit it.
- **Ingest** structured knowledge by name: `assert` operations resolve, deduplicate, and attach evidence instead of creating duplicates.
- **Query** the knowledge base in plain language: answer questions, validate statements, and enrich topics by walking the graph, with provenance on every result.
- **Connect** LLM agents through the MCP server, or work from the `blue-kb` CLI; storage is in memory, in local files, or in Neo4j.

See [docs/features.md](docs/features.md) for the full list with examples, and [examples/](examples/README.md) for runnable scripts.

## Repository layout

```
blue-data-kb-source/
├── lib/                        # Python library (blue-data-kb-source)
│   ├── src/blue_data_kb_source/
│   └── tests/
│       ├── unit/               # fast offline tests (optionally against Neo4j)
│       └── benchmarks/         # report-only LLM benchmarks
├── platform/
│   ├── cli/                    # Command-line interface (blue-kb)
│   │   ├── src/blue_data_kb_cli/
│   │   └── tests/
│   ├── mcp/                    # MCP server (blue-data-kb-mcp)
│   │   ├── src/blue_data_kb_mcp/
│   │   └── tests/
│   └── wordnet-mcp/            # WordNet MCP server (blue-data-wordnet-mcp)
│       ├── src/blue_data_wordnet_mcp/
│       └── tests/
├── scripts/                    # install.sh — installs every package, editable
├── docs/                       # User-facing documentation (start at docs/README.md)
├── examples/                   # Runnable examples (start at examples/README.md)
└── design/                     # Architecture and design-process documents
```

## Requirements

- Python 3.10+
- The sibling [`blue-data`](https://github.com/megagon-internal/blue-data) repository, cloned beside this one (`../blue-data`); its `lib/` provides the graph and vector storage layer
- An OpenAI API key for the LLM features (extraction, schema expansion, `answer` / `validate` / `enrich`)
- Optional: a Neo4j 5 server, for the Neo4j backend

```bash
export OPENAI_API_KEY=sk-...
```

## Quick start

```bash
# Install blue-data (from ../blue-data), the library with all extras, the CLI and both MCP servers
bash scripts/install.sh

# Create a database and a small schema
blue-kb database register main --search bm25 && blue-kb database select main
blue-kb schema class add PERSON "A human individual"
blue-kb schema class add COMPANY "A business organization"
blue-kb schema relation add WORKED_AT "Employment relationship" --domain PERSON --range COMPANY

# Add a source, then entities and a fact that carry their evidence
blue-kb data source add resume --name "Alice's résumé"
blue-kb data entity add --name Alice --class PERSON --text "Alice is a software engineer." --source resume
blue-kb data entity add --name IBM --class COMPANY
blue-kb data fact add --head Alice --relation WORKED_AT --tail IBM \
    --text "Alice joined IBM in June 1998." --source resume
blue-kb data list --show-provenance

# Extract from free text, then ask in plain language (needs OPENAI_API_KEY)
blue-kb data extract "Alice left IBM in 2005 to join Google as a staff engineer." \
    --query "Where has Alice worked?"
blue-kb data answer "Where has Alice worked, and since when?" --format nl
```

Databases live under `~/.blue-kb/` by default. The full walkthrough, with expected output at each step, the Python-library equivalent, and connecting an agent over MCP, is in **[docs/quickstart.md](docs/quickstart.md)**.

## Packages

| Package | Description |
|---|---|
| [lib/](lib/README.md) | `blue-data-kb-source`: the core Python library (`KBSource` with memory, file, and Neo4j backends) |
| [platform/cli/](platform/cli/README.md) | `blue-data-kb-cli`: the command-line tool, installs `blue-kb` |
| [platform/mcp/](platform/mcp/README.md) | `blue-data-kb-mcp`: MCP server for LLM agents, installs `blue-data-kb-mcp` |
| [platform/wordnet-mcp/](platform/wordnet-mcp/README.md) | `blue-data-wordnet-mcp`: standalone MCP server for WordNet lookups, installs `blue-data-wordnet-mcp`; optionally used by schema expansion to ground new names and hierarchy placement |
