from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import ENTITY, FACT
from blue_data_kb_source.knowledge.helpers import entity_classes, fact_parts, node_name
from blue_data_kb_source.resolution.resolver import NodeResolver
from blue_data_kb_source.types import DataSearchResult, ExtractionError

if TYPE_CHECKING:
    from blue_data_kb_source.knowledge.manager import KnowledgeManager
    from blue_data_kb_source.provenance.tracker import ProvenanceTracker
    from blue_data_kb_source.schema.manager import SchemaManager
    from blue_data_kb_source.search.indexer import KBSearchIndexer

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
            "name": "find_class",
            "description": "Look up a CLASS node by name. Returns {id, name, description} or {id: null}.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_relation_type",
            "description": "Look up a RELATION node by name. Returns {id, name, description} or {id: null}.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_entity",
            "description": "Look up an ENTITY node by name. Returns {id, name, description} or {id: null}.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_fact",
            "description": (
                "Look up an existing FACT by its head entity, relation, and tail entity. "
                "Returns {id, name} or {id: null}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "head_entity_id": {"type": "string"},
                    "relation_id": {"type": "string"},
                    "tail_entity_id": {"type": "string"},
                },
                "required": ["head_entity_id", "relation_id", "tail_entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_schema",
            "description": (
                "Search existing schema elements (classes, relations, properties) for one that "
                "matches a given concept — even under a different name. Use this to find the "
                "CLASS/RELATION/PROPERTY that a mention in the text corresponds to. Returns "
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
            "name": "add_entity",
            "description": "Add an ENTITY instance. Returns {id}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "class_id": {"type": "string"},
                    "text": {"type": "string", "description": "The full verbatim sentence (not just the bare name) this entity was extracted from"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_fact",
            "description": "Add a FACT (relation instance between two entities). Returns {id}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "head_entity_id": {"type": "string"},
                    "tail_entity_id": {"type": "string"},
                    "relation_id": {"type": "string"},
                    "name": {"type": "string"},
                    "text": {"type": "string", "description": "The full verbatim sentence (not just the bare relation phrase) this fact was extracted from"},
                },
                "required": ["head_entity_id", "tail_entity_id", "relation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_entity_property_value",
            "description": "Attach a LITERAL property value to an ENTITY. Returns {id}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                    "property_id": {"type": "string"},
                    "value": {"type": "string"},
                    "literal_type": {
                        "type": "string",
                        "enum": ["NUMBER", "DATE", "BOOLEAN", "STRING"],
                    },
                    "text": {"type": "string", "description": "The full verbatim sentence (not just the bare value) this value was extracted from"},
                },
                "required": ["entity_id", "property_id", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_fact_property_value",
            "description": "Attach a LITERAL property value to a FACT. Returns {id}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact_id": {"type": "string"},
                    "property_id": {"type": "string"},
                    "value": {"type": "string"},
                    "literal_type": {
                        "type": "string",
                        "enum": ["NUMBER", "DATE", "BOOLEAN", "STRING"],
                    },
                    "text": {"type": "string", "description": "The full verbatim sentence (not just the bare value) this value was extracted from"},
                },
                "required": ["fact_id", "property_id", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "link_extraction",
            "description": (
                "Link a new text span to an ENTITY, FACT, or LITERAL that already "
                "exists (found via find_entity/find_fact). Use this instead of "
                "add_entity/add_fact/add_*_property_value when the item was already "
                "found to exist, so new text spans accumulate as additional "
                "EXTRACTED_FROM links rather than creating a duplicate. Returns {id} "
                "of the new extraction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "element_id": {"type": "string", "description": "id of the existing entity, fact, or literal"},
                    "text": {"type": "string", "description": "The full verbatim sentence this was found in"},
                },
                "required": ["element_id", "text"],
            },
        },
    },
]

