"""A malformed tool call or a failing tool goes back to the model as an error, and
tool results are bounded — in every agent loop (#130)."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from blue_data_kb_source import MemoryKBSource
from blue_data_kb_source._agent_tools import MAX_TOOL_RESULT_CHARS, tool_message_content


class ScriptedModel:
    """Replays a fixed script of assistant turns and records what it was sent.

    A step is a string (a plain reply) or a list of (tool name, arguments) calls,
    where arguments is a dict or a raw string sent as-is.
    """

    def __init__(self, *script):
        self.script = list(script)
        self.requests: list[list[dict]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.requests.append(list(kwargs["messages"]))
        step = self.script.pop(0) if self.script else "done"
        if isinstance(step, str):
            msg = SimpleNamespace(role="assistant", content=step, tool_calls=None)
        else:
            msg = SimpleNamespace(role="assistant", content=None, tool_calls=[
                SimpleNamespace(id=f"c{i}", type="function", function=SimpleNamespace(
                    name=name, arguments=args if isinstance(args, str) else json.dumps(args)))
                for i, (name, args) in enumerate(step)])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    def tool_replies(self) -> list[str]:
        """Every tool message the model was shown, in order."""
        seen = self.requests[-1] if self.requests else []
        return [m["content"] for m in seen if m.get("role") == "tool"]


@pytest.fixture
async def kb():
    kb = MemoryKBSource(name="kb")
    kb.create_database("main")
    await kb.declare_class("main", "PERSON", "A person")
    return kb


def _use(model):
    return patch("openai.AsyncOpenAI", return_value=model)


async def test_answer_survives_malformed_arguments(kb):
    model = ScriptedModel([("search_entities", '{"query": "Car')], [("return_result", {"result": []})])
    with _use(model):
        assert await kb.answer("main", "Who is Carol?") == []
    assert "not valid JSON" in model.tool_replies()[0]


async def test_answer_survives_a_failing_tool(kb):
    model = ScriptedModel([("get_entity_facts", {})], [("return_result", {"result": []})])
    with _use(model):
        assert await kb.answer("main", "Who is Carol?") == []
    assert json.loads(model.tool_replies()[0]) == {"error": "missing required argument 'entity_id'"}


async def test_answer_does_not_accept_a_malformed_return_result(kb):
    model = ScriptedModel([("return_result", '{"result": [')], [("return_result", {"result": [{"a": 1}]})])
    with _use(model):
        assert await kb.answer("main", "anything") == [{"a": 1}]


async def test_import_text_survives_malformed_arguments(kb):
    model = ScriptedModel([("add_entity", '{"name": "Carol", ')], "done")
    with _use(model):
        await kb.import_text("main", "Carol is a person.", expand_schema=False, related_data_k=0)
    assert "not valid JSON" in model.requests[1][-1]["content"]


async def test_expand_schema_survives_malformed_arguments(kb):
    model = ScriptedModel([("declare_class", '{"name": "CAR')], "done")
    with _use(model):
        await kb.expand_schema("main", "Carol drives a car.", dedup_mode="none")
    assert "not valid JSON" in model.requests[1][-1]["content"]


def test_oversized_tool_result_is_truncated_with_a_note():
    content = json.loads(tool_message_content({"rows": ["x" * 100] * 1000}))
    assert content["truncated"] is True
    assert len(content["partial_result"]) == MAX_TOOL_RESULT_CHARS
    assert "Narrow the request" in content["note"]
    assert tool_message_content({"rows": []}) == '{"rows": []}'


async def test_graph_walk_tools_are_bounded(kb):
    person = kb.match_nodes("main", label="CLASS")[0]["id"]
    knows = await kb.declare_relation_type("main", "KNOWS", "Acquaintance", domain="PERSON", range="PERSON")
    hub = await kb.add_entity("main", "Hub", class_id=person)
    for i in range(150):
        other = await kb.add_entity("main", f"P{i}", class_id=person)
        await kb.add_fact("main", head_entity_id=hub, tail_entity_id=other, relation_id=knows)

    results = {}
    model = ScriptedModel(
        [("get_neighbors", {"entity_id": hub, "depth": 50}), ("get_entity_facts", {"entity_id": hub})],
        [("return_result", {"result": []})],
    )
    with _use(model):
        await kb.answer("main", "Who does Hub know?", on_tool_result=lambda n, a, r: results.setdefault(n, r))
    assert results["get_neighbors"]["truncated"] and len(results["get_neighbors"]["neighbors"]) == 100
    assert results["get_entity_facts"]["truncated"] and len(results["get_entity_facts"]["facts"]) == 100


# ------------------------------------------------------------------ #
# validate cites only facts that exist (#129)                          #
# ------------------------------------------------------------------ #

async def _alice_at_ibm(kb):
    person = kb.match_nodes("main", label="CLASS")[0]["id"]
    company = await kb.declare_class("main", "COMPANY", "A company")
    rel = await kb.declare_relation_type("main", "WORKED_AT", "Employment", domain="PERSON", range="COMPANY")
    alice = await kb.add_entity("main", "Alice", class_id=person)
    ibm = await kb.add_entity("main", "IBM", class_id=company)
    return await kb.add_fact("main", name="Alice at IBM", head_entity_id=alice, tail_entity_id=ibm, relation_id=rel)


async def test_validate_flags_a_cited_fact_the_kb_does_not_hold(kb):
    model = ScriptedModel([("return_result", {"result": {"supporting": [
        {"fact": "Alice WORKED_AT IBM", "head": "Alice", "relation": "WORKED_AT", "tail": "IBM"}],
        "counter": []}})])
    with _use(model):
        result = await kb.validate("main", "Alice worked at IBM")
    assert result.supporting == [] and result.counter == []
    assert result.unverified == [{"fact": "Alice WORKED_AT IBM", "head": "Alice", "relation": "WORKED_AT",
                                  "tail": "IBM", "cited_as": "supporting"}]


async def test_validate_resolves_a_cited_fact_by_id_and_reports_it_as_stored(kb):
    fact_id = await _alice_at_ibm(kb)
    model = ScriptedModel([("return_result", {"result": {"supporting": [
        {"fact_id": fact_id, "head": "Alice Smith", "relation": "EMPLOYED_BY", "tail": "IBM Corp"}],
        "counter": []}})])
    with _use(model):
        result = await kb.validate("main", "Alice worked at IBM")
    assert result.supporting == [{"fact_id": fact_id, "fact": "Alice at IBM", "head": "Alice",
                                  "relation": "WORKED_AT", "tail": "IBM"}]
    assert result.unverified == []


async def test_validate_resolves_a_cited_fact_by_its_names(kb):
    fact_id = await _alice_at_ibm(kb)
    model = ScriptedModel([("return_result", {"result": {"supporting": [], "counter": [
        {"head": "Alice", "relation": "worked at", "tail": "IBM"}]}})])
    with _use(model):
        result = await kb.validate("main", "Alice never worked at IBM")
    assert [f["fact_id"] for f in result.counter] == [fact_id]
