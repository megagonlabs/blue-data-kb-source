# blue-data-kb-source REST API

**Not yet implemented.** `platform/api/` does not exist in the repository yet, and `fastapi`/`uvicorn` are not runtime dependencies of any package today — see the status table in [architecture.md](architecture.md#platform-layer) and [`design/07-repo-organization.md`](../design/07-repo-organization.md), which lists `api/` as "Planned".

Until it's built, a full request/response sketch of what a REST adapter over `KBSource` would look like — endpoints, request/response bodies, and the error envelope — lives in [`design/09-api-interface-spec.md`](../design/09-api-interface-spec.md#2-rest-api-planned-not-yet-implemented). Treat everything there as speculative design, not a working API.

In the meantime, the same operations are available via:

- the [Python library](api-reference.md) directly,
- the [`blue-kb` CLI](cli.md), or
- the [MCP server](mcp.md), for LLM agent access.

This page will be filled in with the real endpoint reference once `platform/api/` is implemented, following the same pattern as [cli.md](cli.md) and [mcp.md](mcp.md).