_SYSTEM_PROMPT_TEMPLATE = """\
You are a knowledge base (KB) extraction agent.

The KB stores:
  CLASS     — category of entities (e.g. PERSON, COMPANY)
  RELATION  — type of relationship (e.g. WORKED_AT, LIVES_IN)
  PROPERTY  — attribute of a class or relation (e.g. AGE, START_DATE)
  ENTITY    — a specific instance of a class (e.g. Alice, IBM)
  FACT      — a specific relation between two entities
  LITERAL   — a concrete property value (e.g. "28", "1998-06-01")

This pass only extracts against the schema as it currently stands — it never
declares new CLASSes, RELATIONs, or PROPERTIES. If nothing in the current schema
covers something in the text (use search_schema to check), skip that item rather
than inventing a schema element for it.

{workflow}

When calling add_entity, add_fact, add_entity_property_value,
add_fact_property_value, or link_extraction, always pass the full sentence
(verbatim, exactly as written) that the item was extracted from as the
`text` argument — never just the bare name, relation phrase, or value on its
own. A short span like "Alice" or "1998-06" gives a reader no way to verify
the extraction later; the surrounding sentence does. If the item depends on
two adjacent sentences, include the verbatim span covering both. Reusing an
existing item via link_extraction records the new text as an additional link
without creating a duplicate.

Source of this text: {source}

Current schema:
{schema}

Potentially related existing data (found by searching the KB with this text;
these are hints from a keyword/semantic match, not confirmed — always verify
with find_entity/find_fact before reusing one, exactly as you would for any
other candidate):
{related_data}

Extraction queries:
{queries}

Work through the text systematically and stop calling tools when you are done.\
"""

# ------------------------------------------------------------------ #
# Staged extraction — entities, then relations, then property values.  #
# Each stage is its own agentic pass over a narrower tool set, told   #
# what the prior stage(s) already resolved rather than rediscovering  #
# it — mirroring the existing schema-expansion-then-extraction split. #
# ------------------------------------------------------------------ #

STAGE_ENTITIES = "entities"
STAGE_RELATIONS = "relations"
STAGE_ENTITY_PROPERTIES = "entity_properties"
STAGE_FACT_PROPERTIES = "fact_properties"

STAGES = (STAGE_ENTITIES, STAGE_RELATIONS, STAGE_ENTITY_PROPERTIES, STAGE_FACT_PROPERTIES)

_STAGE_TOOL_NAMES: dict[str, list[str]] = {
    STAGE_ENTITIES: ["get_schema", "search_schema", "find_class", "find_entity", "add_entity", "link_extraction"],
    STAGE_RELATIONS: ["get_schema", "search_schema", "find_relation_type", "find_fact", "add_fact", "link_extraction"],
    STAGE_ENTITY_PROPERTIES: ["get_schema", "search_schema", "add_entity_property_value", "link_extraction"],
    STAGE_FACT_PROPERTIES: ["get_schema", "search_schema", "add_fact_property_value", "link_extraction"],
}

_STAGE_WORKFLOWS: dict[str, str] = {
    STAGE_ENTITIES: """\
Stage: entity extraction. Only entities exist yet — relations and property
values are handled in later stages, so ignore them here.

For each entity mentioned in the text: call find_entity first. If it already
exists, call link_extraction with its id and the new text span instead of
add_entity. Otherwise find its CLASS via search_schema — if no matching CLASS
exists, skip this entity — then call add_entity. Use names exactly as they
appear in the text.\
""",
    STAGE_RELATIONS: """\
Stage: relation extraction. Entities were already resolved in a prior stage —
never call find_entity or add_entity here, and never invent an entity that
isn't in the list below; if a relationship involves one, skip it.

Entities already resolved for this text (id, name, class):
{entities}

For each relationship the text states between two of the entities above: find
the RELATION type via search_schema. If no matching RELATION exists, skip the
relationship. Otherwise call find_fact using the ids above as
head_entity_id/tail_entity_id. If the fact already exists, call
link_extraction with its id and the new text span instead of add_fact.
Otherwise call add_fact.\
""",
    STAGE_ENTITY_PROPERTIES: """\
Stage: entity property extraction. Entities were already resolved in a prior
stage — never invent an entity that isn't in the list below.

Entities already resolved for this text (id, name, class):
{entities}

For each attribute value the text states about one of the entities above:
find the PROPERTY on its class via search_schema. If no matching PROPERTY
exists, skip this value. Otherwise call add_entity_property_value using the
entity_id from the list above.\
""",
    STAGE_FACT_PROPERTIES: """\
Stage: fact property extraction. Facts were already resolved in a prior
stage — never invent a fact that isn't in the list below.

Facts already resolved for this text (id, head, relation, tail):
{facts}

For each attribute value the text states about one of the facts above: find
the PROPERTY on its relation via search_schema. If no matching PROPERTY
exists, skip this value. Otherwise call add_fact_property_value using the
fact_id from the list above.\
""",
}


