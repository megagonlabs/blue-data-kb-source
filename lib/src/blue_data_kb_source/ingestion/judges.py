"""Single-shot LLM judges for the ingest path.

Each judge is one forced-function call — a single structured completion, not an
agentic loop: the ingest sequence is fixed, so there is nothing for a loop to
explore. Judges are stateless and read no graph; the caller (phase 1 of an
assert) gathers their inputs, so nothing is written when a judge fails.
"""

from __future__ import annotations

import json
import os
from typing import Any

from blue_data_kb_source.types import KBError

_MISSING_KEY_MESSAGE = (
    "schema_creation_mode='llm' needs a judge call, which requires OPENAI_API_KEY; "
    "set it, or pass schema_creation_mode='none'."
)


async def judge_property_placement(
    classes: list[dict],
    property_name: str,
    model: str = "gpt-4o",
) -> list[str]:
    """Pick which of an entity's classes (and their ancestors) declare a new property.

    Returns ids drawn from *classes* only; anything else the model names is dropped.
    """
    listing = "\n".join(
        f"- {c['id']}: {c['name']} — {c.get('description') or ''}" for c in classes
    )
    result = await _single_shot(
        model=model,
        system=(
            "You place a new property on the right class in a knowledge-base schema. "
            "Given an entity's classes and their ancestors, choose the class or classes "
            "that should declare the property: the most specific ones where it genuinely "
            "belongs, not everything offered."
        ),
        user=f"Property: {property_name}\n\nCandidate classes:\n{listing}",
        tool=_tool("return_placement", {
            "class_ids": {
                "type": "array", "items": {"type": "string"},
                "description": "ids of the candidate classes that should declare the property",
            },
        }, required=["class_ids"]),
    )
    offered = {c["id"] for c in classes}
    return [i for i in result.get("class_ids", []) if i in offered]


async def judge_class_name(
    operand_name: str,
    triple_text: str,
    existing_classes: list[dict],
    model: str = "gpt-4o",
) -> str:
    """Name the class for an entity: an existing class name to reuse, or a new one.

    The existing class list is load-bearing: without it the model produces `Person`
    for one row and `PERSON` for the next, and nothing merges them.
    """
    listing = "\n".join(
        f"- {c['name']} — {c.get('description') or ''}" for c in existing_classes
    ) or "(none)"
    result = await _single_shot(
        model=model,
        system=(
            "You name the class for an entity in a knowledge-base schema. Reuse an "
            "existing class when one means the same thing (e.g. PERSON for 'a person'); "
            "be conservative — reuse only on a clear meaning match. Otherwise coin a new "
            "name in the schema convention: uppercase with underscores."
        ),
        user=(
            f"Entity: {operand_name}\nAsserted in: {triple_text}\n\n"
            f"Existing classes:\n{listing}"
        ),
        tool=_tool("return_class", {
            "class_name": {
                "type": "string",
                "description": "an existing class name to reuse, or a new uppercase name",
            },
        }, required=["class_name"]),
    )
    return str(result.get("class_name", "")).strip()


async def judge_description(
    name: str,
    context: str,
    model: str = "gpt-4o",
) -> str:
    """Write a one-sentence description for a newly created schema element."""
    result = await _single_shot(
        model=model,
        system=(
            "You write one-sentence descriptions for new knowledge-base schema "
            "elements. Plain and factual, in the style of 'A human individual' "
            "or 'Employment relationship between a person and a company'."
        ),
        user=f"Element: {name}\nContext: {context}",
        tool=_tool("return_description", {
            "description": {"type": "string", "description": "one plain sentence"},
        }, required=["description"]),
    )
    return str(result.get("description", "")).strip()


# ------------------------------------------------------------------ #
# Internals                                                            #
# ------------------------------------------------------------------ #

def _tool(name: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Return the judged result via {name}.",
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


async def _single_shot(model: str, system: str, user: str, tool: dict) -> dict[str, Any]:
    if not os.environ.get("OPENAI_API_KEY"):
        raise KBError(_MISSING_KEY_MESSAGE)
    import openai

    client = openai.AsyncOpenAI()
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        tools=[tool],  # type: ignore[arg-type]
        tool_choice={"type": "function", "function": {"name": tool["function"]["name"]}},  # type: ignore[arg-type]
    )
    tc = response.choices[0].message.tool_calls[0]
    return json.loads(tc.function.arguments)
