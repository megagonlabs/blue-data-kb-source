import json

import pytest
from click.testing import CliRunner

from blue_data_kb_cli.commands import cli


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """CliRunner with an isolated KBDB_DATA_DIR so tests never touch ~/.kbdb."""
    monkeypatch.setenv("KBDB_DATA_DIR", str(tmp_path))
    return CliRunner()


def invoke(runner, args):
    """Invoke `kb <args>` and assert success; return the Result for further checks."""
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    return result


def invoke_json(runner, args):
    """Invoke with --format json (assumed last-applicable) and parse the JSON output."""
    result = invoke(runner, args)
    return json.loads(result.output)
