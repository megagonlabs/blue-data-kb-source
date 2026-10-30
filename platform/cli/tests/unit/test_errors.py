"""An exception no command handles becomes a one-line `Error: …` naming what to fix,
not a raw traceback (#155)."""
import pytest

import blue_data_kb_cli.commands as commands
from blue_data_kb_cli.commands import _user_error, cli
from blue_data_kb_source.types import KBError

from .conftest import invoke


def _exc(module: str, name: str, message: str) -> Exception:
    """An instance of a stand-in for a driver's exception class, without the driver."""
    return type(name, (Exception,), {"__module__": module})(message)


def test_a_missing_openai_key_is_one_line_naming_the_setting(runner, monkeypatch):
    monkeypatch.delenv("BLUE_DATA_KB_SOURCE_TRACEBACK", raising=False)
    invoke(runner, ["--database", "main", "database", "create", "main"])
    result = runner.invoke(cli, ["--database", "main", "data", "answer", "Where did Alice work?"])
    assert result.exit_code == 1
    assert result.output.strip() == (
        "Error: OPENAI_API_KEY is not set: run `blue-kb profile config OPENAI_API_KEY`, or export it."
    )


@pytest.mark.parametrize("exc, expected", [
    (_exc("openai", "AuthenticationError", "Error code: 401 - invalid_api_key"),
     "OpenAI rejected the API key (OPENAI_API_KEY): Error code: 401 - invalid_api_key"),
    (_exc("litellm.exceptions", "AuthenticationError", "invalid x-api-key"),
     "OpenAI rejected the API key (OPENAI_API_KEY): invalid x-api-key"),
    (_exc("litellm.exceptions", "NotFoundError", "model: claude-nope"),
     "OpenAI request failed: model: claude-nope"),
    (_exc("neo4j.exceptions", "ServiceUnavailable", "Couldn't connect to 127.0.0.1:7999\nConnection refused"),
     "cannot connect to Neo4j: Couldn't connect to 127.0.0.1:7999 Connection refused Check the database's URI"),
    (_exc("neo4j.exceptions", "AuthError", "unauthorized"),
     "Neo4j rejected the credentials: set the password with `blue-kb profile config BLUE_DATA_KB_SOURCE_NEO4J_PASSWORD`."),
    (_exc("redis.exceptions", "ConnectionError", "Error 111 connecting to localhost:6379."),
     "cannot connect to Redis: Error 111 connecting to localhost:6379. Check the host and port"),
    (KBError("could not create vector index 'kb_data_main'"), "could not create vector index 'kb_data_main'"),
])
def test_known_causes_get_a_message_that_names_the_fix(exc, expected):
    assert _user_error(exc).startswith(expected)


def test_a_cause_behind_a_kberror_is_still_recognised():
    try:
        try:
            raise _exc("redis.exceptions", "ConnectionError", "Error 111 connecting to localhost:6379.")
        except Exception as inner:
            raise KBError("could not create vector index") from inner
    except KBError as outer:
        assert _user_error(outer).startswith("cannot connect to Redis:")


def test_an_unexpected_exception_is_one_line_with_the_way_to_the_traceback(runner, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("something broke\nsecond line")
    monkeypatch.setattr(commands, "_make_kb", boom)
    monkeypatch.delenv("BLUE_DATA_KB_SOURCE_TRACEBACK", raising=False)
    result = runner.invoke(cli, ["--database", "main", "schema", "list"])
    assert result.exit_code == 1
    assert result.output.strip() == (
        "Error: RuntimeError: something broke second line "
        "(set BLUE_DATA_KB_SOURCE_TRACEBACK=1 for the full traceback)"
    )
    assert "Traceback" not in result.output


def test_the_traceback_env_lets_the_exception_through(runner, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("something broke")
    monkeypatch.setattr(commands, "_make_kb", boom)
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_TRACEBACK", "1")
    result = runner.invoke(cli, ["--database", "main", "schema", "list"])
    assert isinstance(result.exception, RuntimeError)


def test_a_provider_error_names_the_configured_provider(monkeypatch):
    monkeypatch.setenv("BLUE_DATA_LLM_PROVIDER", "anthropic")
    assert _user_error(_exc("litellm.exceptions", "AuthenticationError", "invalid x-api-key")) == (
        "Anthropic rejected the API key (ANTHROPIC_API_KEY): invalid x-api-key"
    )
