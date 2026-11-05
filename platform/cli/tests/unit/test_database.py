import json
import os
from pathlib import Path

import pytest
from blue_data.source.vector.file_vector_source import FileVectorSource
from blue_data_kb_source import KBSource

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


@pytest.fixture
def redis_answers(monkeypatch):
    """`register` pings a Redis vector store; these tests have no server."""
    redis = pytest.importorskip("redis")  # register builds the vector source
    monkeypatch.setattr(redis.Redis, "ping", lambda self: True)


def test_register_redis_always_stores_a_scan_prefix(runner, redis_answers):
    """#148: without a scan_prefix RedisVectorSource can't create its index, and
    writes go to keys literally prefixed "None"."""
    invoke(runner, ["database", "register", "r", "--search", "semantic", "--vector-backend", "redis",
                    "--redis-host", "127.0.0.1", "--redis-port", "46591"])
    assert _vector_connection(runner, "r") == {"scan_prefix": "blue_kb:", "host": "127.0.0.1", "port": 46591}


def test_register_redis_takes_a_scan_prefix(runner, redis_answers):
    invoke(runner, ["database", "register", "r", "--search", "semantic", "--vector-backend", "redis",
                    "--redis-scan-prefix", "team_a:"])
    assert _vector_connection(runner, "r") == {"scan_prefix": "team_a:"}


_REDIS_URL = "rediss://kbuser:s3cret-pw@redis.internal:6380/0"
_REDIS_REGISTER = ["database", "register", "r", "--search", "semantic", "--vector-backend", "redis"]


def test_the_redis_url_reaches_the_connection_but_is_never_stored(runner, redis_answers, monkeypatch, tmp_path):
    """#181: a URL can carry credentials, so like the Neo4j password it stays in the profile."""
    from blue_data_kb_cli.config import _data_dir, _make_kb

    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_REDIS_URL", _REDIS_URL)
    invoke(runner, _REDIS_REGISTER)
    assert _vector_connection(runner, "r") == {"scan_prefix": "blue_kb:"}
    shown = invoke(runner, ["--database", "r", "database", "config", "show"]).output
    assert "redis.internal" not in shown
    assert not [f for f in Path(_data_dir()).rglob("*") if f.is_file() and b"s3cret-pw" in f.read_bytes()]
    assert not [f for f in Path(_data_dir()).rglob("*") if f.is_file() and b"redis.internal" in f.read_bytes()]

    vector = _make_kb("r")._search_indexer._vector_source
    assert vector.properties["connection"]["url"] == _REDIS_URL


def test_the_redis_url_leaves_other_vector_backends_alone(runner, monkeypatch):
    from blue_data_kb_cli.config import _make_kb

    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_REDIS_URL", _REDIS_URL)
    invoke(runner, ["database", "register", "f", "--search", "semantic"])
    assert "url" not in _make_kb("f")._search_indexer._vector_source.properties["connection"]


@pytest.mark.parametrize("error", ["Authentication required.", "Error 111 connecting to localhost:6379."])
def test_register_refuses_a_redis_that_does_not_accept_it(runner, monkeypatch, error):
    redis = pytest.importorskip("redis")

    def refuse(self):
        raise redis.exceptions.ConnectionError(error)

    monkeypatch.setattr(redis.Redis, "ping", refuse)
    result = runner.invoke(cli, _REDIS_REGISTER)
    assert result.exit_code == 1
    assert error in result.output
    assert "BLUE_DATA_KB_SOURCE_REDIS_URL" in result.output
    assert "r" not in [d["name"] for d in invoke_json(runner, ["database", "list", "--format", "json"])]


def test_search_config_set_redis_stores_a_scan_prefix(runner):
    invoke(runner, ["--database", "main", "database", "create", "main"])
    invoke(runner, ["--database", "main", "database", "search", "config", "set", "--search", "semantic",
                    "--vector-backend", "redis"])
    assert _vector_connection(runner, "main") == {"scan_prefix": "blue_kb:"}


@pytest.mark.parametrize("args", [
    ["database", "register", "r", "--search", "semantic", "--vector-backend", "redis", "--redis-db", "2"],
    ["--database", "main", "database", "search", "config", "set", "--redis-db", "2"],
])
def test_a_redis_logical_db_is_not_offered(runner, args):
    """#177: RediSearch only indexes db 0, and blue-data refuses any other."""
    result = runner.invoke(cli, args)
    assert result.exit_code == 2
    assert "No such option" in result.output


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


def _search_cfg(runner, db):
    return invoke_json(runner, ["--database", db, "database", "search", "config", "show"])


def test_semantic_search_defaults_to_file_vectors_beside_the_graph(runner, tmp_path):
    """#162: the default was `memory`, which a one-process-per-command CLI loses
    before the next command, so semantic search never found anything."""
    invoke(runner, ["database", "register", "v", "--search", "semantic", "--directory", str(tmp_path / "g")])
    vector = _search_cfg(runner, "v")["vector"]
    assert vector["_class"].endswith("FileVectorSource")
    assert vector["connection"] == {"directory": str(tmp_path / "g")}


