# Documentation

User-facing documentation for `blue-data-kb-source` — what to read depends on what you're doing.

> This is distinct from [`../design/`](../design/), which holds the architecture and design-process
> documents written for whoever (human or agent) is implementing or extending this project —
> decision rationale, detailed component specs, the full interface sketch. The pages here assume
> you just want to *use* the library, CLI, or MCP server; they draw on `design/` for accuracy but
> don't require reading it.

## Start here

| Doc | Read this if you want to... |
|---|---|
| [quickstart.md](quickstart.md) | Get a knowledge base running in the next five minutes |
| [installation.md](installation.md) | Install any combination of the library, CLI, and MCP server, with all optional extras |
| [features.md](features.md) | See what the library can do, at a glance, with a short example per feature |

## Using it

| Doc | Read this if you want to... |
|---|---|
| [guide.md](guide.md) | Work through the Python library task by task — schema, provenance, search, LLM extraction and querying, change hooks, export/import |
| [api-reference.md](api-reference.md) | Look up an exact method signature, parameter, or result type |
| [cli.md](cli.md) | Use the `kb` command-line tool — every command, with a full walkthrough |
| [mcp.md](mcp.md) | Connect Claude or another MCP client to a knowledge base — every tool, with Claude Desktop config |
| [api.md](api.md) | REST API — not built yet; this is a placeholder pointing at the design sketch |

## Building on it

| Doc | Read this if you want to... |
|---|---|
| [architecture.md](architecture.md) | Understand how the pieces fit together — the component diagram, the node/edge model, and the data flow through `answer`/`validate`/`enrich` and `import_text` |
| [extending.md](extending.md) | Implement a new graph storage backend, a new vector search backend, or swap the LLM provider |

## Something's out of date

These pages are meant to track the actual current behavior of the code, not the plan for it. If you find one that's drifted, that's a bug in the docs — the underlying source of truth is always the code itself, [`lib/README.md`](../lib/README.md), [`platform/cli/README.md`](../platform/cli/README.md), and [`platform/mcp/README.md`](../platform/mcp/README.md) (the CLI and MCP pages here are copies of those, kept in sync with them).
