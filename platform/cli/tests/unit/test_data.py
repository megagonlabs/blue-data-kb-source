import re

from blue_data_kb_cli.commands import cli

from .conftest import invoke, invoke_json

DB = ["--database", "main"]


def _extract_id(output: str) -> str:
    match = re.search(r"\(id: ([0-9a-fA-F-]+)\)", output)
    assert match, output
    return match.group(1)


def _seed_schema_and_entities(runner):
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "A person employed by an organization"])
    invoke(runner, DB + ["schema", "class", "add", "COMPANY", "A business organization"])
    invoke(runner, DB + [
        "schema", "relation", "add", "WORKED_AT", "Employment relationship",
        "--domain", "EMPLOYEE", "--range", "COMPANY",
    ])
    invoke(runner, DB + [
        "schema", "relation", "property", "add", "START_DATE", "Start of employment",
        "--relation", "WORKED_AT",
    ])
    invoke(runner, DB + ["data", "entity", "add", "--name", "Alice", "--class", "EMPLOYEE",
                         "--text", "Alice is a software engineer."])
    invoke(runner, DB + ["data", "entity", "add", "--name", "IBM", "--class", "COMPANY"])


def test_source_add_and_list(runner):
    invoke(runner, DB + ["data", "source", "add", "resume_alice", "--name", "Alice's résumé"])
    rows = invoke_json(runner, DB + ["data", "source", "list", "--format", "json"])
    assert any(r["name"] == "Alice's résumé" for r in rows)


def test_entity_add_list_update_delete(runner):
    _seed_schema_and_entities(runner)

    rows = invoke_json(runner, DB + ["data", "entity", "list", "--format", "json"])
    names = {r["name"] for r in rows}
    assert names == {"Alice", "IBM"}
    assert {r["class"] for r in rows} == {"EMPLOYEE", "COMPANY"}

    invoke(runner, DB + ["data", "entity", "update", "--entity", "Alice", "--description", "Updated"])

    invoke(runner, DB + ["data", "entity", "delete", "--entity", "IBM", "--yes"])
    rows = invoke_json(runner, DB + ["data", "entity", "list", "--format", "json"])
    assert [r["name"] for r in rows] == ["Alice"]


def test_extraction_add_attaches_additional_evidence(runner):
    _seed_schema_and_entities(runner)
    rows = invoke_json(runner, DB + ["data", "entity", "list", "--format", "json"])
    alice_id = next(r["id"] for r in rows if r["name"] == "Alice")

    invoke(runner, DB + [
        "data", "extraction", "add",
        "--element-id", alice_id,
        "--text", "Alice was later promoted to team lead.",
    ])

    rows = invoke_json(runner, DB + ["data", "entity", "list", "--show-provenance", "--format", "json"])
    alice_row = next(r for r in rows if r["name"] == "Alice")
    assert "Alice is a software engineer." in alice_row["provenance"]
    assert "Alice was later promoted to team lead." in alice_row["provenance"]


def test_entity_not_found_error(runner):
    result = runner.invoke(cli, DB + ["data", "entity", "update", "--entity", "Ghost", "--name", "X"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_entity_property_set_list_delete(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + ["schema", "class", "property", "add", "AGE", "Age of a person", "--class", "EMPLOYEE"])

    invoke(runner, DB + ["data", "entity", "property", "set", "30",
                         "--entity", "Alice", "--property", "AGE", "--type", "NUMBER"])
    rows = invoke_json(runner, DB + ["data", "entity", "property", "list", "--entity", "Alice", "--format", "json"])
    assert rows == [{"property": "AGE", "value": "30", "type": "NUMBER", "id": rows[0]["id"]}]

    invoke(runner, DB + ["data", "entity", "property", "delete", "--entity", "Alice",
                         "--property", "AGE", "--yes"])
    rows = invoke_json(runner, DB + ["data", "entity", "property", "list", "--entity", "Alice", "--format", "json"])
    assert rows == []


def test_fact_add_list_property_and_delete(runner):
    _seed_schema_and_entities(runner)

    result = invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
        "--text", "Alice joined IBM in June 1998.",
    ])
    fact_id = _extract_id(result.output)

    rows = invoke_json(runner, DB + ["data", "fact", "list", "--format", "json"])
    assert rows == [{"id": fact_id, "head": "Alice", "relation": "WORKED_AT", "tail": "IBM"}]

    invoke(runner, DB + [
        "data", "fact", "property", "set", "1998-06-01",
        "--fact-id", fact_id, "--property", "START_DATE", "--type", "DATE",
    ])
    rows = invoke_json(runner, DB + ["data", "fact", "property", "list", "--fact-id", fact_id, "--format", "json"])
    assert rows[0]["property"] == "START_DATE"
    assert rows[0]["value"] == "1998-06-01"

    invoke(runner, DB + ["data", "fact", "delete", "--fact-id", fact_id, "--yes"])
    rows = invoke_json(runner, DB + ["data", "fact", "list", "--format", "json"])
    assert rows == []


def test_data_list_shows_entities_and_facts(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
    ])
    result = invoke(runner, DB + ["data", "list"])
    assert "Alice" in result.output
    assert "IBM" in result.output
    assert "WORKED_AT" in result.output


def test_data_search_keyword_mode(runner):
    _seed_schema_and_entities(runner)
    rows = invoke_json(runner, DB + ["data", "search", "Alice", "--format", "json"])
    assert any(r["name"] == "Alice" for r in rows)


def test_reindex_requires_search_enabled(runner):
    invoke(runner, DB + ["data", "entity", "add", "--name", "Alice"])
    result = runner.invoke(cli, DB + ["data", "reindex"])
    assert result.exit_code != 0
    assert "Search is not enabled" in result.output


def test_reindex_with_search_enabled(runner):
    invoke(runner, ["database", "register", "main", "--search", "bm25"])
    invoke(runner, DB + ["data", "entity", "add", "--name", "Alice"])
    result = invoke(runner, DB + ["data", "reindex"])
    assert "Search index rebuilt" in result.output
