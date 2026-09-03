from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, Callable, Literal

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import CLASS, PROPERTY, RELATION
from blue_data_kb_source.types import SchemaError, SchemaSearchResult

if TYPE_CHECKING:
    from blue_data_kb_source.schema.manager import SchemaManager
    from blue_data_kb_source.search.indexer import KBSearchIndexer

_SCHEMA_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": "Return the current KB schema (classes, relations, properties).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_schema",
            "description": (
                "Search existing schema elements (classes, relations, properties) for one that "
                "already covers a given concept — even under a different name. Call this before "
                "declare_class, declare_relation_type, declare_class_property, or "
                "declare_relation_property to avoid declaring a near-duplicate (e.g. a JOIN_DATE "
                "property when a START_DATE property already means the same thing). Returns "
                "{schema_elements: [{id, type, name, description}]}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Short description of the concept to look for"},
                    "mode":  {"type": "string", "enum": ["keyword", "semantic"], "description": "Search mode (default: keyword)"},
                    "type":  {"type": "string", "enum": ["class", "relation", "property"], "description": "Restrict to a schema element type (optional)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "declare_class",
            "description": "Declare a CLASS in the schema (idempotent). Returns {id}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Class name e.g. PERSON"},
                    "description": {"type": "string"},
                },
                "required": ["name", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "declare_class_property",
            "description": "Declare a PROPERTY on a CLASS (idempotent). Returns {id}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "class_id": {"type": "string"},
                    "name": {"type": "string", "description": "Property name e.g. AGE"},
                    "description": {"type": "string"},
                },
                "required": ["class_id", "name", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "declare_relation_type",
            "description": (
                "Declare a RELATION type in the schema (idempotent). Every relation must have a "
                "domain and range CLASS — find or declare both classes first. Returns {id}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Relation name e.g. WORKED_AT"},
                    "description": {"type": "string"},
                    "is_symmetric": {"type": "boolean"},
                    "domain": {"type": "string", "description": "Name of the domain (head) CLASS, e.g. PERSON"},
                    "range": {"type": "string", "description": "Name of the range (tail) CLASS, e.g. COMPANY"},
                },
                "required": ["name", "description", "domain", "range"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "declare_relation_property",
            "description": "Declare a PROPERTY on a RELATION (idempotent). Returns {id}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "relation_id": {"type": "string"},
                    "name": {"type": "string", "description": "Property name e.g. START_DATE"},
                    "description": {"type": "string"},
                },
                "required": ["relation_id", "name", "description"],
            },
        },
    },
]

_SYSTEM_PROMPT_TEMPLATE = """\
You are a knowledge base (KB) schema expansion agent.

The KB stores:
  CLASS     — category of entities (e.g. PERSON, COMPANY)
  RELATION  — type of relationship (e.g. WORKED_AT, LIVES_IN)
  PROPERTY  — attribute of a class or relation (e.g. AGE, START_DATE)

Your job is only to grow the schema — you never add entities, facts, or property
values. That happens in a separate extraction pass, run afterward, against the
schema you produce here.

Workflow:
1. Read the text and identify the categories, relationship types, and attributes
   it evidences.
2. Before calling declare_class, declare_relation_type, declare_class_property, or
   declare_relation_property, call search_schema with a short description of the
   concept. If an existing element already covers the same meaning — even under a
   different name — reuse it; do not declare a near-duplicate (e.g. a JOIN_DATE
   property when a START_DATE property already means the same thing).
3. Beyond what's directly evidenced, you may also propose up to {depth} level(s) of
   generalization/specialization around each concept — e.g. depth=1 also considers
   each concept's immediate parent or child category (a mentioned "poodle" also
   suggests DOG as a parent CLASS); depth=0 sticks to only what's directly
   evidenced.{mcp_note}
4. declare_relation_type requires the domain (head CLASS name) and range (tail
   CLASS name) — find or declare both classes first.
5. Use UPPER_SNAKE_CASE for CLASS, RELATION, and PROPERTY names.

Current schema:
{schema}

Work through the text systematically and stop calling tools when you are done.\
"""

_MCP_NOTE = (
    " Tools prefixed 'mcp__' connect to an external lexical/ontology resource — use "
    "them to ground new names and hierarchy placement (e.g. a hypernym lookup for the "
    "right parent CLASS, a synonym lookup to catch a near-duplicate search_schema might "
    "miss) before declaring."
)