def _tools_for(stage: str) -> list[dict[str, Any]]:
    names = set(_STAGE_TOOL_NAMES[stage])
    return [t for t in _TOOLS if t["function"]["name"] in names]


def _render_resolved_entities(entities: list[dict[str, Any]]) -> str:
    if not entities:
        return "(none resolved in this pass)"
    lines = []
    for e in entities:
        suffix = f" ({e['class_name']})" if e.get("class_name") else ""
        lines.append(f"- id={e['id']}  {e['name']}{suffix}")
    return "\n".join(lines)


def _render_resolved_facts(facts: list[dict[str, Any]]) -> str:
    if not facts:
        return "(none resolved in this pass)"
    lines = [f"- id={f['id']}  {f['head_name']} —{f['relation_name']}→ {f['tail_name']}" for f in facts]
    return "\n".join(lines)


def _describe_source(node: dict[str, Any] | None) -> str:
    """Human-readable name/description of a SOURCE node, for the system prompt."""
    if node is None:
        return "(not specified)"
    props = node.get("properties", {})
    name = props.get("name")
    description = props.get("description")
    if name and description:
        return f"{name} — {description}"
    return name or description or "(not specified)"


def _describe_related_data(results: list[DataSearchResult]) -> str:
    """Render search_data hits (existing ENTITY/FACT nodes) for the system prompt."""
    if not results:
        return "(none found)"
    lines: list[str] = []
    for r in results:
        if r.type == ENTITY:
            suffix = f" ({r.class_name})" if r.class_name else ""
            lines.append(f"- {r.name}{suffix}")
        else:  # FACT
            lines.append(f"- {r.head_name} —{r.relation_name}→ {r.tail_name}")
    return "\n".join(lines)


