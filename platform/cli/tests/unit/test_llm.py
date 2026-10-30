import json
from unittest.mock import AsyncMock, MagicMock, patch

from blue_data_kb_cli.commands import cli

from .conftest import invoke

DB = ["--database", "main"]


def _tool_call_response(tool_name, arguments):
    tc = MagicMock()
    tc.id = "call_0"
    tc.type = "function"
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(arguments)

    msg = MagicMock()
    msg.role = "assistant"
    msg.content = None
    msg.tool_calls = [tc]

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = msg
    return response


def _text_response(text):
    msg = MagicMock()
    msg.role = "assistant"
    msg.content = text
    msg.tool_calls = None

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = msg
    return response


def _mock_openai_return_result(payload):
    """Patch llm.AsyncClient so the agent immediately calls return_result."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=_tool_call_response("return_result", {"result": payload})
    )
    return MagicMock(return_value=mock_client)


def _mock_openai_no_tool_calls():
    """Patch llm.AsyncClient so the agent stops on the first turn (extraction loop)."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=_text_response("Nothing further to extract.")
    )
    return MagicMock(return_value=mock_client)


def _seed(runner):
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "A person employed by an organization"])
    invoke(runner, DB + ["schema", "class", "add", "COMPANY", "A business organization"])
    invoke(runner, DB + [
        "schema", "relation", "add", "WORKED_AT", "Employment relationship",
        "--domain", "EMPLOYEE", "--range", "COMPANY",
    ])
    invoke(runner, DB + ["data", "entity", "add", "--name", "Alice", "--class", "EMPLOYEE"])
    invoke(runner, DB + ["data", "entity", "add", "--name", "IBM", "--class", "COMPANY"])
    invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
        "--text", "Alice joined IBM in June 1998.",
    ])


def test_extract_reports_completion(runner):
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "A person employed by an organization"])
    with patch("blue_data_kb_source.llm.AsyncClient", _mock_openai_no_tool_calls()):
        result = runner.invoke(cli, DB + ["data", "extract", "Alice joined IBM in June 1998."])
    assert result.exit_code == 0, result.output
    assert "Extraction complete." in result.output


def _schema_expansion_prompts(factory):
    """System prompts belonging to the schema-expansion agent, across every call
    made on the mocked client."""
    return [
        call.kwargs["messages"][0]["content"]
        for call in factory.return_value.chat.completions.create.call_args_list
        if "schema expansion agent" in call.kwargs["messages"][0]["content"]
    ]


def test_extract_no_expand_schema_skips_expansion(runner):
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "A person employed by an organization"])
    factory = _mock_openai_no_tool_calls()
    with patch("blue_data_kb_source.llm.AsyncClient", factory):
        result = runner.invoke(cli, DB + [
            "data", "extract", "Alice joined IBM in June 1998.",
            "--no-expand-schema",
        ])
    assert result.exit_code == 0, result.output
    # Assert on *which* agent ran, not how many turns it took: extraction is a
    # multi-stage pipeline whose stage count is free to change, so a call-count
    # assertion here only records today's number.
    assert not _schema_expansion_prompts(factory)
    assert factory.return_value.chat.completions.create.call_count >= 1


def test_extract_expands_schema_by_default(runner):
    """The other half of the pair: without --no-expand-schema, the expansion
    agent does run — otherwise the test above would pass for the wrong reason."""
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "A person employed by an organization"])
    factory = _mock_openai_no_tool_calls()
    with patch("blue_data_kb_source.llm.AsyncClient", factory):
        result = runner.invoke(cli, DB + ["data", "extract", "Alice joined IBM in June 1998."])
    assert result.exit_code == 0, result.output
    assert _schema_expansion_prompts(factory)


def test_schema_expand_dedup_mode_none_omits_search_schema_tool(runner):
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "A person employed by an organization"])
    factory = _mock_openai_no_tool_calls()
    with patch("blue_data_kb_source.llm.AsyncClient", factory):
        result = runner.invoke(cli, DB + [
            "schema", "expand", "Alice joined IBM in June 1998.",
            "--dedup-mode", "none",
        ])
    assert result.exit_code == 0, result.output
    call_kwargs = factory.return_value.chat.completions.create.call_args.kwargs
    tool_names = {t["function"]["name"] for t in call_kwargs["tools"]}
    assert "search_schema" not in tool_names
    assert "declare_class" in tool_names


