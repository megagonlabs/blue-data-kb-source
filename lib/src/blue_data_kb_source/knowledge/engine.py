from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._agent_tools import parse_tool_arguments, tool_error, tool_message_content
from blue_data_kb_source._constants import (
    CLASS,
    ENTITY,
    EXTRACTED_FROM,
    FACT,
    HAS_PROPERTY,
    HEAD,
    IS_INSTANCE_OF,
    IS_VALUE_OF,
    LITERAL,
    PROPERTY,
    RELATION,
    REPRESENTS,
    TAIL,
)
from blue_data_kb_source._text_match import match_score as _match_score
from blue_data_kb_source.knowledge.helpers import fact_parts, fetch_node_metadata, find_fact, property_values
from blue_data_kb_source.schema.manager import _canonicalize_schema_name
from blue_data_kb_source.types import EnrichmentResult, ValidationResult
from blue_data_kb_source import llm

if TYPE_CHECKING:
    from blue_data_kb_source.schema.manager import SchemaManager
    from blue_data_kb_source.search.indexer import KBSearchIndexer


# ------------------------------------------------------------------ #
# Tool schema                                                          #
# ------------------------------------------------------------------ #

# Bounds on the graph-walking query tools, so one call on a hub entity can't return
# the whole graph (#130). A capped result says so with "truncated": true.
_MAX_NEIGHBOR_DEPTH = 3
_MAX_ENRICH_RESULTS = 500
_MAX_TOOL_ROWS = 100


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
                "Returns {properties: [{literal_id, property_name, value, type, provenance}]}."
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
                "Returns {properties: [{literal_id, property_name, value, type, provenance}]}."
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
                    "depth": {"type": "integer", "description": f"Max hops (default 1, at most {_MAX_NEIGHBOR_DEPTH})"},
                },
                "required": ["entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_metadata",
            "description": (
                "Return metadata for a specific entity, fact, or property-value id: created/modified "
                "timestamps, a fact's derived confidence, and per-extraction evidence records "
                "(text, confidence, source, dates). "
                "Call this for each entity, fact, or property value you cite in your final answer when "
                "metadata has been requested."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Entity, fact, or literal (property value) id"},
                },
                "required": ["id"],
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

# A run of tool calls that all come back empty means the agent is rephrasing searches for
# something the KB doesn't hold; stop it and deliver what it has instead of spending the
# remaining turns (#133: 25 turns per question, 60-300 calls per case).
_MAX_CONSECUTIVE_EMPTY = 8


def _is_empty_result(result: Any) -> bool:
    if not isinstance(result, dict) or not result or "error" in result:
        return False
    if "id" in result and len(result) == 1:
        return result["id"] is None
    return all(isinstance(v, list) and not v for v in result.values())


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

Return an empty array if nothing relevant exists. Searches match names, descriptions
and class names by keyword: search with short distinctive terms (a name, a class, a
key noun) rather than whole sentences. If several differently worded searches all come
back empty, the knowledge base doesn't hold it — stop searching and call return_result
with what you have.

Current schema:
{schema}

Question: {question}\
"""

_VALIDATE_PROMPT = """\
You are a knowledge base validation agent. Use the provided tools to find evidence
that supports or contradicts the statement below.

Evidence comes in two shapes. A relationship between two entities is a FACT; an
attribute of an entity or fact (a title, a date, an age) is a PROPERTY VALUE, which
get_entity_properties / get_fact_properties return with a literal_id. Cite each in
its own shape:

  fact:            {{"fact_id": "<id>", "fact": "<fact name>", "head": "<head>", "tail": "<tail>", "relation": "<relation>"}}
  property value:  {{"literal_id": "<id>", "owner": "<entity or fact name>", "property": "<property>", "value": "<value>"}}

When done, call return_result with:

  {{"result": {{"supporting": [<items>], "counter": [<items>]}}}}

An item is supporting when it says what the statement says, and counter when it says
something incompatible with it — for an attribute, a stored value that differs from
the value the statement gives is counter-evidence, even when another stored value
agrees. Look up the properties of the entities the statement is about, not only their
facts.

Both lists may be empty. Cite only evidence you retrieved with the tools, each with
the fact_id or literal_id a tool returned for it: every item is checked against the
KB, and one that isn't there is reported as unverified rather than as evidence.

Current schema:
{schema}

Statement: {statement}\
"""

_ANSWER_NL_PROMPT = """\
You are given a question and structured answers retrieved from a knowledge base.
Write a clear, concise natural language answer based solely on the provided results.
If the results are empty, say that no relevant information was found.
The knowledge base records only what it has evidence for: when something the answer
depends on is missing from the results, say it is not on record — not that it does not
exist — and, where the results make it clear, what would resolve it.

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

_METADATA_NL_PROMPT = """\
You are given metadata about the specific entities, facts, or property values referenced in an
answer from a knowledge base: when each was created or last modified, what text it was extracted
from, and which source it came from (including the source's own original date, if known).

Write a clear, concise natural language summary of this provenance information, as a short
follow-up section after the main answer. If the metadata is empty, say that no provenance
information was available.

Metadata:
{metadata}
"""

_METADATA_INSTRUCTION = """

Additionally, for each distinct entity, fact, or property value referenced in your final answer, \
call get_metadata with its id before calling return_result.\
"""

_ENRICH_PROMPT = """\
You are a knowledge base enrichment agent. Your only job is to find the entities in
the knowledge base that the query is about: the seeds that everything within {depth}
hops of them will be collected from. The traversal itself is done for you.

Use search_entities (and get_entity_facts or get_neighbors if the query names an
entity only through its relations) to find them. When done, call return_result with
the ids of the seed entities, as a tool returned them:

  {{"result": {{"seed_entity_ids": ["<entity id>", ...]}}}}

Return an empty list if no entity in the knowledge base matches.

Current schema:
{schema}

Query: {query}\
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
        model: str | None = None,
        include_metadata: bool = False,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None = None,
        max_turns: int = 25,
    ) -> list[dict] | tuple[list[dict], dict]:
        schema_str = (await self._schema.fetch_database_schema(database)).to_str(simplified=True)
        system = _ANSWER_PROMPT.format(schema=schema_str, question=question)
        if include_metadata:
            system += _METADATA_INSTRUCTION
            raw, metadata = await self._run_agent(
                database, system, question, model, on_tool_call, on_tool_result,
                include_metadata=True, max_turns=max_turns,
            )
            return (raw if isinstance(raw, list) else []), metadata
        raw = await self._run_agent(
            database, system, question, model, on_tool_call, on_tool_result, max_turns=max_turns
        )
        return raw if isinstance(raw, list) else []

    async def validate(
        self,
        database: str,
        statement: str,
        model: str | None = None,
        include_metadata: bool = False,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None = None,
        max_turns: int = 25,
    ) -> ValidationResult | tuple[ValidationResult, dict]:
        schema_str = (await self._schema.fetch_database_schema(database)).to_str(simplified=True)
        system = _VALIDATE_PROMPT.format(schema=schema_str, statement=statement)
        if include_metadata:
            system += _METADATA_INSTRUCTION
            raw, metadata = await self._run_agent(
                database, system, statement, model, on_tool_call, on_tool_result,
                include_metadata=True, max_turns=max_turns,
            )
            return self._verified(database, raw), metadata
        raw = await self._run_agent(
            database, system, statement, model, on_tool_call, on_tool_result, max_turns=max_turns
        )
        return self._verified(database, raw)

    def _verified(self, database: str, raw: Any) -> ValidationResult:
        """The agent's evidence, keeping only facts and property values that exist
        in the KB (#129, #149).

        The agent's lists used to be returned as they came, so a model that
        hallucinated — or followed an instruction stored in KB text — could report
        support the KB doesn't contain. A fact item is resolved by its `fact_id`, or
        failing that by its exact head/relation/tail names; a property-value item
        (one carrying `literal_id`, or `owner`/`property`/`value`) by its
        `literal_id`, or failing that by its exact owner, property and value. Each
        is rewritten from the graph; one that resolves to nothing goes to
        `unverified`.
        """
        result = ValidationResult()
        if not isinstance(raw, dict):
            return result
        for key in ("supporting", "counter"):
            items = raw.get(key) or []
            for item in items if isinstance(items, list) else []:
                fact = self._resolve_cited(database, item) if isinstance(item, dict) else None
                if fact is None:
                    result.unverified.append({**item, "cited_as": key} if isinstance(item, dict)
                                             else {"item": item, "cited_as": key})
                else:
                    getattr(result, key).append(fact)
        return result

    def _entities_named(self, database: str, name: Any) -> list[dict]:
        """Every ENTITY named exactly *name* — entity names aren't unique."""
        if not isinstance(name, str) or not name.strip():
            return []
        return [n for n in self._source.match_nodes(database, label=ENTITY)
                if n.get("properties", {}).get("name") == name.strip()]

    def _resolve_cited(self, database: str, item: dict) -> dict | None:
        if item.get("literal_id") or ("property" in item and "value" in item and not item.get("fact_id")):
            return self._resolve_cited_property_value(database, item)
        return self._resolve_cited_fact(database, item)

    def _resolve_cited_property_value(self, database: str, item: dict) -> dict | None:
        literal_id = item.get("literal_id")
        node = self._source.get_node(database, literal_id) if isinstance(literal_id, str) else None
        if node is None or node.get("label") != LITERAL:
            node = None
            prop, value = item.get("property"), item.get("value")
            if not (isinstance(prop, str) and prop.strip()) or value is None:
                return None
            wanted_prop = _canonicalize_schema_name(prop)
            wanted_value = str(value).strip().casefold()
            owner = item.get("owner")
            owners = self._entities_named(database, owner) + [
                f for f in self._source.match_nodes(database, label=FACT)
                if isinstance(owner, str) and f.get("properties", {}).get("name") == owner.strip()
            ]
            for o in owners:
                for pv in property_values(self._source, database, o["id"]):
                    if (pv["property"] is not None
                            and pv["property"].get("properties", {}).get("name") == wanted_prop
                            and str(pv["literal"].get("properties", {}).get("value")).strip().casefold()
                                == wanted_value):
                        node = node or pv["literal"]
        if node is None:
            return None
        owner_nbs = self._source.get_neighbors(database, node["id"], edge_type=HAS_PROPERTY, direction="in")
        owner_node = owner_nbs[0]["node"] if owner_nbs else None
        prop_nbs = self._source.get_neighbors(database, node["id"], edge_type=IS_VALUE_OF, direction="out")
        props = node.get("properties", {})
        return {
            "literal_id": node["id"],
            "owner_id": owner_node["id"] if owner_node else None,
            "owner": self._owner_display_name(database, owner_node) if owner_node else None,
            "property": _node_name(prop_nbs),
            "value": props.get("value"),
            "type": props.get("type"),
        }

    def _owner_display_name(self, database: str, owner: dict) -> str | None:
        """An entity's name; a fact's name, or "head RELATION tail" when it has none."""
        name = owner.get("properties", {}).get("name")
        if owner.get("label") != FACT or name:
            return name
        parts = fact_parts(self._source, database, owner["id"])
        names = [(parts[k] or {}).get("properties", {}).get("name") for k in ("head", "relation", "tail")]
        return " ".join(n for n in names if n) or None

    def _resolve_cited_fact(self, database: str, item: dict) -> dict | None:
        fact_id = item.get("fact_id") or item.get("id")
        node = self._source.get_node(database, fact_id) if isinstance(fact_id, str) else None
        if node is None or node.get("label") != FACT:
            node = None
            heads = self._entities_named(database, item.get("head"))
            tails = self._entities_named(database, item.get("tail"))
            rel = item.get("relation")
            relations = ([r for r in self._source.match_nodes(database, label=RELATION)
                          if r.get("properties", {}).get("name") == _canonicalize_schema_name(rel)]
                         if isinstance(rel, str) and rel.strip() else [])
            for h in heads:
                for t in tails:
                    for r in relations:
                        node = node or find_fact(self._source, database, h["id"], r["id"], t["id"])
        if node is None:
            return None
        parts = fact_parts(self._source, database, node["id"])
        return {
            "fact_id": node["id"],
            "fact": node.get("properties", {}).get("name"),
            "head": _node_name([{"node": parts["head"]}]) if parts["head"] else None,
            "relation": _node_name([{"node": parts["relation"]}]) if parts["relation"] else None,
            "tail": _node_name([{"node": parts["tail"]}]) if parts["tail"] else None,
        }

    async def enrich(
        self,
        database: str,
        query: str,
        depth: int = 2,
        model: str | None = None,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None = None,
        max_turns: int = 25,
    ) -> list[EnrichmentResult]:
        schema_str = (await self._schema.fetch_database_schema(database)).to_str(simplified=True)
        system = _ENRICH_PROMPT.format(schema=schema_str, query=query, depth=depth)
        raw = await self._run_agent(
            database, system, query, model, on_tool_call, on_tool_result, max_turns=max_turns
        )
        seeds = self._enrich_seeds(database, raw)
        return self._enrich_walk(database, seeds, min(max(int(depth), 0), _MAX_NEIGHBOR_DEPTH))

    def _enrich_seeds(self, database: str, raw: Any) -> list[dict]:
        """The ENTITY nodes the agent named as seeds (#163). Ids are checked against
        the graph; a name is accepted as a fallback; anything else is dropped."""
        cited = raw.get("seed_entity_ids") if isinstance(raw, dict) else raw
        seeds: dict[str, dict] = {}
        for item in cited if isinstance(cited, list) else []:
            if isinstance(item, dict):
                item = item.get("entity_id") or item.get("id") or item.get("name") or item.get("entity")
            if not isinstance(item, str):
                continue
            node = self._source.get_node(database, item)
            found = [node] if node is not None and node.get("label") == ENTITY else self._entities_named(database, item)
            for n in found:
                seeds.setdefault(n["id"], n)
        return list(seeds.values())

    def _enrich_walk(self, database: str, seeds: list[dict], depth: int) -> list[EnrichmentResult]:
        """Everything within *depth* hops of the seeds, breadth-first, with the hop
        count from the graph (#163): distance 0 is a seed and its own property values;
        a fact one hop out, its property values and its other endpoint (with that
        entity's property values) are distance 1; and so on. Capped at
        _MAX_ENRICH_RESULTS rows."""
        results: list[EnrichmentResult] = []

        def emit(entity: str | None, relation: str | None, value: Any, distance: int) -> bool:
            if len(results) >= _MAX_ENRICH_RESULTS:
                return False
            results.append(EnrichmentResult(entity=entity, relation=relation,
                                            value=None if value is None else str(value), distance=distance))
            return True

        def emit_with_properties(owner_id: str, label: str, relation: str | None, distance: int) -> bool:
            if not emit(label, relation, None, distance):
                return False
            for pv in property_values(self._source, database, owner_id):
                prop = pv["property"]
                name = prop.get("properties", {}).get("name") if prop else None
                if not emit(label, name, pv["literal"].get("properties", {}).get("value"), distance):
                    return False
            return True

        visited_entities: set[str] = set()
        visited_facts: set[str] = set()
        frontier: list[dict] = []
        for seed in seeds:
            if seed["id"] not in visited_entities:
                visited_entities.add(seed["id"])
                frontier.append(seed)
                if not emit_with_properties(seed["id"], seed.get("properties", {}).get("name"), None, 0):
                    return results
        for distance in range(1, depth + 1):
            next_frontier: list[dict] = []
            for entity in frontier:
                for edge_type in (HEAD, TAIL):
                    for nb in self._source.get_neighbors(database, entity["id"], edge_type=edge_type, direction="in"):
                        fact = nb["node"]
                        if fact["id"] in visited_facts:
                            continue
                        visited_facts.add(fact["id"])
                        parts = fact_parts(self._source, database, fact["id"])
                        head = (parts["head"] or {}).get("properties", {}).get("name")
                        tail = (parts["tail"] or {}).get("properties", {}).get("name")
                        rel = (parts["relation"] or {}).get("properties", {}).get("name")
                        if not emit_with_properties(fact["id"], f"{head} → {tail}", rel, distance):
                            return results
                        for end in (parts["head"], parts["tail"]):
                            if end is not None and end["id"] not in visited_entities:
                                visited_entities.add(end["id"])
                                next_frontier.append(end)
                                if not emit_with_properties(end["id"], end.get("properties", {}).get("name"), None, distance):
                                    return results
            frontier = next_frontier
        return results

    async def answer_as_text(
        self,
        question: str,
        results: list[dict],
        model: str | None = None,
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
        model: str | None = None,
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

    async def metadata_as_text(
        self,
        metadata: dict,
        model: str | None = None,
    ) -> str:
        metadata_str = "\n".join(
            "- " + ", ".join(f"{k}={v!r}" for k, v in item.items())
            for item in metadata.values()
        ) or "(none)"
        prompt = _METADATA_NL_PROMPT.format(metadata=metadata_str)
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
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None = None,
        include_metadata: bool = False,
        max_turns: int = 25,
    ) -> Any:
        client = llm.AsyncClient()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ]
        result_payload: Any = None
        metadata_acc: dict[str, dict] = {}
        turns = 0
        consecutive_empty = 0

        while True:
            if turns >= max_turns:
                # Out of turns without a return_result: force one final call so what the
                # agent gathered is still delivered, rather than silently answering
                # nothing. A question whose answer is partly an *absence* (no audit record
                # on file) is exactly the kind that keeps an agent searching to the limit.
                result_payload = await self._force_return_result(client, model, messages)
                break
            turns += 1
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
                result_payload = await self._force_return_result(client, model, messages)
                break

            done = False
            for tc in msg.tool_calls:
                fn   = tc.function.name
                args, bad_args = parse_tool_arguments(tc.function.arguments)
                if on_tool_call:
                    on_tool_call(fn, args)

                if fn == "return_result" and bad_args is None:
                    # print(f"Received final result payload: {args.get('result')}")
                    result_payload = args.get("result")
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": "done"})
                    done = True
                else:
                    # A malformed call or a failing tool goes back to the model as an
                    # error it can correct, instead of aborting the whole run (#130).
                    if bad_args is not None:
                        tool_result = {"error": bad_args}
                    else:
                        try:
                            tool_result = await self._dispatch_tool(database, fn, args)
                        except Exception as exc:
                            tool_result = tool_error(exc)
                    if on_tool_result:
                        on_tool_result(fn, args, tool_result)
                    if include_metadata and fn == "get_metadata" and "metadata" in tool_result:
                        metadata_acc[args["id"]] = tool_result["metadata"]
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_message_content(tool_result),
                    })
                    consecutive_empty = consecutive_empty + 1 if _is_empty_result(tool_result) else 0

            if done:
                break
            if consecutive_empty >= _MAX_CONSECUTIVE_EMPTY:
                result_payload = await self._force_return_result(client, model, messages)
                break

        # print(f"Final result payload: {result_payload}")
        return (result_payload, metadata_acc) if include_metadata else result_payload

    async def _force_return_result(self, client: Any, model: str, messages: list[dict[str, Any]]) -> Any:
        """One extra model call restricted to return_result, serialising what the
        conversation has gathered so far; None if the model still returns nothing."""
        forced = await client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            tools=_TOOLS,       # type: ignore[arg-type]
            tool_choice={"type": "function", "function": {"name": "return_result"}},
        )
        forced_msg = forced.choices[0].message
        if forced_msg.tool_calls:
            args, bad_args = parse_tool_arguments(forced_msg.tool_calls[0].function.arguments)
            return None if bad_args else args.get("result")
        return None

    # ------------------------------------------------------------------ #
    # Single-turn completion                                               #
    # ------------------------------------------------------------------ #

    async def _complete(self, prompt: str, model: str) -> str:
        client = llm.AsyncClient()
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
            scored_e = []
            for node in self._source.match_nodes(database, label=ENTITY):
                props = node.get("properties", {})
                name  = props.get("name", "")
                desc  = props.get("description", "") or ""
                cls_nbs = self._source.get_neighbors(database, node["id"], edge_type=IS_INSTANCE_OF, direction="out")
                class_names = [nb["node"].get("properties", {}).get("name") or "" for nb in cls_nbs]
                # Class names count too, so "programming" finds Python as a PROGRAMMING_LANGUAGE.
                score = _match_score(args["query"], " ".join([name, desc, *class_names]))
                if score:
                    scored_e.append((score, {"id": node["id"], "name": name, "description": desc,
                                             "class_name": class_names[0] if class_names else None}))
            scored_e.sort(key=lambda x: -x[0])
            return {"entities": [e for _, e in scored_e[:20]]}

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
            scored_f = []
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
                searchable = " ".join(s for s in [name, desc, head_name, rel_name, tail_name] if s)
                score = _match_score(args["query"], searchable)
                if score:
                    scored_f.append((score, {
                        "id": node["id"], "name": name, "description": desc,
                        "head_name": head_name, "relation_name": rel_name, "tail_name": tail_name,
                    }))
            scored_f.sort(key=lambda x: -x[0])
            return {"facts": [f for _, f in scored_f[:20]]}

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
                    if len(facts) >= _MAX_TOOL_ROWS:
                        return {"facts": facts, "truncated": True}
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
                    "literal_id":    lit["id"],
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
                    "literal_id":    lit["id"],
                    "property_name": _node_name(prop_nbs),
                    "value":         lit_p.get("value"),
                    "type":          lit_p.get("type"),
                    "provenance":    self._provenance_text(database, lit["id"]),
                })
            return {"properties": props}

        if fn == "get_neighbors":
            entity_id        = args["entity_id"]
            max_depth        = min(max(int(args.get("depth", 1)), 1), _MAX_NEIGHBOR_DEPTH)
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
                                    if len(neighbors) >= _MAX_TOOL_ROWS:
                                        return {"neighbors": neighbors, "truncated": True}
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

        if fn == "get_metadata":
            try:
                return {"metadata": fetch_node_metadata(self._source, database, args["id"])}
            except KeyError as exc:
                return {"error": str(exc)}

        return {"error": f"unknown tool: {fn}"}

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _provenance_text(self, database: str, node_id: str) -> str | None:
        nbs = self._source.get_neighbors(database, node_id, edge_type=EXTRACTED_FROM, direction="out")
        texts = [nb["node"].get("properties", {}).get("text") for nb in nbs]
        return " | ".join(t for t in texts if t) or None


def _node_name(neighbors: list[dict]) -> str | None:
    return neighbors[0]["node"].get("properties", {}).get("name") if neighbors else None
