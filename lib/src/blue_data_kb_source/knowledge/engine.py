from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import (
    CLASS,
    ENTITY,
    EXTRACTED_FROM,
    FACT,
    HAS_PROPERTY,
    HEAD,
    IS_INSTANCE_OF,
    IS_VALUE_OF,
    PROPERTY,
    RELATION,
    REPRESENTS,
    TAIL,
)
from blue_data_kb_source.types import EnrichmentResult, KBError, ValidationResult

if TYPE_CHECKING:
    from blue_data_kb_source.schema.manager import SchemaManager
    from blue_data_kb_source.search.indexer import KBSearchIndexer


# ------------------------------------------------------------------ #
# Tool schema                                                          #
# ------------------------------------------------------------------ #

_TOOLS: list[dict[str, Any]] = [
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
                "Search schema elements (classes, relations, properties) by keyword or semantic similarity. "
                "Returns {schema_elements: [{id, type, name, description}]}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
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
            "name": "search_entities",
            "description": (
                "Search ENTITY nodes by keyword or semantic similarity. Matches against name, description, and property values. "
                "Returns {entities: [{id, name, description, class_name}]}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "mode":  {"type": "string", "enum": ["keyword", "semantic"], "description": "Search mode (default: keyword)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_facts",
            "description": (
                "Search FACT nodes by keyword or semantic similarity. Matches against head, relation, tail names, description, and property values. "
                "Returns {facts: [{id, name, description, head_name, relation_name, tail_name}]}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "mode":  {"type": "string", "enum": ["keyword", "semantic"], "description": "Search mode (default: keyword)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_entity_facts",
            "description": (
                "Return all facts that involve the given entity as head or tail. "
                "Returns {facts: [{fact_id, relation_name, head_name, tail_name, provenance}]}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                },
                "required": ["entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_entity_properties",
            "description": (
                "Return all property values attached to an entity. "
                "Returns {properties: [{property_name, value, type, provenance}]}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                },
                "required": ["entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fact_properties",
            "description": (
                "Return all property values attached to a fact. "
                "Returns {properties: [{property_name, value, type}]}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fact_id": {"type": "string"},
                },
                "required": ["fact_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_neighbors",
            "description": (
                "BFS from an entity through facts up to *depth* hops. "
                "Returns {neighbors: [{entity_id, entity_name, relation_name, head, tail, distance}]}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                    "depth": {"type": "integer", "description": "Max hops (default 1)"},
                },
                "required": ["entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "return_result",
            "description": "Return the final structured result. Call exactly once when done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {
                        "description": "The result payload as specified by the task.",
                    },
                },
                "required": ["result"],
            },
        },
    },
]

# ------------------------------------------------------------------ #
# System prompts                                                       #
# ------------------------------------------------------------------ #

_ANSWER_PROMPT = """\
You are a knowledge base query agent. Use the provided tools to search the knowledge
base and answer the question below.

When you have gathered enough information, you MUST call return_result to deliver the
answer. Never reply with plain text — always use the return_result tool.

Call return_result with a JSON array where each row is a flat dict whose keys reflect
the question asked. 

Choose natural column names based on the question and the schema. For example, use class names 
for entities, relation names for relationships, and property names for entity or fact properties.
Use domain and range class names for head and tail entities in relationships/facts.

Use null for any missing values. 
Include provenance text if available.

Examples:

  Question: "how old is Alice?"
  {{"result": [{{"age": "42", "provenance": null}}]}}

  Question: "list the companies Alice worked at"
  {{"result": [
    {{"company": "IBM",    "relation": "WORKED_AT", "start_date": "1998-06-01", "provenance": "Alice joined IBM in June 1998."}},
    {{"company": "Google", "relation": "WORKED_AT", "start_date": "May 2002",   "provenance": "Alice later joined Google."}}
  ]}}

Return an empty array if nothing relevant exists.

Current schema:
{schema}

Question: {question}\
"""

_VALIDATE_PROMPT = """\
You are a knowledge base validation agent. Use the provided tools to find evidence
that supports or contradicts the statement below.

When done, call return_result with:

  {{"result": {{
    "supporting": [{{"fact": "<fact name>", "head": "<head>", "tail": "<tail>", "relation": "<relation>"}}],
    "counter":    [{{"fact": "<fact name>", "head": "<head>", "tail": "<tail>", "relation": "<relation>"}}]
  }}}}

Both lists may be empty. Only include well-grounded counter-evidence from the KB.

Current schema:
{schema}

Statement: {statement}\
"""

_ANSWER_NL_PROMPT = """\
You are given a question and structured answers retrieved from a knowledge base.
Write a clear, concise natural language answer based solely on the provided results.
If the results are empty, say that no relevant information was found.

Question: {question}

Structured results:
{results}
"""

_VALIDATE_NL_PROMPT = """\
You are given a statement and evidence retrieved from a knowledge base.
Write a clear, concise natural language assessment of whether the statement is supported,
contradicted, or unverifiable based solely on the provided evidence.

Statement: {statement}

Supporting evidence:
{supporting}

Counter evidence:
{counter}
"""

_ENRICH_PROMPT = """\
You are a knowledge base enrichment agent. Starting from entities that match the
query, traverse the graph up to {depth} hops to find all related knowledge.

Use search_entities to find seed entities, then get_neighbors and get_entity_facts
to explore. When done, call return_result with a JSON array:

  {{"result": [
    {{"entity":   "<entity name or 'head → tail'>",
      "relation": "<relation name or null>",
      "value":    "<literal value or null>",
      "distance": <integer hop count>}},
    ...
  ]}}

Current schema:
{schema}

Query: {query}
Max depth: {depth}\
"""


# ------------------------------------------------------------------ #
# Engine                                                               #
# ------------------------------------------------------------------ #

class QueryEngine:
    """LLM-powered query engine that answers, validates, and enriches KB queries."""

    def __init__(
        self,
        source: GraphSource,
        schema_manager: SchemaManager,
        search_indexer: KBSearchIndexer | None = None,
    ) -> None:
        self._source = source
        self._schema = schema_manager
        self._search_indexer = search_indexer

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def answer(
        self,
        database: str,
        question: str,
        model: str = "gpt-4o",
    ) -> list[dict]:
        schema_str = (await self._schema.fetch_database_schema(database)).to_str(simplified=True)
        system = _ANSWER_PROMPT.format(schema=schema_str, question=question)
        raw = await self._run_agent(database, system, question, model)
        return raw if isinstance(raw, list) else []

    async def validate(
        self,
        database: str,
        statement: str,
        model: str = "gpt-4o",
    ) -> ValidationResult:
        schema_str = (await self._schema.fetch_database_schema(database)).to_str(simplified=True)
        system = _VALIDATE_PROMPT.format(schema=schema_str, statement=statement)
        raw = await self._run_agent(database, system, statement, model)
        if isinstance(raw, dict):
            return ValidationResult(
                supporting=raw.get("supporting", []),
                counter=raw.get("counter", []),
            )
        return ValidationResult(supporting=[], counter=[])

    async def enrich(
        self,
        database: str,
        query: str,
        depth: int = 2,
        model: str = "gpt-4o",
    ) -> list[EnrichmentResult]:
        schema_str = (await self._schema.fetch_database_schema(database)).to_str(simplified=True)
        system = _ENRICH_PROMPT.format(schema=schema_str, query=query, depth=depth)
        raw = await self._run_agent(database, system, query, model)
        return [
            EnrichmentResult(
                entity=item.get("entity"),
                relation=item.get("relation"),
                value=item.get("value"),
                distance=item.get("distance", 0),
            )
            for item in (raw if isinstance(raw, list) else [])
        ]

    async def answer_as_text(
        self,
        question: str,
        results: list[dict],
        model: str = "gpt-4o",
    ) -> str:
        results_str = "\n".join(
            "- " + ", ".join(f"{k}={v!r}" for k, v in item.items())
            for item in results
        ) or "(none)"
        prompt = _ANSWER_NL_PROMPT.format(question=question, results=results_str)
        return await self._complete(prompt, model)

    async def validate_as_text(
        self,
        statement: str,
        result: ValidationResult,
        model: str = "gpt-4o",
    ) -> str:
        def _fmt(items: list[dict]) -> str:
            if not items:
                return "(none)"
            return "\n".join(
                f"- {it.get('head','?')} —{it.get('relation','?')}→ {it.get('tail','?')}"
                for it in items
            )
        prompt = _VALIDATE_NL_PROMPT.format(
            statement=statement,
            supporting=_fmt(result.supporting),
            counter=_fmt(result.counter),
        )
        return await self._complete(prompt, model)

    # ------------------------------------------------------------------ #
    # Agent loop                                                           #
    # ------------------------------------------------------------------ #

    async def _run_agent(
        self,
        database: str,
        system_prompt: str,
        user_message: str,
        model: str,
    ) -> Any:
        try:
            import openai
        except ImportError as exc:
            raise KBError(
                "QueryEngine requires the 'openai' package: pip install openai"
            ) from exc

        client = openai.AsyncOpenAI()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ]
        result_payload: Any = None

        while True:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                tools=_TOOLS,       # type: ignore[arg-type]
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
                # LLM gave a prose response instead of calling return_result.
                # Force one extra turn so the accumulated context is serialised.
                forced = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=_TOOLS,
                    tool_choice={"type": "function", "function": {"name": "return_result"}},
                )
                forced_msg = forced.choices[0].message
                if forced_msg.tool_calls:
                    forced_args = json.loads(forced_msg.tool_calls[0].function.arguments)
                    result_payload = forced_args.get("result")
                break

            done = False
            for tc in msg.tool_calls:
                fn   = tc.function.name
                args = json.loads(tc.function.arguments)

                if fn == "return_result":
                    # print(f"Received final result payload: {args.get('result')}")
                    result_payload = args.get("result")
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": "done"})
                    done = True
                else:
                    # print(f"Dispatching tool {fn} with args: {args}")
                    tool_result = await self._dispatch_tool(database, fn, args)
                    # print(f"Tool {fn} returned: {tool_result}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(tool_result),
                    })

            if done:
                break

        # print(f"Final result payload: {result_payload}")
        return result_payload

    # ------------------------------------------------------------------ #
    # Single-turn completion                                               #
    # ------------------------------------------------------------------ #

    async def _complete(self, prompt: str, model: str) -> str:
        try:
            import openai
        except ImportError as exc:
            raise KBError(
                "QueryEngine requires the 'openai' package: pip install openai"
            ) from exc
        client = openai.AsyncOpenAI()
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""

    # ------------------------------------------------------------------ #
    # Tool dispatch                                                         #
    # ------------------------------------------------------------------ #

    async def _dispatch_tool(
        self, database: str, fn: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        if fn == "get_schema":
            schema = await self._schema.fetch_database_schema(database)
            return {"schema": schema.to_str(simplified=True)}

        if fn == "search_schema":
            mode = args.get("mode", "keyword")
            type_filter = args.get("type")
            if type_filter:
                type_filter = type_filter.upper()
            if self._search_indexer is not None:
                hits = self._search_indexer.search_schema(database, args["query"], k=20, mode=mode, type_filter=type_filter)
                return {"schema_elements": [
                    {"id": r.id, "type": r.type, "name": r.name, "description": r.description}
                    for r in hits
                ]}
            results = await self._schema.find_schema(database, args["query"], type_filter=type_filter)
            return {"schema_elements": [
                {"id": r.id, "type": r.type, "name": r.name, "description": r.description}
                for r in results
            ]}

        if fn == "search_entities":
            mode = args.get("mode", "keyword")
            if self._search_indexer is not None:
                hits = self._search_indexer.search_data(database, args["query"], k=20, mode=mode, type_filter=ENTITY)
                return {"entities": [
                    {"id": r.id, "name": r.name, "description": r.description, "class_name": r.class_name}
                    for r in hits
                ]}
            kw = args["query"].lower()
            results_e = []
            for node in self._source.match_nodes(database, label=ENTITY):
                props = node.get("properties", {})
                name  = props.get("name", "")
                desc  = props.get("description", "") or ""
                if kw in name.lower() or kw in desc.lower():
                    cls_nbs    = self._source.get_neighbors(database, node["id"], edge_type=IS_INSTANCE_OF, direction="out")
                    class_name = cls_nbs[0]["node"].get("properties", {}).get("name") if cls_nbs else None
                    results_e.append({"id": node["id"], "name": name, "description": desc, "class_name": class_name})
            return {"entities": results_e}

        if fn == "search_facts":
            mode = args.get("mode", "keyword")
            if self._search_indexer is not None:
                hits = self._search_indexer.search_data(database, args["query"], k=20, mode=mode, type_filter=FACT)
                return {"facts": [
                    {
                        "id": r.id, "name": r.name, "description": r.description,
                        "head_name": r.head_name, "relation_name": r.relation_name, "tail_name": r.tail_name,
                    }
                    for r in hits
                ]}
            kw = args["query"].lower()
            results_f = []
            for node in self._source.match_nodes(database, label=FACT):
                props = node.get("properties", {})
                name  = props.get("name", "")
                desc  = props.get("description", "") or ""
                rel_nbs  = self._source.get_neighbors(database, node["id"], edge_type=REPRESENTS, direction="out")
                head_nbs = self._source.get_neighbors(database, node["id"], edge_type=HEAD,       direction="out")
                tail_nbs = self._source.get_neighbors(database, node["id"], edge_type=TAIL,       direction="out")
                head_name = _node_name(head_nbs) or ""
                rel_name  = _node_name(rel_nbs)  or ""
                tail_name = _node_name(tail_nbs) or ""
                searchable = " ".join(s for s in [name, desc, head_name, rel_name, tail_name] if s).lower()
                if kw in searchable:
                    results_f.append({
                        "id": node["id"], "name": name, "description": desc,
                        "head_name": head_name, "relation_name": rel_name, "tail_name": tail_name,
                    })
            return {"facts": results_f}

        if fn == "get_entity_facts":
            entity_id   = args["entity_id"]
            seen: set[str] = set()
            facts = []
            for edge_type in (HEAD, TAIL):
                for nb in self._source.get_neighbors(database, entity_id, edge_type=edge_type, direction="in"):
                    fact_node = nb["node"]
                    fact_id   = fact_node["id"]
                    if fact_id in seen:
                        continue
                    seen.add(fact_id)
                    rel_nbs  = self._source.get_neighbors(database, fact_id, edge_type=REPRESENTS, direction="out")
                    head_nbs = self._source.get_neighbors(database, fact_id, edge_type=HEAD,       direction="out")
                    tail_nbs = self._source.get_neighbors(database, fact_id, edge_type=TAIL,       direction="out")
                    facts.append({
                        "fact_id":       fact_id,
                        "relation_name": _node_name(rel_nbs),
                        "head_name":     _node_name(head_nbs),
                        "tail_name":     _node_name(tail_nbs),
                        "provenance":    self._provenance_text(database, fact_id),
                    })
            return {"facts": facts}

        if fn == "get_entity_properties":
            entity_id = args["entity_id"]
            props = []
            for nb in self._source.get_neighbors(database, entity_id, edge_type=HAS_PROPERTY, direction="out"):
                lit      = nb["node"]
                lit_p    = lit.get("properties", {})
                prop_nbs = self._source.get_neighbors(database, lit["id"], edge_type=IS_VALUE_OF, direction="out")
                props.append({
                    "property_name": _node_name(prop_nbs),
                    "value":         lit_p.get("value"),
                    "type":          lit_p.get("type"),
                    "provenance":    self._provenance_text(database, lit["id"]),
                })
            return {"properties": props}

        if fn == "get_fact_properties":
            fact_id = args["fact_id"]
            props = []
            for nb in self._source.get_neighbors(database, fact_id, edge_type=HAS_PROPERTY, direction="out"):
                lit      = nb["node"]
                lit_p    = lit.get("properties", {})
                prop_nbs = self._source.get_neighbors(database, lit["id"], edge_type=IS_VALUE_OF, direction="out")
                props.append({
                    "property_name": _node_name(prop_nbs),
                    "value":         lit_p.get("value"),
                    "type":          lit_p.get("type"),
                })
            return {"properties": props}

        if fn == "get_neighbors":
            entity_id        = args["entity_id"]
            max_depth        = int(args.get("depth", 1))
            visited_entities: set[str] = {entity_id}
            visited_facts:    set[str] = set()
            queue: list[tuple[str, int]] = [(entity_id, 0)]
            neighbors = []

            while queue:
                curr_id, dist = queue.pop(0)
                if dist >= max_depth:
                    continue
                for edge_type in (HEAD, TAIL):
                    for nb in self._source.get_neighbors(database, curr_id, edge_type=edge_type, direction="in"):
                        fact_node = nb["node"]
                        fact_id   = fact_node["id"]
                        if fact_id in visited_facts:
                            continue
                        visited_facts.add(fact_id)
                        rel_nbs  = self._source.get_neighbors(database, fact_id, edge_type=REPRESENTS, direction="out")
                        head_nbs = self._source.get_neighbors(database, fact_id, edge_type=HEAD,       direction="out")
                        tail_nbs = self._source.get_neighbors(database, fact_id, edge_type=TAIL,       direction="out")
                        rel_name  = _node_name(rel_nbs)
                        head_name = _node_name(head_nbs)
                        tail_name = _node_name(tail_nbs)
                        for end_edge in (HEAD, TAIL):
                            for end_nb in self._source.get_neighbors(database, fact_id, edge_type=end_edge, direction="out"):
                                other     = end_nb["node"]
                                other_id  = other["id"]
                                other_name = other.get("properties", {}).get("name")
                                if other_id not in visited_entities:
                                    visited_entities.add(other_id)
                                    neighbors.append({
                                        "entity_id":   other_id,
                                        "entity_name": other_name,
                                        "relation_name": rel_name,
                                        "head":   head_name,
                                        "tail":   tail_name,
                                        "distance": dist + 1,
                                    })
                                    queue.append((other_id, dist + 1))

            return {"neighbors": neighbors}

        return {"error": f"unknown tool: {fn}"}

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _provenance_text(self, database: str, node_id: str) -> str | None:
        nbs = self._source.get_neighbors(database, node_id, edge_type=EXTRACTED_FROM, direction="out")
        return nbs[0]["node"].get("properties", {}).get("text") if nbs else None


def _node_name(neighbors: list[dict]) -> str | None:
    return neighbors[0]["node"].get("properties", {}).get("name") if neighbors else None
