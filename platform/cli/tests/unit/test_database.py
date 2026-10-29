import json
import os
from pathlib import Path

import pytest

from blue_data_kb_cli.commands import cli

from .conftest import invoke, invoke_json


def test_database_list_empty(runner):
    result = invoke(runner, ["database", "list"])
    assert "no databases registered" in result.output


def test_database_create_auto_registers(runner):
    invoke(runner, ["--database", "main", "database", "create", "main"])
    rows = invoke_json(runner, ["database", "list", "--format", "json"])
    assert rows["main"]["_class"] == "blue_data_kb_source.file_kb_source.FileKBSource"
    assert rows["main"].get("search") is None


def test_database_create_inherits_session_search_config(runner):
    """`create` is the shortcut for `register`, so it must not quietly produce a
    database with search off when the session's own database has it on."""
    invoke(runner, ["database", "register", "primary", "--search", "bm25"])
    invoke(runner, ["--database", "primary", "database", "create", "derived"])
    cfg = invoke_json(runner, ["--database", "derived", "database", "search", "config", "show"])
    assert cfg["mode"] == "bm25"


def test_database_create_inherits_session_backend_connection(runner, tmp_path):
    data_dir = str(tmp_path / "graphs")
    invoke(runner, ["database", "register", "primary", "--directory", data_dir])
    invoke(runner, ["--database", "primary", "database", "create", "derived"])
    rows = invoke_json(runner, ["database", "list", "--format", "json"])
    assert rows["derived"]["_class"] == rows["primary"]["_class"]
    assert rows["derived"]["connection"]["directory"] == data_dir


def test_database_create_does_not_persist_a_none_search_mode(runner):
    """An absent "search" key already means mode=none to KBSource; writing it
    out explicitly would only add noise to the registry record."""
    invoke(runner, ["--database", "main", "database", "create", "main"])
    invoke(runner, ["--database", "main", "database", "create", "second"])
    rows = invoke_json(runner, ["database", "list", "--format", "json"])
    assert rows["second"].get("search") is None


def test_database_register_file_backend(runner, tmp_path):
    result = invoke(runner, [
        "database", "register", "mydb",
        "--graph-backend", "file",
        "--directory", str(tmp_path / "data"),
    ])
    assert "Registered database" in result.output
    rows = invoke_json(runner, ["database", "list", "--format", "json"])
    assert rows["mydb"]["connection"]["directory"] == str(tmp_path / "data")
    # The backend file should have been created immediately.
    assert (tmp_path / "data" / "mydb.json").exists()


def test_bm25_search_works_without_an_explicit_index_dir(runner):
    """The CLI is one process per command, so an in-memory BM25 corpus is empty
    on every invocation and `schema search` silently returns nothing. The
    corpus directory defaults to the graph directory, as --bm25-index-dir has
    always documented."""
    pytest.importorskip("rank_bm25")
    invoke(runner, ["database", "register", "main", "--search", "bm25"])
    invoke(runner, ["--database", "main", "schema", "class", "add", "PERSON", "A human"])
    invoke(runner, ["--database", "main", "schema", "class", "add", "COMPANY", "A business"])
    invoke(runner, ["--database", "main", "schema", "relation", "add",
                    "WORKED_AT", "Employment relationship",
                    "--domain", "PERSON", "--range", "COMPANY"])
    rows = invoke_json(runner, ["--database", "main", "schema", "search",
                                "employment", "--format", "json"])
    assert any(r["name"] == "WORKED_AT" for r in rows)


def test_register_defaults_bm25_index_dir_to_the_graph_directory(runner, tmp_path):
    data_dir = str(tmp_path / "graphs")
    invoke(runner, ["database", "register", "main", "--search", "bm25", "--directory", data_dir])
    cfg = invoke_json(runner, ["--database", "main", "database", "search", "config", "show"])
    assert cfg["bm25_index_dir"] == data_dir


def test_enabling_search_later_also_gets_an_index_dir(runner):
    invoke(runner, ["database", "register", "main"])
    invoke(runner, ["--database", "main", "database", "search", "config", "set", "--search", "bm25"])
    cfg = invoke_json(runner, ["--database", "main", "database", "search", "config", "show"])
    assert cfg.get("bm25_index_dir")


def test_database_register_bm25_search(runner):
    invoke(runner, ["database", "register", "searchable", "--search", "bm25"])
    cfg = invoke_json(runner, ["--database", "searchable", "database", "search", "config", "show"])
    assert cfg["mode"] == "bm25"


