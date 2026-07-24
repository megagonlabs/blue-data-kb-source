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
    """Patch openai.AsyncOpenAI so the agent immediately calls return_result."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=_tool_call_response("return_result", {"result": payload})
    )
    return MagicMock(return_value=mock_client)


def _mock_openai_no_tool_calls():
    """Patch openai.AsyncOpenAI so the agent stops on the first turn (extraction loop)."""
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
    with patch("openai.AsyncOpenAI", _mock_openai_no_tool_calls()):
        result = runner.invoke(cli, DB + ["data", "extract", "Alice joined IBM in June 1998."])
    assert result.exit_code == 0, result.output
    assert "Extraction complete." in result.output


def test_extract_schema_discovery_mode_none_omits_search_schema_tool(runner):
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "A person employed by an organization"])
    factory = _mock_openai_no_tool_calls()
    with patch("openai.AsyncOpenAI", factory):
        result = runner.invoke(cli, DB + [
            "data", "extract", "Alice joined IBM in June 1998.",
            "--schema-discovery-mode", "none",
        ])
    assert result.exit_code == 0, result.output
    call_kwargs = factory.return_value.chat.completions.create.call_args.kwargs
    tool_names = {t["function"]["name"] for t in call_kwargs["tools"]}
    assert "search_schema" not in tool_names


def test_answer_table_format(runner):
    _seed(runner)
    payload = [{"entity": "Alice", "relation": "WORKED_AT", "value": None, "provenance": "Alice joined IBM in June 1998."}]
    with patch("openai.AsyncOpenAI", _mock_openai_return_result(payload)):
        result = runner.invoke(cli, DB + ["data", "answer", "Where did Alice work?"])
    assert result.exit_code == 0, result.output
    assert "Alice" in result.output
    assert "WORKED_AT" in result.output


def test_answer_json_format(runner):
    _seed(runner)
    payload = [{"entity": "Alice", "relation": "WORKED_AT"}]
    with patch("openai.AsyncOpenAI", _mock_openai_return_result(payload)):
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
    with patch("openai.AsyncOpenAI", MagicMock(return_value=mock_client)):
        result = runner.invoke(cli, DB + ["data", "answer", "Where did Alice work?", "--format", "nl"])
    assert result.exit_code == 0, result.output
    assert "Alice worked at IBM." in result.output


def test_validate_structured_format(runner):
    _seed(runner)
    payload = {
        "supporting": [{"fact": "Alice_WORKED_AT_IBM", "head": "Alice", "tail": "IBM", "relation": "WORKED_AT"}],
        "counter": [],
    }
    with patch("openai.AsyncOpenAI", _mock_openai_return_result(payload)):
        result = runner.invoke(cli, DB + ["data", "validate", "Alice worked at IBM."])
    assert result.exit_code == 0, result.output
    assert "Supporting" in result.output
    assert "Alice" in result.output and "IBM" in result.output
    assert "Counter" in result.output
    assert "(none)" in result.output


def test_validate_natural_language_format(runner):
    _seed(runner)
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[
            _tool_call_response("return_result", {"result": {"supporting": [], "counter": []}}),
            _text_response("This statement is supported."),
        ]
    )
    with patch("openai.AsyncOpenAI", MagicMock(return_value=mock_client)):
        result = runner.invoke(cli, DB + ["data", "validate", "Alice worked at IBM.", "--natural-language"])
    assert result.exit_code == 0, result.output
    assert "This statement is supported." in result.output