class ExtractionEngine:
    """LLM-powered extraction engine that populates the KB from raw text."""

    def __init__(
        self,
        source: GraphSource,
        schema_manager: SchemaManager,
        knowledge_manager: KnowledgeManager,
        provenance_tracker: ProvenanceTracker,
        search_indexer: KBSearchIndexer | None = None,
    ) -> None:
        self._source = source
        self._schema = schema_manager
        self._knowledge = knowledge_manager
        self._provenance = provenance_tracker
        self._search_indexer = search_indexer
        self._resolver = NodeResolver(source, search_indexer)

    async def import_text(
        self,
        database: str,
        text: str,
        queries: list[str] | None = None,
        model: str = "gpt-4o",
        source_id: str | None = None,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None = None,
        max_turns: int = 25,
        related_data_k: int = 10,
    ) -> None:
        """Run the staged extraction pipeline: entities, then relations, then
        entity property values, then fact property values. Each stage is its
        own agentic pass over a narrower tool set than the last, told what the
        prior stage(s) resolved (by id) instead of rediscovering it.
        """
        try:
            import openai
        except ImportError as exc:
            raise ExtractionError(
                "import_text requires the 'openai' package: pip install openai"
            ) from exc

        schema_str = (await self._schema.fetch_database_schema(database)).to_str(simplified=True)
        queries_str = (
            "\n".join(f"- {q}" for q in queries)
            if queries
            else "Extract all notable entities, relations, and property values."
        )
        source_node = self._source.get_node(database, source_id) if source_id is not None else None
        source_str = _describe_source(source_node)
        if self._search_indexer is not None and related_data_k > 0:
            related_data_str = _describe_related_data(
                self._search_indexer.search_data(database, text, k=related_data_k)
            )
        else:
            related_data_str = "(search not configured for this database)"

        def build_prompt(workflow: str) -> str:
            return _SYSTEM_PROMPT_TEMPLATE.format(
                workflow=workflow, schema=schema_str, queries=queries_str,
                source=source_str, related_data=related_data_str,
            )

        client = openai.AsyncOpenAI()

        entity_calls = await self._run_stage(
            database, client, model, max_turns, source_id, on_tool_call, on_tool_result,
            tools=_tools_for(STAGE_ENTITIES),
            system_prompt=build_prompt(_STAGE_WORKFLOWS[STAGE_ENTITIES]),
            text=text,
        )
        entities = self._resolved_entities(database, entity_calls)

        relation_calls = await self._run_stage(
            database, client, model, max_turns, source_id, on_tool_call, on_tool_result,
            tools=_tools_for(STAGE_RELATIONS),
            system_prompt=build_prompt(
                _STAGE_WORKFLOWS[STAGE_RELATIONS].format(entities=_render_resolved_entities(entities))
            ),
            text=text,
        )
        facts = self._resolved_facts(database, relation_calls)

        await self._run_stage(
            database, client, model, max_turns, source_id, on_tool_call, on_tool_result,
            tools=_tools_for(STAGE_ENTITY_PROPERTIES),
            system_prompt=build_prompt(
                _STAGE_WORKFLOWS[STAGE_ENTITY_PROPERTIES].format(entities=_render_resolved_entities(entities))
            ),
            text=text,
        )

        await self._run_stage(
            database, client, model, max_turns, source_id, on_tool_call, on_tool_result,
            tools=_tools_for(STAGE_FACT_PROPERTIES),
            system_prompt=build_prompt(
                _STAGE_WORKFLOWS[STAGE_FACT_PROPERTIES].format(facts=_render_resolved_facts(facts))
            ),
            text=text,
        )

    async def _run_stage(
        self,
        database: str,
        client: Any,
        model: str,
        max_turns: int,
        source_id: str | None,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None,
        *,
        tools: list[dict[str, Any]],
        system_prompt: str,
        text: str,
    ) -> list[tuple[str, dict[str, Any], Any]]:
        """Run one agentic tool-calling pass to convergence (or max_turns).

        Returns every (tool_name, args, result) triple from this pass, so the
        caller can harvest which entities/facts it resolved for the next stage.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]
        calls: list[tuple[str, dict[str, Any], Any]] = []
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
                    result = await self._dispatch_tool(database, tc.function.name, args, source_id)
                except Exception as exc:
                    result = {"error": str(exc)}
                if on_tool_result:
                    on_tool_result(tc.function.name, args, result)
                calls.append((tc.function.name, args, result))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })
        return calls

    def _resolved_entities(
        self, database: str, calls: list[tuple[str, dict[str, Any], Any]]
    ) -> list[dict[str, Any]]:
        """Entities the entity stage touched: created, found, or re-linked."""
        ids: set[str] = set()
        for name, args, result in calls:
            if name in ("add_entity", "find_entity") and isinstance(result, dict) and result.get("id"):
                ids.add(result["id"])
            elif name == "link_extraction" and args.get("element_id"):
                ids.add(args["element_id"])
        resolved = []
        for eid in ids:
            node = self._source.get_node(database, eid)
            if node is None or node.get("label") != ENTITY:
                continue
            classes = entity_classes(self._source, database, eid)
            resolved.append({
                "id": eid,
                "name": node_name(node),
                "class_name": node_name(classes[0]) if classes else None,
            })
        return resolved

    def _resolved_facts(
        self, database: str, calls: list[tuple[str, dict[str, Any], Any]]
    ) -> list[dict[str, Any]]:
        """Facts the relation stage touched: created, found, or re-linked."""
        ids: set[str] = set()
        for name, args, result in calls:
            if name in ("add_fact", "find_fact") and isinstance(result, dict) and result.get("id"):
                ids.add(result["id"])
            elif name == "link_extraction" and args.get("element_id"):
                ids.add(args["element_id"])
        resolved = []
        for fid in ids:
            node = self._source.get_node(database, fid)
            if node is None or node.get("label") != FACT:
                continue
            parts = fact_parts(self._source, database, fid)
            head, relation, tail = parts["head"], parts["relation"], parts["tail"]
            if head is None or relation is None or tail is None:
                continue
            resolved.append({
                "id": fid,
                "head_name": node_name(head),
                "relation_name": node_name(relation),
                "tail_name": node_name(tail),
            })
        return resolved

    async def _dispatch_tool(
        self,
        database: str,
        fn: str,
        args: dict[str, Any],
        source_id: str | None,
    ) -> dict[str, Any]:
        if fn == "get_schema":
            schema = await self._schema.fetch_database_schema(database)
            return {"schema": schema.to_str(simplified=True)}

        if fn == "search_schema":
            type_filter = args.get("type")
            if type_filter:
                type_filter = type_filter.upper()
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

        if fn == "find_class":
            node = await self._schema.find_class(database, args["name"])
            if node:
                p = node.get("properties", {})
                return {"id": node["id"], "name": p.get("name"), "description": p.get("description")}
            return {"id": None}

        if fn == "find_relation_type":
            node = await self._schema.find_relation_type(database, args["name"])
            if node:
                p = node.get("properties", {})
                return {"id": node["id"], "name": p.get("name"), "description": p.get("description")}
            return {"id": None}

        if fn == "find_entity":
            node = await self._knowledge.find_entity(database, args["name"])
            if node is None:
                # find_entity is a deliberately frozen exact-match lookup (see
                # resolution/resolver.py); fall back here to a case/whitespace/
                # punctuation-insensitive match so "ibm" resolves to an existing
                # "IBM" instead of creating a duplicate. Exclude the search tier —
                # a semantically similar but distinct entity is not the same entity.
                candidates = await self._resolver.resolve_entity(
                    database, args["name"], k=1, include_search=False
                )
                if candidates:
                    node = self._source.get_node(database, candidates[0].node_id)
            if node:
                p = node.get("properties", {})
                return {"id": node["id"], "name": p.get("name"), "description": p.get("description")}
            return {"id": None}

        if fn == "find_fact":
            node = await self._knowledge.find_fact(
                database, args["head_entity_id"], args["relation_id"], args["tail_entity_id"]
            )
            if node:
                return {"id": node["id"], "name": node.get("properties", {}).get("name")}
            return {"id": None}

        if fn == "add_entity":
            eid = await self._knowledge.add_entity(
                database,
                name=args["name"],
                description=args.get("description"),
                class_id=args.get("class_id"),
                text=args.get("text"),
                source_id=source_id,
            )
            return {"id": eid}

        if fn == "add_fact":
            fid = await self._knowledge.add_fact(
                database,
                name=args.get("name"),
                head_entity_id=args["head_entity_id"],
                tail_entity_id=args["tail_entity_id"],
                relation_id=args["relation_id"],
                text=args.get("text"),
                source_id=source_id,
            )
            return {"id": fid}

        if fn == "add_entity_property_value":
            lit_id = await self._knowledge.add_entity_property_value(
                database,
                value=args["value"],
                literal_type=args.get("literal_type"),
                entity_id=args["entity_id"],
                property_id=args["property_id"],
                text=args.get("text"),
                source_id=source_id,
            )
            return {"id": lit_id}

        if fn == "add_fact_property_value":
            lit_id = await self._knowledge.add_fact_property_value(
                database,
                value=args["value"],
                literal_type=args.get("literal_type"),
                fact_id=args["fact_id"],
                property_id=args["property_id"],
                text=args.get("text"),
                source_id=source_id,
            )
            return {"id": lit_id}

        if fn == "link_extraction":
            extraction_id = await self._provenance.add_extraction(
                database,
                text=args["text"],
                source_id=source_id,
                element_id=args["element_id"],
            )
            return {"id": extraction_id}

        return {"error": f"unknown tool: {fn}"}