def test_database_search_config_set(runner):
    invoke(runner, ["--database", "main", "database", "create", "main"])
    invoke(runner, ["--database", "main", "database", "search", "config", "set", "--search", "bm25"])
    cfg = invoke_json(runner, ["--database", "main", "database", "search", "config", "show"])
    assert cfg["mode"] == "bm25"


def _vector_connection(runner, db):
    cfg = invoke_json(runner, ["--database", db, "database", "search", "config", "show"])
    return cfg["vector"]["connection"]


def test_register_redis_always_stores_a_scan_prefix(runner):
    """#148: without a scan_prefix RedisVectorSource can't create its index, and
    writes go to keys literally prefixed "None"."""
    pytest.importorskip("redis")  # register builds the vector source
    invoke(runner, ["database", "register", "r", "--search", "semantic", "--vector-backend", "redis",
                    "--redis-host", "127.0.0.1", "--redis-port", "46591"])
    assert _vector_connection(runner, "r") == {"scan_prefix": "blue_kb:", "host": "127.0.0.1", "port": 46591}


def test_register_redis_takes_a_scan_prefix(runner):
    pytest.importorskip("redis")
    invoke(runner, ["database", "register", "r", "--search", "semantic", "--vector-backend", "redis",
                    "--redis-scan-prefix", "team_a:"])
    assert _vector_connection(runner, "r") == {"scan_prefix": "team_a:"}


def test_search_config_set_redis_stores_a_scan_prefix(runner):
    invoke(runner, ["--database", "main", "database", "create", "main"])
    invoke(runner, ["--database", "main", "database", "search", "config", "set", "--search", "semantic",
                    "--vector-backend", "redis", "--redis-db", "2"])
    assert _vector_connection(runner, "main") == {"scan_prefix": "blue_kb:", "db": 2}


def test_search_config_set_redis_options_imply_the_redis_backend(runner):
    invoke(runner, ["--database", "main", "database", "create", "main"])
    invoke(runner, ["--database", "main", "database", "search", "config", "set", "--redis-port", "6380"])
    cfg = invoke_json(runner, ["--database", "main", "database", "search", "config", "show"])
    assert cfg["vector"]["_class"].endswith("RedisVectorSource")
    assert cfg["vector"]["connection"] == {"scan_prefix": "blue_kb:", "port": 6380}


def test_search_config_set_refuses_redis_options_for_another_backend(runner):
    invoke(runner, ["--database", "main", "database", "create", "main"])
    result = runner.invoke(cli, ["--database", "main", "database", "search", "config", "set",
                                 "--vector-backend", "chromadb", "--redis-port", "6380"])
    assert result.exit_code != 0
    assert "--redis-* options don't apply to --vector-backend chromadb" in result.output


def _failing_initialization(monkeypatch):
    """`register` whose backend registers but can't initialize, like an unreachable Neo4j."""
    import blue_data_kb_cli.commands as commands
    real = commands.register_database

    def flaky(*args, **kwargs):
        kb = real(*args, **kwargs)
        kb.fetch_databases = lambda: []
        def refuse(name):
            raise RuntimeError("Couldn't connect to 127.0.0.1:7999")
        kb.create_database = refuse
        return kb
    monkeypatch.setattr(commands, "register_database", flaky)


def test_a_failed_register_leaves_no_registry_entry(runner, monkeypatch):
    """#154: an unreachable server used to leave the name registered anyway."""
    _failing_initialization(monkeypatch)
    result = runner.invoke(cli, ["database", "register", "main", "--search", "bm25"])
    assert result.exit_code != 0 and "Couldn't connect" in result.output
    assert "main" not in invoke_json(runner, ["database", "list", "--format", "json"])


def test_a_failed_re_register_keeps_the_previous_config(runner, monkeypatch):
    invoke(runner, ["database", "register", "main", "--search", "bm25"])
    _failing_initialization(monkeypatch)
    result = runner.invoke(cli, ["database", "register", "main", "--search", "none"])
    assert result.exit_code != 0
    cfg = invoke_json(runner, ["--database", "main", "database", "search", "config", "show"])
    assert cfg["mode"] == "bm25"


def test_selecting_an_unknown_name_warns_that_it_will_be_file_backed(runner):
    """#154: after a failed register, `select` then silently sent writes to a file."""
    result = invoke(runner, ["database", "select", "ghost"])
    assert "Default database set to: ghost" in result.output
    assert "'ghost' is not registered and doesn't exist yet" in result.output
    assert "file-backed" in result.output


@pytest.mark.parametrize("make", [
    ["database", "register", "known"],                 # registered
    ["--database", "known", "database", "create", "known"],  # exists as a file database
])
def test_selecting_a_known_database_does_not_warn(runner, make):
    invoke(runner, make)
    result = invoke(runner, ["database", "select", "known"])
    assert "Warning" not in result.output


