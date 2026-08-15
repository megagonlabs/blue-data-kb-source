import json

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


def test_database_register_bm25_search(runner):
    invoke(runner, ["database", "register", "searchable", "--search", "bm25"])
    cfg = invoke_json(runner, ["--database", "searchable", "database", "search", "config", "show"])
    assert cfg["mode"] == "bm25"


def test_database_search_config_set(runner):
    invoke(runner, ["--database", "main", "database", "create", "main"])
    invoke(runner, ["--database", "main", "database", "search", "config", "set", "--search", "bm25"])
    cfg = invoke_json(runner, ["--database", "main", "database", "search", "config", "show"])
    assert cfg["mode"] == "bm25"


def test_database_select_changes_default(runner):
    invoke(runner, ["--database", "main", "database", "create", "main"])
    invoke(runner, ["database", "select", "main"])
    # Now a bare `kb schema list` (no --database) should target "main".
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
