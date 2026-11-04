"""The agents' LLM calls go through LiteLLM to the BLUE_DATA_LLM_PROVIDER provider (#164, #166)."""
import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest
from blue_data_kb_source import MemoryKBSource, llm

_FUNCTIONS = ("QUERY", "VALIDATION", "ENRICHMENT", "SCHEMA_EXPANSION", "EXTRACTION", "ASSERTION", "INGESTION")
_MODEL_VARS = (
    *(f"BLUE_DATA_KB_SOURCE_{f}_LLM_MODEL" for f in _FUNCTIONS),
    "BLUE_DATA_KB_SOURCE_LLM_MODEL", "BLUE_DATA_LLM_MODEL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (llm.PROVIDER_ENV, *_MODEL_VARS, "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("value, expected", [
    ("", "openai"), ("openai", "openai"), ("OpenAI", "openai"), (" anthropic ", "anthropic"),
])
def test_provider_is_the_family_setting_and_blank_means_openai(monkeypatch, value, expected):
    monkeypatch.setenv(llm.PROVIDER_ENV, value)
    assert llm.provider() == expected


@pytest.mark.parametrize("value, model", [("", "gpt-4o"), ("openai", "gpt-4o"), ("anthropic", "claude-opus-4-8")])
def test_with_no_model_configured_the_provider_default_is_used(monkeypatch, value, model):
    monkeypatch.setenv(llm.PROVIDER_ENV, value)
    assert llm.configured_model("QUERY") == model


def test_the_family_model_is_used_for_the_family_provider(monkeypatch):
    """The XR-T-04 setup: a Claude family model is now a model of the provider kb calls."""
    monkeypatch.setenv(llm.PROVIDER_ENV, "anthropic")
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "claude-opus-4-8")
    assert llm.configured_model("QUERY") == "claude-opus-4-8"


def test_a_kb_model_overrides_the_family_one_and_a_function_model_overrides_both(monkeypatch):
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gpt-4o")
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_LLM_MODEL", "gpt-4.1-mini")
    assert llm.configured_model("QUERY") == "gpt-4.1-mini"
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_QUERY_LLM_MODEL", "gpt-4.1")
    assert llm.configured_model("QUERY") == "gpt-4.1"
    assert llm.configured_model("EXTRACTION") == "gpt-4.1-mini"


def test_a_provider_without_a_known_default_needs_a_model(monkeypatch):
    monkeypatch.setenv(llm.PROVIDER_ENV, "gemini")
    with pytest.raises(Exception, match="no default model for BLUE_DATA_LLM_PROVIDER='gemini'"):
        llm.configured_model("QUERY")
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gemini-2.5-pro")
    assert llm.configured_model("QUERY") == "gemini-2.5-pro"


def _create(**kwargs):
    return asyncio.run(llm.AsyncClient().chat.completions.create(**kwargs))


@pytest.mark.parametrize("value, provider, key, model", [
    ("", "openai", "OPENAI_API_KEY", "gpt-4o"),
    ("anthropic", "anthropic", "ANTHROPIC_API_KEY", "claude-opus-4-8"),
])
def test_every_call_is_pinned_to_the_deployment_provider(monkeypatch, value, provider, key, model):
    monkeypatch.setenv(llm.PROVIDER_ENV, value)
    monkeypatch.setenv(key, "sk-test")
    with patch("litellm.acompletion", AsyncMock(return_value="response")) as acompletion:
        assert _create(model=None, messages=[{"role": "user", "content": "hi"}], tool_choice="auto") == "response"
    acompletion.assert_awaited_once_with(
        model=model, custom_llm_provider=provider,
        messages=[{"role": "user", "content": "hi"}], tool_choice="auto",
    )