async def _connect_mcp_servers(
    mcp_servers: list[dict[str, Any]], stack: AsyncExitStack
) -> tuple[list[dict[str, Any]], dict[str, tuple[Any, str]]]:
    """Connect to each configured MCP server (stdio/sse/streamable_http), returning
    OpenAI-shaped tool specs — name-prefixed per server as 'mcp__<server>__<tool>' to
    avoid collisions across servers — and a name -> (session, original_name) map for
    dispatch. All connections live for the duration of *stack*."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    tool_specs: list[dict[str, Any]] = []
    dispatch: dict[str, tuple[Any, str]] = {}

    for i, cfg in enumerate(mcp_servers):
        server_name = cfg.get("name") or f"server{i}"
        transport = cfg.get("transport", "stdio")
        if transport == "stdio":
            params = StdioServerParameters(
                command=cfg["command"], args=cfg.get("args", []), env=cfg.get("env") or None
            )
            streams = await stack.enter_async_context(stdio_client(params))
        elif transport == "sse":
            streams = await stack.enter_async_context(
                sse_client(cfg["url"], headers=cfg.get("headers"))
            )
        elif transport == "streamable_http":
            streams = await stack.enter_async_context(
                streamablehttp_client(cfg["url"], headers=cfg.get("headers"))
            )
        else:
            raise SchemaError(f"unknown MCP transport '{transport}' for server '{server_name}'")

        read, write = streams[0], streams[1]
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        listed = await session.list_tools()
        for tool in listed.tools:
            prefixed = f"mcp__{server_name}__{tool.name}"
            tool_specs.append({
                "type": "function",
                "function": {
                    "name": prefixed,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema,
                },
            })
            dispatch[prefixed] = (session, tool.name)

    return tool_specs, dispatch


async def _call_mcp_tool(dispatch: dict[str, tuple[Any, str]], name: str, arguments: dict[str, Any]) -> str:
    session, original_name = dispatch[name]
    result = await session.call_tool(original_name, arguments)
    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            return text
    return json.dumps({"content": [str(c) for c in result.content]})


class SchemaExpansionEngine:
    """LLM-powered schema expansion — proposes and declares CLASS/RELATION/PROPERTY
    elements grounded by *text*, optionally using tools from connected MCP servers
    (e.g. WordNet) alongside the built-in schema tools. Never touches entities, facts,
    or property values — see extraction.engine.ExtractionEngine for that."""

    def __init__(
        self,
        source: GraphSource,
        schema_manager: "SchemaManager",
        search_indexer: "KBSearchIndexer | None" = None,
    ) -> None:
        self._source = source
        self._schema = schema_manager
        self._search_indexer = search_indexer

    async def expand_schema(
        self,
        database: str,
        text: str,
        *,
        depth: int = 1,
        reference: Literal["llm", "mcp"] = "llm",
        mcp_servers: list[dict[str, Any]] | None = None,
        dedup_mode: Literal["search", "llm", "none"] = "search",
        model: str = "gpt-4o",
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None = None,
        max_turns: int = 25,
    ) -> None:
        try:
            import openai
        except ImportError as exc:
            raise SchemaError(
                "expand_schema requires the 'openai' package: pip install openai"
            ) from exc

        if reference == "mcp" and not mcp_servers:
            raise SchemaError("reference='mcp' requires at least one entry in mcp_servers")

        schema_str = (await self._schema.fetch_database_schema(database)).to_str(simplified=True)
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            depth=depth, schema=schema_str, mcp_note=_MCP_NOTE if reference == "mcp" else ""
        )

        base_tools = (
            _SCHEMA_TOOLS if dedup_mode != "none"
            else [t for t in _SCHEMA_TOOLS if t["function"]["name"] != "search_schema"]
        )

        client = openai.AsyncOpenAI()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]

        async with AsyncExitStack() as stack:
            tools = list(base_tools)
            mcp_dispatch: dict[str, tuple[Any, str]] = {}
            if reference == "mcp":
                mcp_tools, mcp_dispatch = await _connect_mcp_servers(mcp_servers or [], stack)
                tools = tools + mcp_tools

            turns = 0
            while True:
                if turns >= max_turns:
                    break
                turns += 1
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    tools=tools,  # type: ignore[arg-type]
                    tool_choice="auto",
                )
                msg = response.choices[0].message
                msg_dict: dict[str, Any] = {"role": msg.role, "content": msg.content}
                if msg.tool_calls:
                    msg_dict["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in msg.tool_calls
                    ]
                messages.append(msg_dict)

                if not msg.tool_calls:
                    break

                for tc in msg.tool_calls:
                    args = json.loads(tc.function.arguments)
                    if on_tool_call:
                        on_tool_call(tc.function.name, args)
                    try:
                        if tc.function.name in mcp_dispatch:
                            content_str = await _call_mcp_tool(mcp_dispatch, tc.function.name, args)
                            callback_result: Any = content_str
                        else:
                            callback_result = await self._dispatch_tool(
                                database, tc.function.name, args, dedup_mode, model
                            )
                            content_str = json.dumps(callback_result)
                    except Exception as exc:
                        callback_result = {"error": str(exc)}
                        content_str = json.dumps(callback_result)
                    if on_tool_result:
                        on_tool_result(tc.function.name, args, callback_result)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": content_str,
                    })

    async def _dispatch_tool(
        self,
        database: str,
        fn: str,
        args: dict[str, Any],
        dedup_mode: Literal["search", "llm", "none"],
        model: str,
    ) -> dict[str, Any]:
        if fn == "get_schema":
            schema = await self._schema.fetch_database_schema(database)
            return {"schema": schema.to_str(simplified=True)}

        if fn == "search_schema":
            type_filter = args.get("type")
            if type_filter:
                type_filter = type_filter.upper()
            if dedup_mode == "llm":
                results = await self._judge_similar_schema(database, args["query"], type_filter, model)
            else:
                mode = args.get("mode", "keyword")
                if self._search_indexer is not None:
                    results = self._search_indexer.search_schema(
                        database, args["query"], k=20, mode=mode, type_filter=type_filter
                    )
                else:
                    results = await self._schema.find_schema(database, args["query"], type_filter=type_filter)
            return {"schema_elements": [
                {"id": r.id, "type": r.type, "name": r.name, "description": r.description}
                for r in results
            ]}

        if fn == "declare_class":
            cid = await self._schema.declare_class(database, name=args["name"], description=args["description"])
            return {"id": cid}

        if fn == "declare_class_property":
            pid = await self._schema.declare_class_property(
                database, name=args["name"], description=args["description"], class_id=args["class_id"]
            )
            return {"id": pid}

        if fn == "declare_relation_type":
            rid = await self._schema.declare_relation_type(
                database,
                name=args["name"],
                description=args["description"],
                is_symmetric=args.get("is_symmetric", False),
                domain=args["domain"],
                range=args["range"],
            )
            return {"id": rid}

        if fn == "declare_relation_property":
            pid = await self._schema.declare_relation_property(
                database,
                name=args["name"],
                description=args["description"],
                relation_id=args["relation_id"],
            )
            return {"id": pid}

        return {"error": f"unknown tool: {fn}"}

    async def _judge_similar_schema(
        self,
        database: str,
        query: str,
        type_filter: str | None,
        model: str,
    ) -> list[SchemaSearchResult]:
        """Ask the LLM whether *query* means the same thing as an existing schema element.

        Used in place of keyword/semantic search_schema when accuracy on
        differently-worded synonyms (e.g. JOIN_DATE vs START_DATE) matters more
        than the cost of an extra model call.
        """
        import openai

        labels = [type_filter] if type_filter else [CLASS, RELATION, PROPERTY]
        candidates: list[SchemaSearchResult] = []
        for label in labels:
            for node in self._source.match_nodes(database, label=label):
                props = node.get("properties", {})
                candidates.append(SchemaSearchResult(
                    id=node["id"], type=label,
                    name=props.get("name", ""), description=props.get("description") or "",
                ))
        if not candidates:
            return []

        candidates_str = "\n".join(f"- {c.id}: [{c.type}] {c.name} — {c.description}" for c in candidates)
        judge_tool = {
            "type": "function",
            "function": {
                "name": "return_matches",
                "description": "Return the ids of existing schema elements that mean the same thing as the proposed one.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "matching_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "ids of existing elements representing the same concept as the query; empty if none match",
                        },
                    },
                    "required": ["matching_ids"],
                },
            },
        }
        client = openai.AsyncOpenAI()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You judge whether a proposed KB schema element duplicates an existing one "
                        "by meaning, not by exact wording (e.g. JOIN_DATE and START_DATE can mean the "
                        "same thing: the date employment began). Be conservative: only flag a match "
                        "when they clearly represent the same concept."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Proposed element: \"{query}\"\n\nExisting elements:\n{candidates_str}",
                },
            ],
            tools=[judge_tool],  # type: ignore[arg-type]
            tool_choice={"type": "function", "function": {"name": "return_matches"}},  # type: ignore[arg-type]
        )
        tc = response.choices[0].message.tool_calls[0]
        matching_ids = set(json.loads(tc.function.arguments).get("matching_ids", []))
        return [c for c in candidates if c.id in matching_ids]
