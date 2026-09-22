import json
import os
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from blue_data_kb_cli.commands import cli

# Anything in the real ~/.blue-kb newer than this was written by the test run.
_SESSION_START = time.time()


# Every environment variable that can steer the CLI at a real home directory.
# Listed explicitly so that adding a new one without isolating it trips the
# guard below rather than silently writing into the developer's own ~/.blue-kb.
_HOME_ENV_VARS = (
    "BLUE_DATA_KB_SOURCE_HOME",
    "BLUE_DATA_KB_SOURCE_DATA_DIR",
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point every home/data path at tmp_path, and fail loudly if one escapes.

    Isolation used to rest on a single variable name; when the CLI grew profile
    configuration under new names, the tests quietly started writing into the
    real ~/.blue-kb. The guard is the part that matters — it turns that class of
    mistake into a test failure instead of a surprise in someone's home dir.
    """
    # Clear every configurable parameter first, so a developer's own exported
    # OPENAI_API_KEY (or any other) cannot change what these tests observe.
    # Driven off the spec so a new parameter is covered without touching this.
    from blue_data_kb_cli.profile import cli_spec

    for param in cli_spec():
        monkeypatch.delenv(param.name, raising=False)
    monkeypatch.delenv("BLUE_DATA_PROFILE", raising=False)

    for var in _HOME_ENV_VARS:
        monkeypatch.setenv(var, str(tmp_path))

    yield

    real_home = Path.home() / ".blue-kb"
    if real_home.exists():
        touched = [
            f.name for f in real_home.iterdir()
            if f.stat().st_mtime >= _SESSION_START
        ]
        assert not touched, (
            f"CLI tests wrote into the real {real_home}: {touched}. "
            f"Something resolved a path outside tmp_path — check that every "
            f"home/data environment variable is listed in _HOME_ENV_VARS."
        )


class IsolatedRunner(CliRunner):
    """A CliRunner that gives each invocation its own environment.

    The CLI applies a profile by exporting resolved values into `os.environ`,
    which is correct for a real run — one process per command, gone when it
    exits. In-process, those exports would leak into the next `invoke` and make
    the profile tier look like the environment tier. Snapshotting around each
    call restores the one-process-per-command behavior the CLI is written for.
    """

    def invoke(self, *args, **kwargs):
        saved = dict(os.environ)
        try:
            return super().invoke(*args, **kwargs)
        finally:
            os.environ.clear()
            os.environ.update(saved)


@pytest.fixture
def runner(isolated_home):
    """CliRunner whose CLI state is confined to a per-test tmp_path."""
    return IsolatedRunner()


def invoke(runner, args):
    """Invoke `kb <args>` and assert success; return the Result for further checks."""
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    return result


def invoke_json(runner, args):
    """Invoke with --format json (assumed last-applicable) and parse the JSON output."""
    result = invoke(runner, args)
    return json.loads(result.output)