@pytest.mark.parametrize("value, key", [("", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")])
def test_a_missing_provider_key_is_named_before_any_call(monkeypatch, value, key):
    monkeypatch.setenv(llm.PROVIDER_ENV, value)
    with patch("litellm.acompletion", AsyncMock()) as acompletion:
        with pytest.raises(llm.MissingKeyError) as info:
            _create(model="m", messages=[])
    assert info.value.env == key
    acompletion.assert_not_awaited()


def test_the_other_providers_key_is_not_required(monkeypatch):
    monkeypatch.setenv(llm.PROVIDER_ENV, "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert llm.missing_key() is None


def test_litellm_logs_nothing_below_warning_once_a_call_is_made(monkeypatch):
    """Its handler sends those records to stdout, which MCP's stdio transport owns (#173)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    logger = logging.getLogger("LiteLLM")
    monkeypatch.setattr(logger, "level", logging.NOTSET)
    with patch("litellm.acompletion", AsyncMock(return_value="response")):
        _create(model="m", messages=[])
    assert logger.level == logging.WARNING


def test_the_environment_alone_names_no_model_when_nothing_is_set(monkeypatch):
    monkeypatch.setenv(llm.PROVIDER_ENV, "gemini")
    assert llm.model_from_env("QUERY") is None


_ENTRY_POINTS = [
    ("answer", "QUERY", lambda kb: kb.answer("d", "who?")),
    ("validate", "VALIDATION", lambda kb: kb.validate("d", "x")),
    ("enrich", "ENRICHMENT", lambda kb: kb.enrich("d", "x")),
    ("expand_schema", "SCHEMA_EXPANSION", lambda kb: kb.expand_schema("d", "text")),
    ("import_text", "EXTRACTION", lambda kb: kb.import_text("d", "text", expand_schema=False)),
    ("assert_class", "ASSERTION", lambda kb: kb.assert_class("d", "PERSON")),
    ("assert_entity", "ASSERTION", lambda kb: kb.assert_entity("d", "Alice", "PERSON")),
    ("ingest", "INGESTION", lambda kb: kb.ingest("d", {})),
]


def _sent_model(kb):
    """The model the engine or manager mock received, positional or by keyword."""
    for manager in (kb._knowledge, kb._schema, kb._extraction, kb._ingestion):
        for call in manager.mock_calls:
            if "model" in call.kwargs:
                return call.kwargs["model"]
            sent = [a for a in call.args if isinstance(a, str) and a.endswith("-model")]
            if sent:
                return sent[0]
    return None


def _kb_with_mocked_managers():
    kb = MemoryKBSource("t")
    kb._knowledge, kb._schema, kb._extraction, kb._ingestion = (
        AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(unsafe=True),  # unsafe: assert_* methods
    )
    return kb


@pytest.mark.parametrize("name, function, call", _ENTRY_POINTS, ids=[e[0] for e in _ENTRY_POINTS])
def test_a_library_call_without_a_model_uses_the_configured_chain(monkeypatch, name, function, call):
    """#176: not only execute_query_nl, the CLI and the MCP server."""
    kb = _kb_with_mocked_managers()
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "family-model")
    asyncio.run(call(kb))
    assert _sent_model(kb) == "family-model"

    kb = _kb_with_mocked_managers()
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_LLM_MODEL", "kb-model")
    asyncio.run(call(kb))
    assert _sent_model(kb) == "kb-model"

    kb = _kb_with_mocked_managers()
    monkeypatch.setenv(f"BLUE_DATA_KB_SOURCE_{function}_LLM_MODEL", "function-model")
    asyncio.run(call(kb))
    assert _sent_model(kb) == "function-model"


def test_a_model_argument_beats_the_configured_chain(monkeypatch):
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_QUERY_LLM_MODEL", "function-model")
    kb = _kb_with_mocked_managers()
    asyncio.run(kb.answer("d", "who?", model="argument-model"))
    assert _sent_model(kb) == "argument-model"


def test_import_text_runs_expansion_and_extraction_on_one_model(monkeypatch):
    """As the CLI and the MCP server do: expansion here is part of the extraction call."""
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_EXTRACTION_LLM_MODEL", "extraction-model")
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_LLM_MODEL", "expansion-model")
    kb = _kb_with_mocked_managers()
    asyncio.run(kb.import_text("d", "text"))
    assert kb._schema.expand_schema.call_args.kwargs["model"] == "extraction-model"
    assert kb._extraction.import_text.call_args.args[3] == "extraction-model"


def test_an_assert_needing_no_judge_works_without_a_known_default(monkeypatch):
    """Resolving the model must not fail a call that never reaches an LLM."""
    monkeypatch.setenv(llm.PROVIDER_ENV, "gemini")
    kb = MemoryKBSource("t")
    kb.create_database("d")
    result = asyncio.run(kb.assert_class("d", "PERSON", "A person", schema_creation_mode="none"))
    assert result.node_id
