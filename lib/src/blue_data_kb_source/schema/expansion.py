from __future__ import annotations

import asyncio
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
            "description": (
                "Declare a CLASS — a category/kind that can have many members (e.g. PERSON, "
                "ORGANIZATION) — in the schema (idempotent). NEVER call this for a specific named "
                "individual mentioned in the text: a person named 'Alice' is not a CLASS 'ALICE', "
                "an organization named 'United Nations' is not a CLASS 'UNITED_NATIONS' — those "
                "are entities, created later in a separate extraction pass. If a mentioned kind "
                "doesn't fit any existing CLASS, declare the general kind it belongs to (e.g. "
                "ORGANIZATION), never a class named after the specific instance itself. After "
                "declaring, also check whether the text evidences a RELATION connecting this "
                "CLASS to another one (existing or new) — see declare_relation_type; a class "
                "left with no relation to anything is often a sign one was missed. Returns {id}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Class name e.g. PERSON — a category, never a specific named individual"},
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
                "Declare a RELATION type in the schema (idempotent) — a general kind of "
                "relationship (e.g. WORKED_AT), never a specific fact between two named "
                "individuals (e.g. no ALICE_WORKED_AT_UNITED_NATIONS relation — that's a FACT, "
                "created later in extraction, between entities that instantiate the CLASSes "
                "below). Every relation must have a domain and range CLASS — find or declare "
                "both classes first. Returns {id}."
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
    {
        "type": "function",
        "function": {
            "name": "add_subclass_of",
            "description": (
                "Link an existing CLASS or RELATION as a subtype of a more general existing one "
                "of the same kind — e.g. EMPLOYEE (child) add_subclass_of PERSON (parent). Use "
                "this for any is-a/type-of/kind-of relationship between two concepts; never "
                "declare_relation_type for that purpose. Both ids must come from a prior "
                "declare_class/declare_relation_type/search_schema/get_schema result. Returns {}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "child_id": {"type": "string", "description": "id of the more specific CLASS or RELATION"},
                    "parent_id": {"type": "string", "description": "id of the more general CLASS or RELATION"},
                },
                "required": ["child_id", "parent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attach_property",
            "description": (
                "Attach an existing PROPERTY (e.g. one found via search_schema) to a CLASS or "
                "RELATION (idempotent), reusing it instead of declaring a same-meaning "
                "near-duplicate under a new name. Prefer this over declare_class_property / "
                "declare_relation_property whenever search_schema already turned up a property "
                "that means the same thing. Returns {}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_id": {"type": "string", "description": "id of the CLASS or RELATION to attach the property to"},
                    "property_id": {"type": "string", "description": "id of the existing PROPERTY to reuse"},
                },
                "required": ["target_id", "property_id"],
            },
        },
    },
]

_TAXONOMIC_RELATION_NAMES = {
    "IS_A", "TYPE_OF", "KIND_OF", "SUBCLASS_OF", "SUBTYPE_OF", "SUBTYPE",
    "INSTANCE_OF", "IS_INSTANCE_OF", "IS_SUBCLASS_OF", "IS_KIND_OF", "IS_TYPE_OF",
}

# Minimum cosine similarity at which the automatic pre-declare check treats an existing
# element as the same concept. Keyword (BM25) hits never auto-merge: sharing a word
# like "person" or "relationship" says nothing about synonymy, and merging on it
# silently swallowed STUDIED_AT/MANAGED_BY/HAS_ROLE into WORKED_AT (issue #101).
_SEMANTIC_DEDUP_MIN_SCORE = 0.9