def _mock_openai_stalled():
    """Patch llm.AsyncClient so every model call hangs, like a stalled request."""
    import asyncio

    async def _hang(*args, **kwargs):
        await asyncio.sleep(3600)

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=_hang)
    return MagicMock(return_value=mock_client)


def test_schema_expand_timeout_fails_with_a_clear_error(runner):
    with patch("blue_data_kb_source.llm.AsyncClient", _mock_openai_stalled()):
        result = runner.invoke(cli, DB + [
            "schema", "expand", "Alice joined IBM.", "--model", "gpt-5", "--timeout", "0.2",
        ])
    assert result.exit_code != 0
    assert "timed out after 0.2s" in result.output
    assert "'gpt-5'" in result.output


def test_schema_expand_timeout_defaults_to_the_profile_value(runner):
    with patch("blue_data_kb_source.KBSource.expand_schema", new=AsyncMock()) as expand:
        invoke(runner, DB + ["schema", "expand", "Alice joined IBM."])
        assert expand.call_args.kwargs["timeout"] == 1800
        invoke(runner, DB + ["schema", "expand", "Alice joined IBM.", "--timeout", "0"])
        assert expand.call_args.kwargs["timeout"] is None


def test_extract_caps_schema_expansion_with_the_profile_timeout(runner):
    with patch("blue_data_kb_source.KBSource.import_text", new=AsyncMock()) as imp:
        invoke(runner, DB + ["data", "extract", "Alice joined IBM."])
        assert imp.call_args.kwargs["schema_expansion_timeout"] == 1800
        invoke(runner, DB + ["data", "extract", "Alice joined IBM.", "--schema-expansion-timeout", "0"])
        assert imp.call_args.kwargs["schema_expansion_timeout"] is None


def test_answer_table_format(runner):
    _seed(runner)
    payload = [{"entity": "Alice", "relation": "WORKED_AT", "value": None, "provenance": "Alice joined IBM in June 1998."}]
    with patch("blue_data_kb_source.llm.AsyncClient", _mock_openai_return_result(payload)):
        result = runner.invoke(cli, DB + ["data", "answer", "Where did Alice work?"])
    assert result.exit_code == 0, result.output
    assert "Alice" in result.output
    assert "WORKED_AT" in result.output


def test_answer_json_format(runner):
    _seed(runner)
    payload = [{"entity": "Alice", "relation": "WORKED_AT"}]
    with patch("blue_data_kb_source.llm.AsyncClient", _mock_openai_return_result(payload)):
        result = runner.invoke(cli, DB + ["data", "answer", "Where did Alice work?", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload


def test_answer_natural_language_format(runner):
    _seed(runner)
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[
            _tool_call_response("return_result", {"result": [{"entity": "Alice"}]}),
            _text_response("Alice worked at IBM."),
        ]
    )
    with patch("blue_data_kb_source.llm.AsyncClient", MagicMock(return_value=mock_client)):
        result = runner.invoke(cli, DB + ["data", "answer", "Where did Alice work?", "--format", "nl"])
    assert result.exit_code == 0, result.output
    assert "Alice worked at IBM." in result.output


def test_validate_structured_format(runner):
    _seed(runner)
    payload = {
        "supporting": [{"fact": "Alice_WORKED_AT_IBM", "head": "Alice", "tail": "IBM", "relation": "WORKED_AT"}],
        "counter": [],
    }
    with patch("blue_data_kb_source.llm.AsyncClient", _mock_openai_return_result(payload)):
        result = runner.invoke(cli, DB + ["data", "validate", "Alice worked at IBM."])
    assert result.exit_code == 0, result.output
    assert "Supporting" in result.output
    assert "Alice" in result.output and "IBM" in result.output
    assert "Counter" in result.output
    assert "(none)" in result.output


def test_validate_prints_property_value_evidence(runner):
    """#149: property values are evidence too, printed as owner · PROPERTY = value."""
    _seed(runner)
    invoke(runner, DB + ["schema", "class", "property", "add", "TITLE", "Job title", "--class", "EMPLOYEE"])
    invoke(runner, DB + ["data", "entity", "property", "set", "Software Engineer", "--entity", "Alice", "--property", "TITLE"])
    invoke(runner, DB + ["data", "entity", "property", "set", "Staff Software Engineer", "--entity", "Alice", "--property", "TITLE"])
    payload = {
        "supporting": [{"owner": "Alice", "property": "TITLE", "value": "Software Engineer"}],
        "counter": [{"owner": "Alice", "property": "TITLE", "value": "Staff Software Engineer"}],
    }
    with patch("blue_data_kb_source.llm.AsyncClient", _mock_openai_return_result(payload)):
        result = runner.invoke(cli, DB + ["data", "validate", "Alice's title is Software Engineer."])
    assert result.exit_code == 0, result.output
    supporting, counter = result.output.split("Counter:")
    assert "Alice · TITLE = Software Engineer" in supporting
    assert "Alice · TITLE = Staff Software Engineer" in counter
    assert "Unverified" not in result.output


def test_validate_natural_language_format(runner):
    _seed(runner)
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[
            _tool_call_response("return_result", {"result": {"supporting": [], "counter": []}}),
            _text_response("This statement is supported."),
        ]
    )
    with patch("blue_data_kb_source.llm.AsyncClient", MagicMock(return_value=mock_client)):
        result = runner.invoke(cli, DB + ["data", "validate", "Alice worked at IBM.", "--natural-language"])
    assert result.exit_code == 0, result.output
    assert "This statement is supported." in result.output


