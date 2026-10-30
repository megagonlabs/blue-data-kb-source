import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from blue_data_kb_source.ingestion.judges import (
    judge_class_name,
    judge_description,
    judge_property_placement,
)
from blue_data_kb_source.types import KBError


def _tool_response(arguments: dict):
    tc = MagicMock()
    tc.function.arguments = json.dumps(arguments)
    msg = MagicMock()
    msg.tool_calls = [tc]
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _mock_openai(response):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    return MagicMock(return_value=client), client


def _prompt(client) -> str:
    return client.chat.completions.create.call_args.kwargs["messages"][1]["content"]


_CLASSES = [
    {"id": "c-emp", "name": "EMPLOYEE", "description": "A person employed by an organization"},
    {"id": "c-per", "name": "PERSON", "description": "A human individual"},
]


# ------------------------------------------------------------------ #
# Judge 1 — property placement                                         #
# ------------------------------------------------------------------ #

async def test_placement_returns_the_chosen_ids(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    factory, _ = _mock_openai(_tool_response({"class_ids": ["c-emp"]}))
    with patch("blue_data_kb_source.llm.AsyncClient", factory):
        assert await judge_property_placement(_CLASSES, "SALARY") == ["c-emp"]


async def test_placement_prompt_offers_every_candidate(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    factory, client = _mock_openai(_tool_response({"class_ids": ["c-emp"]}))
    with patch("blue_data_kb_source.llm.AsyncClient", factory):
        await judge_property_placement(_CLASSES, "SALARY")
    prompt = _prompt(client)
    for token in ("SALARY", "c-emp", "EMPLOYEE", "c-per", "PERSON", "A human individual"):
        assert token in prompt


async def test_placement_keeps_every_chosen_id_in_order(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    factory, _ = _mock_openai(_tool_response({"class_ids": ["c-emp", "c-per"]}))
    with patch("blue_data_kb_source.llm.AsyncClient", factory):
        # Decision 15 says class(es): multi-placement must not degrade to one.
        assert await judge_property_placement(_CLASSES, "NAME") == ["c-emp", "c-per"]


async def test_placement_drops_ids_that_were_not_offered(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    factory, _ = _mock_openai(_tool_response({"class_ids": ["c-emp", "c-invented"]}))
    with patch("blue_data_kb_source.llm.AsyncClient", factory):
        assert await judge_property_placement(_CLASSES, "SALARY") == ["c-emp"]


# ------------------------------------------------------------------ #
# Judge 2 — class naming                                               #
# ------------------------------------------------------------------ #

async def test_class_name_returns_the_choice_stripped(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    factory, _ = _mock_openai(_tool_response({"class_name": " PERSON "}))
    with patch("blue_data_kb_source.llm.AsyncClient", factory):
        name = await judge_class_name("a person", "alice WORKED_AT ibm", _CLASSES)
    # Padded model output must come back clean, or exact resolution downstream
    # would miss and mint a duplicate class.
    assert name == "PERSON"


async def test_class_name_prompt_lists_every_existing_class(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    factory, client = _mock_openai(_tool_response({"class_name": "PERSON"}))
    with patch("blue_data_kb_source.llm.AsyncClient", factory):
        await judge_class_name("a person", "alice WORKED_AT ibm", _CLASSES)
    prompt = _prompt(client)
    for token in ("EMPLOYEE", "PERSON", "a person", "alice WORKED_AT ibm"):
        assert token in prompt


async def test_class_name_prompt_says_none_when_schema_is_empty(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    factory, client = _mock_openai(_tool_response({"class_name": "SPACESHIP"}))
    with patch("blue_data_kb_source.llm.AsyncClient", factory):
        name = await judge_class_name("a spaceship", "x DOCKED_AT y", [])
    assert name == "SPACESHIP"
    assert "(none)" in _prompt(client)


# ------------------------------------------------------------------ #
# Judge 3 — description                                                #
# ------------------------------------------------------------------ #

async def test_description_returns_the_sentence_and_sees_context(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    factory, client = _mock_openai(
        _tool_response({"description": "A business organization."})
    )
    with patch("blue_data_kb_source.llm.AsyncClient", factory):
        desc = await judge_description("COMPANY", "asserted in: alice WORKED_AT ibm")
    assert desc == "A business organization."
    prompt = _prompt(client)
    assert "COMPANY" in prompt
    assert "alice WORKED_AT ibm" in prompt


# ------------------------------------------------------------------ #
# Shared plumbing                                                      #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("provider, key", [("", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")])
async def test_every_judge_requires_the_providers_api_key(monkeypatch, provider, key):
    monkeypatch.setenv("BLUE_DATA_LLM_PROVIDER", provider)
    monkeypatch.delenv(key, raising=False)
    for coro in (
        judge_property_placement(_CLASSES, "SALARY"),
        judge_class_name("a person", "t", _CLASSES),
        judge_description("COMPANY", "ctx"),
    ):
        with pytest.raises(KBError, match=key):
            await coro


async def test_model_and_forced_tool_reach_the_api(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    factory, client = _mock_openai(_tool_response({"class_name": "PERSON"}))
    with patch("blue_data_kb_source.llm.AsyncClient", factory):
        await judge_class_name("a person", "t", _CLASSES, model="gpt-4o-mini")
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["tool_choice"] == {"type": "function", "function": {"name": "return_class"}}