_SYSTEM_PROMPT_TEMPLATE = """\
You are a knowledge base (KB) schema expansion agent.

The KB stores:
  CLASS     — category of entities (e.g. PERSON, COMPANY)
  RELATION  — type of relationship between two entities (e.g. WORKED_AT, LIVES_IN)
  PROPERTY  — attribute of a class or relation (e.g. AGE, START_DATE)

Your job is only to grow the schema — the CLASS/RELATION/PROPERTY *types* — never
the specific entities, facts, or property values that instantiate them. Those are
created later in a separate extraction pass, against the schema you produce here.

CLASS vs. entity — the single most important distinction in this job: a CLASS is
a *kind* that can have many members (PERSON, ORGANIZATION, COMPANY). A specific
named person, place, organization, or thing mentioned in the text is an *entity*
— exactly one real-world referent — never a CLASS of its own. If the text says
"Alice worked at the United Nations":
  WRONG:  declare_class("ALICE", ...)             — Alice is a PERSON, not a class
          declare_class("UNITED_NATIONS", ...)    — an ORGANIZATION, not a class
  RIGHT:  confirm/declare_class("PERSON", ...) and declare_class("ORGANIZATION",
          ...) — the general kinds "Alice" and "United Nations" belong to — then
          declare_relation_type("WORKED_AT", domain="PERSON", range="ORGANIZATION")
          if no equivalent relation already exists.
Before calling declare_class on any name, ask: "could many different real-world
things share this same name as a kind?" (PERSON — yes; ALICE — no, it names one
specific person). If the answer is no, the word names an entity: find or declare
the general kind it belongs to instead, and never create a class named after the
instance itself. The same applies to declare_relation_type — declare a general
kind of relationship (WORKED_AT), never a relation named after a specific fact
between two named individuals (e.g. no ALICE_WORKED_AT_UNITED_NATIONS).

Hierarchy between two CLASSes or two RELATIONs (is-a / type-of / kind-of /
subclass-of) is a *structural* link, wired with add_subclass_of — it is never a
declared RELATION. Link X under Y only when every X is necessarily a Y (an
ENGINEER is a PERSON). Things that are merely related are not subtypes: a TEAM
is part of a COMPANY, a DEGREE is held by a PERSON, a UNIVERSITY is not a
COMPANY — those are RELATIONs or nothing. Each add_subclass_of is checked for
this, and a link that fails comes back as an error with the reason. Never call declare_relation_type with a name like IS_A, TYPE_OF,
KIND_OF, or SUBCLASS_OF; use add_subclass_of on the two existing CLASS or RELATION
ids instead. declare_relation_type is only for semantic relationships that hold
between instances — WORKED_AT, LIVES_IN, and even meronymy like PART_OF (e.g. an
ENGINE is PART_OF a CAR, a fact-level relationship, not a taxonomy edge) all
belong there.

A CLASS sitting unconnected to the rest of the schema is a red flag: classes exist
to be related to each other. Every CLASS you declare should end up reachable from
the rest of the schema via at least one RELATION (as domain or range) and/or one
add_subclass_of link — if you can't connect it either way, ask whether it was the
right class to declare at all.

Workflow:
1. Read the text and identify the categories, relationship types, and attributes
   it evidences — separating general kinds (candidates for CLASS/RELATION) from
   specific named individuals (entities; see CLASS vs. entity above — these never
   get declared here, only the kinds they belong to). Identify relationship types
   explicitly, not just classes: for each new kind you plan to declare, ask "what
   connects this to PERSON or another class already in the schema?" — e.g. text
   evidencing that a PERSON has a SKILL or a HOBBY evidences HAS_SKILL and
   HAS_HOBBY relations between them just as much as it evidences the SKILL/HOBBY
   classes themselves; don't stop at declaring the class.
2. Before calling declare_class, declare_relation_type, declare_class_property, or
   declare_relation_property, call search_schema with a short description of the
   concept. If an existing element already covers the same meaning — even under a
   different name — reuse it; do not declare a near-duplicate (e.g. a JOIN_DATE
   property when a START_DATE property already means the same thing, or an
   EMPLOYED_AT relation when WORKED_AT already means the same thing). Declaring a
   class/relation also runs an automatic duplicate check as a safety net, but don't
   rely on that — searching first is cheaper and is also how you find a parent to
   attach to in step 4. If that check redirects a declaration to an existing
   element, the result carries "reused_existing" naming it: if that element really
   is a different concept from the one you meant, declare yours again under a more
   specific name rather than treating it as done.
3. Declare a PROPERTY on the CLASS or RELATION it actually describes — a degree
   belongs to an education relationship or a person, never to an employment
   relation; if the right owner doesn't exist yet, declare it first. Each
   property declaration is checked for this and for a same-meaning property the
   owner already has (the result then carries "reused_existing").
   When search_schema turns up an existing PROPERTY that covers the concept you
   need on a *different* class/relation than the one that already defines it, call
   attach_property to reuse it there instead of declare_class_property /
   declare_relation_property under a new name.
4. After declaring a new CLASS or RELATION, search_schema again for a plausible
   existing parent or child of the same kind, and call add_subclass_of to place it
   in the hierarchy whenever one clearly fits. Beyond what's directly evidenced,
   also propose up to {depth} level(s) of generalization/specialization around
   each concept this way — e.g. depth=1 also considers each concept's immediate
   parent or child category (a mentioned "poodle" also suggests DOG as a parent
   CLASS, linked via add_subclass_of); depth=0 sticks to only what's directly
   evidenced.{mcp_note}
5. After declaring a new CLASS, also check whether the text evidences a RELATION
   connecting it to another CLASS — existing or one you just declared — with
   either end (domain or range) being the new CLASS. E.g. after declaring SKILL
   and HOBBY from "Alice has programming skills, and hobbies such as wood
   working", also declare HAS_SKILL (domain="PERSON", range="SKILL") and
   HAS_HOBBY (domain="PERSON", range="HOBBY") if no equivalent relation already
   exists (search_schema first, as in step 2) — a class with no relation
   connecting it to anything is usually a sign a relation was missed, not that
   none is needed.
6. declare_relation_type requires the domain (head CLASS name) and range (tail
   CLASS name) — find or declare both classes first.
7. Use UPPER_SNAKE_CASE for CLASS, RELATION, and PROPERTY names.

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


# Per-server limits for reference MCP servers, overridable per entry with
# "startup_timeout" / "call_timeout" (seconds). Without them a server that starts but
# never answers — a wrong command waiting on stdin, one logging to stdout and corrupting
# JSON-RPC — blocked the handshake forever, and so did a stuck tool call (#113).
_MCP_STARTUP_TIMEOUT = 30.0
_MCP_CALL_TIMEOUT = 120.0   # WordNet's first call downloads its corpus


async def _connect_mcp_servers(
    mcp_servers: list[dict[str, Any]], stack: AsyncExitStack
) -> tuple[list[dict[str, Any]], dict[str, tuple[Any, str, Any]]]:
    """Connect to each configured MCP server (stdio/sse/streamable_http), returning
    OpenAI-shaped tool specs — name-prefixed per server as 'mcp__<server>__<tool>' to
    avoid collisions across servers — and a name -> (session, original_name) map for
    dispatch. All connections live for the duration of *stack*."""
    from datetime import timedelta

    import anyio
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    tool_specs: list[dict[str, Any]] = []
    dispatch: dict[str, tuple[Any, str, Any]] = {}

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
        # The call timeout is applied per tool call, not to the session: as a session
        # default it would also bound the handshake, which startup_timeout governs.
        call_timeout = timedelta(seconds=float(cfg.get("call_timeout", _MCP_CALL_TIMEOUT)))
        session = await stack.enter_async_context(ClientSession(read, write))
        startup_timeout = float(cfg.get("startup_timeout", _MCP_STARTUP_TIMEOUT))
        try:
            # anyio, not asyncio.wait_for: the MCP client's task groups must be entered
            # and exited from this same task.
            with anyio.fail_after(startup_timeout):
                await session.initialize()
                listed = await session.list_tools()
        except TimeoutError:
            raise SchemaError(
                f"reference MCP server {server_name!r} did not complete its startup handshake "
                f"within {startup_timeout:g}s — check its command or URL (a stdio server must speak "
                "MCP on stdout and log to stderr), or raise its \"startup_timeout\""
            ) from None
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
            dispatch[prefixed] = (session, tool.name, call_timeout)

    return tool_specs, dispatch


async def _call_mcp_tool(dispatch: dict[str, tuple[Any, str, Any]], name: str, arguments: dict[str, Any]) -> str:
    session, original_name, call_timeout = dispatch[name]
    result = await session.call_tool(original_name, arguments, read_timeout_seconds=call_timeout)
    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            return text
    return json.dumps({"content": [str(c) for c in result.content]})


try:
    _BaseExceptionGroup = BaseExceptionGroup  # type: ignore[name-defined]  # 3.11+
except NameError:  # 3.10: the backport anyio depends on
    from exceptiongroup import BaseExceptionGroup as _BaseExceptionGroup


def _leaf_errors(group: BaseException) -> list[BaseException]:
    if isinstance(group, _BaseExceptionGroup):
        return [leaf for e in group.exceptions for leaf in _leaf_errors(e)]
    return [group]


async def _unwrapped(coro: Any, progress: dict[str, Any]) -> Any:
    """Await *coro*, re-raising a single error from inside the MCP client's task groups as
    itself. Otherwise a clear SchemaError ("server X didn't complete its handshake") or a
    spawn failure reaches the caller as "unhandled errors in a TaskGroup (1 sub-exception)".

    Once the model loop has finished, stream errors raised while the MCP connections close
    are ignored: a server still busy with a call that timed out answers into a closed
    stream, which is teardown noise, not a failed run."""
    import anyio

    teardown = (anyio.BrokenResourceError, anyio.ClosedResourceError)
    try:
        return await coro
    except _BaseExceptionGroup as group:
        leaves = _leaf_errors(group)
        if progress.get("done") and all(isinstance(e, teardown) for e in leaves):
            return None
        if len(leaves) == 1 and isinstance(leaves[0], Exception):
            raise leaves[0] from None
        raise
    except (anyio.BrokenResourceError, anyio.ClosedResourceError):
        if progress.get("done"):
            return None
        raise


def _as_list(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def _reused_result(existing: SchemaSearchResult, requested_name: str) -> dict[str, Any]:
    """Tool result for a declaration the auto-dedup check redirected to *existing* — naming
    the reused element so the model can tell a real synonym from a wrong merge. A plain
    re-declaration of the same name is just the existing id, as declare_* returns it."""
    from blue_data_kb_source.schema.manager import _canonicalize_schema_name

    if _canonicalize_schema_name(requested_name) == existing.name:
        return {"id": existing.id}
    return {
        "id": existing.id,
        "reused_existing": {"type": existing.type, "name": existing.name, "description": existing.description},
        "note": (
            f"No new element was created: {requested_name!r} was judged to mean the same as the "
            f"existing {existing.type} {existing.name!r}. If it is a different concept, declare it "
            "again under a more specific name."
        ),
    }


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
        timeout: float | None = None,
    ) -> None:
        """Run the expansion agent. *timeout* caps the whole run in seconds — every model
        turn, tool call, and judge call — and raises SchemaError on expiry; None (default)
        means no cap. Without one, a stalled request waits out the OpenAI client's own
        600 s timeout and its silent retries, which is how a slow reasoning model can sit
        for many minutes with nothing printed. Declarations made before expiry are kept."""
        progress: dict[str, Any] = {"turns": 0, "phase": "model"}
        run = self._expand_schema_run(
            database, text, depth=depth, reference=reference, mcp_servers=mcp_servers,
            dedup_mode=dedup_mode, model=model, on_tool_call=on_tool_call,
            on_tool_result=on_tool_result, max_turns=max_turns, progress=progress,
        )
        if timeout is None:
            await _unwrapped(run, progress)
            return
        try:
            await asyncio.wait_for(_unwrapped(run, progress), timeout)
        except asyncio.TimeoutError:
            if progress["phase"] == "connecting":
                raise SchemaError(
                    f"schema expansion timed out after {timeout:g}s while connecting to its "
                    "reference MCP servers, before any model turn — the servers, not the model, "
                    "are the problem; check their command or URL."
                ) from None
            raise SchemaError(
                f"schema expansion with model {model!r} timed out after {timeout:g}s, "
                f"{progress['turns']} of at most {max_turns} model turn(s) in; schema elements "
                "declared before the timeout were kept. Reasoning models can take a minute "
                "per turn — raise the timeout for them."
            ) from None

    async def _expand_schema_run(
        self,
        database: str,
        text: str,
        *,
        depth: int,
        reference: Literal["llm", "mcp"],
        mcp_servers: list[dict[str, Any]] | None,
        dedup_mode: Literal["search", "llm", "none"],
        model: str,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None,
        max_turns: int,
        progress: dict[str, Any],
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

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]

        async with AsyncExitStack() as stack:
            tools = list(base_tools)
            mcp_dispatch: dict[str, tuple[Any, str, Any]] = {}
            if reference == "mcp":
                progress["phase"] = "connecting"
                mcp_tools, mcp_dispatch = await _connect_mcp_servers(mcp_servers or [], stack)
                progress["phase"] = "model"
                tools = tools + mcp_tools

            # Built after the MCP handshake, so a dead reference server is reported
            # as such rather than as a missing OPENAI_API_KEY (#138).
            client = openai.AsyncOpenAI()
            turns = 0
            while True:
                if turns >= max_turns:
                    break
                turns += 1
                progress["turns"] = turns
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
            progress["done"] = True  # the model loop finished; only teardown is left

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
            dup = await self._auto_dedup_check(
                database, CLASS, args["name"], args["description"], dedup_mode, model
            )
            if dup:
                return _reused_result(dup, args["name"])
            cid = await self._schema.declare_class(database, name=args["name"], description=args["description"])
            return {"id": cid}

        if fn == "declare_class_property":
            verdict = await self._check_property(
                database, args["class_id"], args["name"], args["description"], dedup_mode, model
            )
            if verdict is not None:
                return verdict
            pid = await self._schema.declare_class_property(
                database, name=args["name"], description=args["description"], class_id=args["class_id"]
            )
            return {"id": pid}

        if fn == "declare_relation_type":
            if args["name"].upper() in _TAXONOMIC_RELATION_NAMES:
                return {"error": (
                    f"'{args['name']}' expresses an is-a/type-of hierarchy, not a semantic "
                    "relationship between instances — declare_relation_type is not for this. "
                    "Use add_subclass_of on the two existing CLASS or RELATION ids instead."
                )}
            dup = await self._auto_dedup_check(
                database, RELATION, args["name"], args["description"], dedup_mode, model,
                domain=args["domain"], range_=args["range"],
            )
            if dup:
                return _reused_result(dup, args["name"])
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
            verdict = await self._check_property(
                database, args["relation_id"], args["name"], args["description"], dedup_mode, model
            )
            if verdict is not None:
                return verdict
            pid = await self._schema.declare_relation_property(
                database,
                name=args["name"],
                description=args["description"],
                relation_id=args["relation_id"],
            )
            return {"id": pid}

        if fn == "add_subclass_of":
            rejection = await self._judge_subclass(database, args["child_id"], args["parent_id"], model)
            if rejection:
                return {"error": rejection}
            await self._schema.add_is_subclass_of(database, args["child_id"], args["parent_id"])
            return {}

        if fn == "attach_property":
            await self._schema.attach_property(database, args["target_id"], args["property_id"])
            return {}

        return {"error": f"unknown tool: {fn}"}

    async def _auto_dedup_check(
        self,
        database: str,
        type_filter: str,
        name: str,
        description: str,
        dedup_mode: Literal["search", "llm", "none"],
        model: str,
        *,
        domain: str | list[str] | None = None,
        range_: str | list[str] | None = None,
    ) -> SchemaSearchResult | None:
        """Best-effort near-duplicate lookup run before creating a CLASS/RELATION node, regardless
        of whether the model called search_schema itself first — otherwise declare_class /
        declare_relation_type only dedup on an exact name match, so e.g. WORKED_AT and EMPLOYED_AT
        would sail through as two separate relations. Returns a same-meaning existing element to
        reuse, or None to proceed with creating a new one. A no-op when dedup_mode="none".

        Only strong evidence counts: the LLM judge's verdict (dedup_mode="llm") or a semantic
        hit at or above _SEMANTIC_DEDUP_MIN_SCORE. Keyword-only search never auto-merges. A
        RELATION candidate must also have a domain and range hierarchy-compatible with the
        requested ones, so a same-sounding relation between different classes stays separate."""
        if dedup_mode == "none":
            return None
        query = f"{name}: {description}" if description else name
        if type_filter == RELATION and domain is not None and range_ is not None:
            query += f" (domain: {_as_list(domain)}, range: {_as_list(range_)})"
        if dedup_mode == "llm":
            results = await self._judge_similar_schema(database, query, type_filter, model)
        elif self._search_indexer is not None and self._search_indexer.has_semantic:
            results = [
                r for r in self._search_indexer.search_schema(
                    database, query, k=5, mode="semantic", type_filter=type_filter
                )
                if r.score is not None and r.score >= _SEMANTIC_DEDUP_MIN_SCORE
            ]
        else:
            return None
        for result in results:
            if type_filter != RELATION or await self._relation_ends_compatible(
                database, result.id, domain, range_
            ):
                return result
        return None

    async def _relation_ends_compatible(
        self,
        database: str,
        relation_id: str,
        domain: str | list[str] | None,
        range_: str | list[str] | None,
    ) -> bool:
        """Whether *relation_id*'s domain and range each share a class lineage (the class
        itself, an ancestor, or a descendant) with the requested domain and range names."""
        from blue_data_kb_source.schema.manager import _canonicalize_schema_name

        if domain is None or range_ is None:
            return True
        ends = await self._schema.get_relation_domain_range(database, relation_id)
        for requested, existing in ((domain, ends["domain"]), (range_, ends["range"])):
            existing_ids = {node["id"] for node in existing}
            lineage: set[str] = set()
            for class_name in _as_list(requested):
                cls = await self._schema.find_class(database, _canonicalize_schema_name(class_name))
                if cls is None:
                    continue
                lineage.add(cls["id"])
                for walk in (self._schema.get_class_ancestors, self._schema.get_class_descendants):
                    lineage.update(n["id"] for n in await walk(database, cls["id"], max_hops=None))
            if not existing_ids & lineage:
                return False
        return True

    async def _check_property(
        self,
        database: str,
        owner_id: str,
        name: str,
        description: str,
        dedup_mode: Literal["search", "llm", "none"],
        model: str,
    ) -> dict[str, Any] | None:
        """Owner-scoped check run before a PROPERTY is declared on a CLASS/RELATION: does
        the property describe that owner at all (no DEGREE on an employment relation), and
        does it mean the same as a property the owner already has (JOIN_DATE next to
        START_DATE)? Neither is visible to search — BM25 shares no token between JOIN_DATE
        and START_DATE — so this is one forced-tool LLM call over the owner's own
        properties, which stays small however large the schema grows.

        Returns a tool result to send back instead of declaring (the reused property, or
        an error explaining the misplacement), or None to proceed. The same-meaning half
        is skipped when dedup_mode="none"; placement is always checked. A missing owner or
        an exact-name redeclaration falls through to declare_*_property, which handles it."""
        import openai

        from blue_data_kb_source._constants import DEFINE_PROPERTY, DOMAIN, RANGE
        from blue_data_kb_source.schema.manager import _canonicalize_schema_name

        owner = self._source.get_node(database, owner_id)
        if not owner or owner.get("label") not in (CLASS, RELATION):
            return None
        existing = [
            nb["node"] for nb in self._source.get_neighbors(
                database, owner_id, edge_type=DEFINE_PROPERTY, direction="out"
            )
        ]
        canonical = _canonicalize_schema_name(name)
        if any(p.get("properties", {}).get("name") == canonical for p in existing):
            return None
        candidates = existing if dedup_mode != "none" else []

        owner_props = owner.get("properties", {})
        owner_str = f"{owner['label']} {owner_props.get('name', '')} — {owner_props.get('description') or '(no description)'}"
        if owner["label"] == RELATION:
            ends = {
                edge: [nb["node"].get("properties", {}).get("name") for nb in
                       self._source.get_neighbors(database, owner_id, edge_type=edge, direction="out")]
                for edge in (DOMAIN, RANGE)
            }
            owner_str += f" (from {', '.join(ends[DOMAIN]) or '?'} to {', '.join(ends[RANGE]) or '?'})"
        candidates_str = "\n".join(
            f"- {p['id']}: {p.get('properties', {}).get('name')} — {p.get('properties', {}).get('description') or ''}"
            for p in candidates
        ) or "(none)"

        check_tool = {
            "type": "function",
            "function": {
                "name": "return_property_check",
                "description": "Report whether the proposed property belongs on this owner, and whether it duplicates one it already has.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "describes_owner": {
                            "type": "boolean",
                            "description": "true if the property is an attribute of the owner itself (of each of its instances)",
                        },
                        "same_as_id": {
                            "type": "string",
                            "description": "id of an existing property of the owner that means the same thing, or empty string if none",
                        },
                        "reason": {"type": "string", "description": "One short sentence."},
                    },
                    "required": ["describes_owner", "same_as_id", "reason"],
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
                        "You check a PROPERTY proposed for a KB CLASS or RELATION. First: is it an "
                        "attribute of the owner itself? A property of a RELATION describes one "
                        "instance of that relationship (an employment's START_DATE or SALARY); a "
                        "property of a CLASS describes one member (a person's BIRTH_DATE). A value "
                        "that belongs to some other concept — a DEGREE on an employment relation, "
                        "a company's FOUNDED_YEAR on a person — does not describe the owner. "
                        "Second: does it mean the same as one of the owner's existing properties, "
                        "judged by meaning, not wording (JOIN_DATE and START_DATE on an employment "
                        "both mean when it began)? Be conservative: only report a match when they "
                        "clearly are the same attribute."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Owner: {owner_str}\n"
                        f"Proposed property: {canonical} — {description}\n"
                        f"Owner's existing properties:\n{candidates_str}"
                    ),
                },
            ],
            tools=[check_tool],  # type: ignore[arg-type]
            tool_choice={"type": "function", "function": {"name": "return_property_check"}},  # type: ignore[arg-type]
        )
        verdict = json.loads(response.choices[0].message.tool_calls[0].function.arguments)
        same = next((p for p in candidates if p["id"] == verdict.get("same_as_id")), None)
        if same is not None:
            props = same.get("properties", {})
            return _reused_result(
                SchemaSearchResult(
                    id=same["id"], type=PROPERTY,
                    name=props.get("name", ""), description=props.get("description") or "",
                ),
                name,
            )
        if not verdict.get("describes_owner", True):
            return {"error": (
                f"Rejected: {canonical} does not describe {owner_props.get('name')} — "
                f"{verdict.get('reason', '')} Declare it on the CLASS or RELATION it does "
                "describe; if that element doesn't exist yet, declare it first."
            )}
        return None

    async def _judge_subclass(
        self, database: str, child_id: str, parent_id: str, model: str
    ) -> str | None:
        """Ask the LLM whether every CHILD is necessarily a PARENT before an is-a edge is
        written. The expansion agent otherwise links whatever seems topically nearby —
        TEAM under COMPANY, DEGREE under JOB_TITLE, COMPANY under ENGINEER — and a wrong
        edge silently widens every domain/range and ancestor check built on it.

        Returns a rejection message for the agent, or None to proceed. Malformed pairs
        (missing ids, mixed kinds, self-loops) and already-present edges skip the judge
        and fall through to add_is_subclass_of, which reports or no-ops them itself."""
        import openai

        from blue_data_kb_source._constants import IS_SUBCLASS_OF

        child = self._source.get_node(database, child_id)
        parent = self._source.get_node(database, parent_id)
        if (
            not child or not parent or child_id == parent_id
            or child.get("label") != parent.get("label")
            or child.get("label") not in (CLASS, RELATION)
        ):
            return None
        existing = self._source.get_neighbors(database, child_id, edge_type=IS_SUBCLASS_OF, direction="out")
        if any(nb["node"]["id"] == parent_id for nb in existing):
            return None

        def describe(node: dict) -> str:
            props = node.get("properties", {})
            desc = props.get("description") or "(no description)"
            return f"{props.get('name', '')} — {desc}"

        kind = child["label"]
        judge_tool = {
            "type": "function",
            "function": {
                "name": "return_verdict",
                "description": "Say whether the child is a genuine subtype of the parent.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "is_subtype": {"type": "boolean"},
                        "reason": {"type": "string", "description": "One short sentence."},
                    },
                    "required": ["is_subtype", "reason"],
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
                        f"You check proposed is-a (subtype) links between two KB {kind}es. The link "
                        "is valid only if every instance of the child is necessarily also an "
                        "instance of the parent, as their names and descriptions define them — "
                        "e.g. ENGINEER is a PERSON. Being related is not enough: a part, member, "
                        "location, holder, or product of something is not a subtype of it (a TEAM "
                        "is not a COMPANY, a DEGREE is not a JOB_TITLE, a COMPANY is not an "
                        "ENGINEER, a UNIVERSITY is not a COMPANY described as a business)."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Child: {describe(child)}\nParent: {describe(parent)}",
                },
            ],
            tools=[judge_tool],  # type: ignore[arg-type]
            tool_choice={"type": "function", "function": {"name": "return_verdict"}},  # type: ignore[arg-type]
        )
        verdict = json.loads(response.choices[0].message.tool_calls[0].function.arguments)
        if verdict.get("is_subtype"):
            return None
        child_name = child.get("properties", {}).get("name")
        parent_name = parent.get("properties", {}).get("name")
        return (
            f"Rejected: {child_name} is not a subtype of {parent_name} — {verdict.get('reason', '')} "
            "Only link X under Y when every X is necessarily a Y; if the two are related some "
            "other way, that is a RELATION (declare_relation_type), or nothing at all."
        )

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
