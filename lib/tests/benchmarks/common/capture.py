"""Instrumentation used while running a benchmark case: capture what got created in
the KB, the raw tool-call trace, and OpenAI token usage/latency — all observed via
`import_text`'s callbacks/hooks since it returns nothing itself.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# Change-hook event "type" -> bucket name on CreationCollector.
_EVENT_BUCKETS = {
    "class_declared": "classes_declared",
    "relation_declared": "relations_declared",
    "class_property_declared": "class_properties_declared",
    "relation_property_declared": "relation_properties_declared",
    "entity_added": "entities",
    "fact_added": "facts",
    "entity_property_added": "entity_properties",
    "fact_property_added": "fact_properties",
}


class CreationCollector:
    """`add_change_hook` callback that buckets creation events by type.

    Change hooks fire only when something is genuinely created — idempotent
    reuse of an existing schema element does not re-fire — so this is the
    authoritative record of what one `import_text` call actually created.
    """

    def __init__(self) -> None:
        self.classes_declared: list[dict] = []
        self.relations_declared: list[dict] = []
        self.class_properties_declared: list[dict] = []
        self.relation_properties_declared: list[dict] = []
        self.entities: list[dict] = []
        self.facts: list[dict] = []
        self.entity_properties: list[dict] = []
        self.fact_properties: list[dict] = []
        self.unrecognized: list[dict] = []

    def __call__(self, event: dict) -> None:
        bucket = _EVENT_BUCKETS.get(event.get("type", ""))
        if bucket is None:
            self.unrecognized.append(event)
            return
        getattr(self, bucket).append(event)


@dataclass
class ToolCallRecord:
    name: str
    args: dict
    result: Any
    is_error: bool


def make_trace_callbacks() -> tuple[Callable[[str, dict], None], Callable[[str, dict, Any], None], list[ToolCallRecord]]:
    """Build `on_tool_call`/`on_tool_result` callbacks that append an ordered trace.

    Diagnostic only (call counts, whether search_schema preceded a declare_*,
    error results) — the trace does not replace CreationCollector for scoring.
    `on_tool_call` is a no-op: `on_tool_result` receives the same (name, args)
    plus the result on the very next line of the engine's dispatch loop, so one
    record per call is enough.
    """
    trace: list[ToolCallRecord] = []

    def on_tool_call(name: str, args: dict) -> None:
        pass

    def on_tool_result(name: str, args: dict, result: Any) -> None:
        is_error = isinstance(result, dict) and "error" in result
        trace.append(ToolCallRecord(name=name, args=args, result=result, is_error=is_error))

    return on_tool_call, on_tool_result, trace


def _tool_calls_to_dicts(tool_calls: Any) -> list[dict]:
    if not tool_calls:
        return []
    out = []
    for tc in tool_calls:
        raw_args = tc.function.arguments
        try:
            args: Any = json.loads(raw_args)
        except (TypeError, ValueError):
            args = raw_args
        out.append({"id": tc.id, "name": tc.function.name, "arguments": args})
    return out


@dataclass
class LLMCallRecord:
    """Full prompt/response for one `chat.completions.create` call.

    `messages`/`tools` are the exact request payload (system/user/tool
    messages the model saw); `response_content`/`response_tool_calls` are
    what it produced. Kept in full (not just token counts) so the detailed
    log can show a benchmark run's entire prompt history for debugging.
    """

    model: str
    latency_seconds: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    messages: list[dict] = field(default_factory=list)
    tools: list[dict] | None = None
    response_content: str | None = None
    response_tool_calls: list[dict] = field(default_factory=list)


class InstrumentedOpenAI:
    """Drop-in replacement for `openai.AsyncOpenAI` that records usage/latency.

    Delegates to a real client under the hood so responses are genuine whether
    the underlying HTTP call is live or replayed from a VCR cassette; only
    `chat.completions.create` is wrapped. Construct via
    `functools.partial(InstrumentedOpenAI, real_client_cls=openai.AsyncOpenAI, sink=calls)`
    *before* patching, then patch `openai.AsyncOpenAI` with the partial — the
    same patch site the existing unit tests use. `real_client_cls` must be
    captured before the patch is applied: looking up `openai.AsyncOpenAI`
    from inside `__init__` would resolve to this very wrapper once patched,
    recursing forever.
    """

    def __init__(self, *args: Any, real_client_cls: type, sink: list[LLMCallRecord], **kwargs: Any) -> None:
        self._client = real_client_cls(*args, **kwargs)
        self._sink = sink
        self.chat = _InstrumentedChat(self._client.chat, sink)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class _InstrumentedChat:
    def __init__(self, chat: Any, sink: list[LLMCallRecord]) -> None:
        self._chat = chat
        self._sink = sink
        self.completions = _InstrumentedCompletions(chat.completions, sink)


class _InstrumentedCompletions:
    def __init__(self, completions: Any, sink: list[LLMCallRecord]) -> None:
        self._completions = completions
        self._sink = sink

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        response = await self._completions.create(*args, **kwargs)
        elapsed = time.monotonic() - start
        usage = getattr(response, "usage", None)
        msg = response.choices[0].message
        self._sink.append(LLMCallRecord(
            model=kwargs.get("model", ""),
            latency_seconds=elapsed,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
            messages=list(kwargs.get("messages") or []),
            tools=list(kwargs.get("tools")) if kwargs.get("tools") is not None else None,
            response_content=msg.content,
            response_tool_calls=_tool_calls_to_dicts(msg.tool_calls),
        ))
        return response