# ------------------------------------------------------------------ #
# provider (#164, #166)                                               #
# ------------------------------------------------------------------ #

def _answer_dispatch(runner, monkeypatch, **env):
    """Run `data answer` with *env*, LiteLLM stubbed; return what reached it."""
    for name in ("BLUE_DATA_LLM_PROVIDER", "BLUE_DATA_LLM_MODEL", "BLUE_DATA_KB_SOURCE_LLM_MODEL",
                 "BLUE_DATA_KB_SOURCE_QUERY_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    invoke(runner, DB + ["database", "create", "main"])
    acompletion = AsyncMock(return_value=_tool_call_response("return_result", {"result": []}))
    with patch("litellm.acompletion", acompletion):
        invoke(runner, DB + ["data", "answer", "Where did Alice work?"])
    return acompletion.call_args.kwargs


def test_a_claude_family_model_goes_to_anthropic(runner, monkeypatch):
    """The #164 setup: the family pair for the Claude-based packages."""
    kwargs = _answer_dispatch(runner, monkeypatch, BLUE_DATA_LLM_PROVIDER="anthropic",
                              BLUE_DATA_LLM_MODEL="claude-opus-4-8", ANTHROPIC_API_KEY="sk-ant-test")
    assert (kwargs["model"], kwargs["custom_llm_provider"]) == ("claude-opus-4-8", "anthropic")


def test_with_no_model_set_each_provider_uses_its_default(runner, monkeypatch):
    kwargs = _answer_dispatch(runner, monkeypatch, BLUE_DATA_LLM_PROVIDER="anthropic",
                              ANTHROPIC_API_KEY="sk-ant-test")
    assert (kwargs["model"], kwargs["custom_llm_provider"]) == ("claude-opus-4-8", "anthropic")
    kwargs = _answer_dispatch(runner, monkeypatch, OPENAI_API_KEY="sk-test")
    assert (kwargs["model"], kwargs["custom_llm_provider"]) == ("gpt-4o", "openai")


def test_a_missing_anthropic_key_is_one_line_naming_the_setting(runner, monkeypatch):
    monkeypatch.setenv("BLUE_DATA_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BLUE_DATA_KB_SOURCE_TRACEBACK", raising=False)
    invoke(runner, DB + ["database", "create", "main"])
    result = runner.invoke(cli, DB + ["data", "answer", "Where did Alice work?"])
    assert result.exit_code == 1
    assert result.output.strip() == (
        "Error: ANTHROPIC_API_KEY is not set: run `blue-kb profile config ANTHROPIC_API_KEY`, or export it."
    )