def test_a_cli_built_directory_syncs_into_a_data_registry(runner, tmp_path):
    """#175: the CLI's BM25 corpus beside the graph is not a database."""
    from blue_data.registry.data_registry import DataRegistry
    from blue_data_kb_source import FileKBSource

    directory = tmp_path / "g"
    invoke(runner, ["database", "register", "main", "--search", "bm25", "--directory", str(directory)])
    invoke(runner, ["--database", "main", "schema", "class", "add", "PERSON", "A person"])
    assert any(f.endswith("_bm25.json") for f in os.listdir(directory))

    registry = DataRegistry()
    registry.register_source(FileKBSource("kb", properties={"connection": {"directory": str(directory)}}))
    registry.sync_source("kb")
    assert [r["name"] for r in registry.list_source_databases("kb")] == ["main"]


def test_chromadb_without_a_path_gets_one_beside_the_graph(runner, tmp_path):
    pytest.importorskip("chromadb")
    invoke(runner, ["database", "register", "v", "--search", "semantic", "--vector-backend", "chromadb",
                    "--directory", str(tmp_path / "g")])
    assert _search_cfg(runner, "v")["vector"]["connection"] == {"path": str(tmp_path / "g" / "chromadb")}


def test_an_explicit_vector_location_is_kept(runner, tmp_path):
    invoke(runner, ["database", "register", "v", "--search", "semantic", "--vector-directory", str(tmp_path / "vec")])
    assert _search_cfg(runner, "v")["vector"]["connection"] == {"directory": str(tmp_path / "vec")}


@pytest.mark.parametrize("args", [
    ["database", "register", "v", "--search", "semantic", "--vector-backend", "memory"],
    ["--database", "v", "database", "search", "config", "set", "--vector-backend", "memory"],
])
def test_memory_vectors_are_refused(runner, args):
    result = runner.invoke(cli, args)
    assert result.exit_code != 0
    assert "every blue-kb command is a new process" in result.output


def test_turning_semantic_search_on_later_gets_a_vector_store_and_embedding(runner, tmp_path):
    invoke(runner, ["database", "register", "v", "--directory", str(tmp_path)])
    invoke(runner, ["--database", "v", "database", "search", "config", "set", "--search", "semantic"])
    cfg = _search_cfg(runner, "v")
    assert cfg["vector"]["connection"] == {"directory": str(tmp_path)}
    assert cfg["embedding"] == {"provider": "openai"}


def test_a_database_registered_without_a_vector_location_gets_the_default_when_loaded(tmp_path):
    from blue_data_kb_cli.config import _VECTOR_CLASS_BY_BACKEND, _with_search_defaults

    for backend, expected in (("file", {"directory": str(tmp_path)}),
                              ("chromadb", {"path": str(tmp_path / "chromadb")})):
        props = {"connection": {"directory": str(tmp_path)},
                 "search": {"mode": "semantic", "vector": {"_class": _VECTOR_CLASS_BY_BACKEND[backend], "connection": {}}}}
        assert _with_search_defaults(props)["search"]["vector"]["connection"] == expected


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


def _stored_vector(directory, collection):
    source = FileVectorSource("kb_vectors", directory=str(directory))
    source.upsert(collection, "n1", {"embedding": [1.0, 0.0], "name": "Alice"})
    return source


def test_database_delete_removes_its_vectors_and_no_others(runner, tmp_path):
    invoke(runner, ["database", "register", "main", "--search", "semantic"])
    invoke(runner, ["--database", "main", "database", "create", "main"])
    data_dir = Path(invoke_json(runner, ["database", "list", "--format", "json"])["main"]["connection"]["directory"])
    source = _stored_vector(data_dir, "kb_data_main")
    _stored_vector(data_dir, "kb_schema_main")
    _stored_vector(data_dir, "kb_data_other")

    invoke(runner, ["database", "delete", "main", "--yes"])

    assert source.list_ids("kb_data_main") == []
    assert source.list_ids("kb_schema_main") == []
    assert source.list_ids("kb_data_other") == ["n1"]


def test_database_delete_finishes_when_the_vector_store_fails(runner, tmp_path, monkeypatch):
    invoke(runner, ["--database", "main", "database", "create", "main"])

    def fail(self, database):
        raise ConnectionError("store unreachable")

    monkeypatch.setattr(KBSource, "clear_vectors", fail)
    result = invoke(runner, ["database", "delete", "main", "--yes"])
    assert "could not remove the vectors" in result.output
    assert "store unreachable" in result.output
    assert "main" not in invoke_json(runner, ["database", "list", "--format", "json"])


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


def test_deleting_a_neo4j_database_keeps_its_vectors(runner, monkeypatch):
    """The graph stays on the server, so the vectors that index it stay too."""
    _neo4j_db(runner)
    monkeypatch.setattr(KBSource, "clear_vectors", lambda self, database: pytest.fail("cleared"))
    invoke(runner, ["database", "delete", "neo", "--yes"])


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
