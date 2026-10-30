"""The agents' LLM calls go through LiteLLM to the BLUE_DATA_LLM_PROVIDER provider (#164, #166)."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from blue_data_kb_source import llm

_MODEL_VARS = ("BLUE_DATA_KB_SOURCE_QUERY_LLM_MODEL", "BLUE_DATA_KB_SOURCE_LLM_MODEL", "BLUE_DATA_LLM_MODEL")


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