def test_database_select_changes_default(runner):
    invoke(runner, ["--database", "main", "database", "create", "main"])
    invoke(runner, ["database", "select", "main"])
    # Now a bare `blue-kb schema list` (no --database) should target "main".
    result = invoke(runner, ["schema", "list"])
    assert "no schema declared" in result.output


def test_database_delete_removes_file(runner, tmp_path):
    invoke(runner, ["--database", "main", "database", "create", "main"])
    invoke(runner, ["database", "delete", "main", "--yes"])
    rows = invoke_json(runner, ["database", "list", "--format", "json"])
    assert "main" not in rows
    assert not (tmp_path / "main.json").exists()


def test_database_delete_missing_fails(runner):
    result = runner.invoke(cli, ["database", "delete", "ghost", "--yes"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_database_config_show_and_set(runner, tmp_path):
    invoke(runner, ["--database", "main", "database", "create", "main"])
    cfg = json.loads(runner.invoke(cli, ["--database", "main", "database", "config", "show"]).output)
    assert cfg["_class"] == "blue_data_kb_source.file_kb_source.FileKBSource"

    new_dir = tmp_path / "elsewhere"
    invoke(runner, ["--database", "main", "database", "config", "set", "--directory", str(new_dir)])
    cfg = json.loads(runner.invoke(cli, ["--database", "main", "database", "config", "show"]).output)
    assert cfg["connection"]["directory"] == str(new_dir)


# ------------------------------------------------------------------ #
# Neo4j connection: TLS scheme kept (#121), password from the profile (#122)
# ------------------------------------------------------------------ #

def _dial(monkeypatch, database):
    """Connect the CLI's way and return (uri, auth) the Neo4j driver was given."""
    pytest.importorskip("neo4j")
    import blue_data.source.graph.neo4j.neo4j_graph_source as ngs
    from unittest.mock import patch

    from blue_data_kb_cli.config import _make_kb

    with patch.object(ngs.GraphDatabase, "driver") as driver:
        _make_kb(database)
    return driver.call_args.args[0], driver.call_args.kwargs["auth"]


def _neo4j_db(runner, uri="neo4j+s://kb.example.com:7687"):
    invoke(runner, ["--database", "neo", "database", "config", "set",
                    "--graph-backend", "neo4j", "--neo4j-uri", uri, "--neo4j-user", "kbuser"])


def test_neo4j_tls_uri_is_dialled_over_tls(runner, monkeypatch):
    _neo4j_db(runner)
    uri, _ = _dial(monkeypatch, "neo")
    assert uri == "neo4j+s://kb.example.com:7687"


def test_neo4j_uri_with_an_unknown_scheme_is_refused(runner):
    result = runner.invoke(cli, ["--database", "neo", "database", "config", "set",
                                 "--graph-backend", "neo4j", "--neo4j-uri", "https://kb.example.com"])
    assert result.exit_code != 0
    assert "unsupported Neo4j URI scheme" in result.output


def test_neo4j_password_comes_from_the_profile_at_connect_time(runner, monkeypatch):
    _neo4j_db(runner)
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_NEO4J_PASSWORD", "from-profile")
    _, auth = _dial(monkeypatch, "neo")
    assert auth == ("kbuser", "from-profile")


@pytest.mark.parametrize("args", [
    ["database", "register", "neo", "--graph-backend", "neo4j", "--neo4j-password", "pw"],
    ["--database", "neo", "database", "config", "set", "--neo4j-password", "pw"],
])
def test_neo4j_password_flag_is_refused(runner, args):
    result = runner.invoke(cli, args)
    assert result.exit_code != 0
    assert "BLUE_DATA_KB_SOURCE_NEO4J_PASSWORD" in result.output
    assert not (Path(os.environ["BLUE_DATA_KB_SOURCE_DATA_DIR"]) / "registry.json").exists() \
        or "pw" not in (Path(os.environ["BLUE_DATA_KB_SOURCE_DATA_DIR"]) / "registry.json").read_text()


def test_a_stored_password_is_masked_in_every_output(runner):
    """Records written by the old `config set --neo4j-password` hold it in plaintext."""
    from blue_data_kb_cli.config import update_db_config

    _neo4j_db(runner)
    update_db_config("neo", {"connection": {"password": "legacy-secret"}})
    for args in (["--database", "neo", "database", "config", "show"],
                 ["database", "list", "--format", "json"]):
        out = invoke(runner, args).output
        assert "legacy-secret" not in out
        assert '"password"' in out
