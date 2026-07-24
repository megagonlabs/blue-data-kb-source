from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import CLASS, ENTITY, RELATION
from blue_data_kb_source.types import ExtractionError

if TYPE_CHECKING:
    from blue_data_kb_source.knowledge.manager import KnowledgeManager
    from blue_data_kb_source.schema.manager import SchemaManager

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
            "description": "Declare a RELATION type in the schema (idempotent). Returns {id}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Relation name e.g. WORKED_AT"},
                    "description": {"type": "string"},
                    "is_symmetric": {"type": "boolean"},
                },
                "required": ["name", "description"],
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

Workflow:
1. For each entity in the text: find or declare its CLASS, then call add_entity.
2. For each relationship: find or declare the RELATION type, then call add_fact.
3. For each attribute value: find or declare the PROPERTY on its class/relation,
   then call add_entity_property_value or add_fact_property_value.
4. Prefer existing schema elements; only declare new ones when nothing fits.
5. Use names exactly as they appear in the text for entities.
   Use UPPER_SNAKE_CASE for CLASS, RELATION, and PROPERTY names.
6. When calling add_entity, add_fact, add_entity_property_value, or
   add_fact_property_value, always pass the verbatim sentence or phrase from
   the source text that the item was extracted from as the `text` argument.

Current schema:
{schema}

Extraction queries:
{queries}

Work through the text systematically and stop calling tools when you are done.\
"""


class ExtractionEngine:
    """LLM-powered extraction engine that populates the KB from raw text."""

    def __init__(
        self,
        source: GraphSource,
        schema_manager: SchemaManager,
        knowledge_manager: KnowledgeManager,
    ) -> None:
        self._source = source
        self._schema = schema_manager
        self._knowledge = knowledge_manager

    async def import_text(
        self,
        database: str,
        text: str,
        queries: list[str] | None = None,
        model: str = "gpt-4o",
        source_id: str | None = None,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None = None,
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
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(schema=schema_str, queries=queries_str)

        client = openai.AsyncOpenAI()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]

        while True:
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
                    result = await self._dispatch_tool(
                        database, tc.function.name, args, source_id
                    )
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

        if fn == "find_class":
            nodes = self._source.match_nodes(database, label=CLASS, properties={"name": args["name"]})
            if nodes:
                p = nodes[0].get("properties", {})
                return {"id": nodes[0]["id"], "name": p.get("name"), "description": p.get("description")}
            return {"id": None}

        if fn == "find_relation_type":
            nodes = self._source.match_nodes(database, label=RELATION, properties={"name": args["name"]})
            if nodes:
                p = nodes[0].get("properties", {})
                return {"id": nodes[0]["id"], "name": p.get("name"), "description": p.get("description")}
            return {"id": None}

        if fn == "find_entity":
            nodes = self._source.match_nodes(database, label=ENTITY, properties={"name": args["name"]})
            if nodes:
                p = nodes[0].get("properties", {})
                return {"id": nodes[0]["id"], "name": p.get("name"), "description": p.get("description")}
            return {"id": None}

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

        return {"error": f"unknown tool: {fn}"}
