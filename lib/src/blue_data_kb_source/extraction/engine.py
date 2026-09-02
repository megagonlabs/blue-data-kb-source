from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source.types import ExtractionError

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
                    "text": {"type": "string", "description": "Verbatim text span this entity was extracted from"},
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
                    "text": {"type": "string", "description": "Verbatim text span this fact was extracted from"},
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
                    "text": {"type": "string", "description": "Verbatim text span this value was extracted from"},
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
                    "text": {"type": "string", "description": "Verbatim text span this value was extracted from"},
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
                    "text": {"type": "string", "description": "Verbatim text span this was found in"},
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

Workflow:
1. For each entity in the text: call find_entity first. If it already exists, call
   link_extraction with its id and the new text span instead of add_entity. Otherwise
   find its CLASS via search_schema — if no matching CLASS exists, skip this entity —
   then call add_entity.
2. For each relationship: resolve both entities (including their CLASSes) first, then
   find the RELATION type via search_schema. If no matching RELATION exists, skip this
   relationship. Otherwise call find_fact. If the fact already exists, call
   link_extraction with its id and the new text span instead of add_fact. Otherwise
   call add_fact.
3. For each attribute value: find the PROPERTY on its class/relation via search_schema.
   If no matching PROPERTY exists, skip this value. Otherwise call
   add_entity_property_value or add_fact_property_value.
4. Use names exactly as they appear in the text for entities.
5. When calling add_entity, add_fact, add_entity_property_value,
   add_fact_property_value, or link_extraction, always pass the verbatim sentence
   or phrase from the source text that the item was extracted from as the
   `text` argument. Reusing an existing entity/fact via link_extraction records
   the new text as an additional link without creating a duplicate.

Source of this text: {source}

Current schema:
{schema}

Extraction queries:
{queries}

Work through the text systematically and stop calling tools when you are done.\
"""


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
    ) -> None:
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
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(schema=schema_str, queries=queries_str, source=source_str)

        client = openai.AsyncOpenAI()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]
        turns = 0

        while True:
            if turns >= max_turns:
                break
            turns += 1
            response = await client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                tools=_TOOLS,  # type: ignore[arg-type]
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
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })

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
            extraction_id = await self._provenance.link_extraction(
                database,
                element_id=args["element_id"],
                text=args["text"],
                source_id=source_id,
            )
            return {"id": extraction_id}

        return {"error": f"unknown tool: {fn}"}
