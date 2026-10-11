"""Tool-call plumbing shared by the agent loops: query (answer/validate/enrich),
extraction and schema expansion (#130).

A tool call the model gets wrong — arguments that aren't valid JSON, a missing
required argument, a tool that raises — goes back to the model as an error
result it can correct, rather than aborting the whole run. And every result is
capped before it enters the conversation, so one broad call can't flood the
model's context.
"""
from __future__ import annotations

import json
from typing import Any

# About 5k tokens. Results bigger than this are almost always a query that should
# have been narrower; the model is told so and shown the beginning.
MAX_TOOL_RESULT_CHARS = 20_000


def parse_tool_arguments(raw: str | None) -> tuple[dict[str, Any], str | None]:
    """(arguments, None), or ({}, error message) when *raw* isn't a JSON object."""
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return {}, (f"the arguments are not valid JSON ({exc}); "
                    f"call the tool again with a complete JSON object")
    if not isinstance(args, dict):
        return {}, "the arguments must be a JSON object"
    return args, None


def tool_error(exc: Exception) -> dict[str, str]:
    """The tool result for a tool that raised."""
    if isinstance(exc, KeyError) and exc.args:
        return {"error": f"missing required argument {exc.args[0]!r}"}
    return {"error": str(exc) or type(exc).__name__}


def tool_message_content(result: Any) -> str:
    """*result* as the content of a tool message, capped at MAX_TOOL_RESULT_CHARS."""
    return cap_tool_text(result if isinstance(result, str) else json.dumps(result, default=str))


def cap_tool_text(text: str) -> str:
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    return json.dumps({
        "truncated": True,
        "note": (f"The result was {len(text)} characters; only the first {MAX_TOOL_RESULT_CHARS} "
                 f"are shown. Narrow the request (a more specific query, a smaller depth) to see the rest."),
        "partial_result": text[:MAX_TOOL_RESULT_CHARS],
    })
