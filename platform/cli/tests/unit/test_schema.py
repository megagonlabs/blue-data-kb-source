from blue_data_kb_cli.commands import cli

from .conftest import invoke, invoke_json

DB = ["--database", "main"]


def test_class_add_is_idempotent(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human individual"])
    result = invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human individual"])
    assert "Added class" in result.output

    rows = invoke_json(runner, DB + ["schema", "class", "list", "--format", "json"])
    assert [r["name"] for r in rows] == ["PERSON"]


def test_class_update(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human individual"])
    invoke(runner, DB + ["schema", "class", "update", "--class", "PERSON", "--description", "Updated desc"])
    rows = invoke_json(runner, DB + ["schema", "class", "list", "--format", "json"])
    assert rows[0]["description"] == "Updated desc"


def test_class_hierarchy_parent_child(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human individual"])
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "A person employed by an organization"])
    invoke(runner, DB + ["schema", "class", "parent", "add", "--class", "EMPLOYEE", "--parent", "PERSON"])

    result = invoke(runner, DB + ["schema", "class", "parent", "list", "--class", "EMPLOYEE"])
    assert "PERSON" in result.output

    result = invoke(runner, DB + ["schema", "class", "child", "list", "--class", "PERSON"])
    assert "EMPLOYEE" in result.output


def test_class_property_add_and_list(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human individual"])
    invoke(runner, DB + ["schema", "class", "property", "add", "AGE", "Age of a person", "--class", "PERSON"])
    rows = invoke_json(runner, DB + ["schema", "class", "property", "list", "--format", "json"])
    assert rows == [{"class": "PERSON", "property": "AGE", "description": "Age of a person"}]


def test_relation_add_and_list_with_domain_range(runner):
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "A person employed by an organization"])
    invoke(runner, DB + ["schema", "class", "add", "COMPANY", "A business organization"])
    invoke(runner, DB + [
        "schema", "relation", "add", "WORKED_AT", "Employment relationship",
        "--domain", "EMPLOYEE", "--range", "COMPANY",
    ])
    rows = invoke_json(runner, DB + ["schema", "relation", "list", "--format", "json"])
    assert rows == [{
        "name": "WORKED_AT", "source": "EMPLOYEE", "target": "COMPANY",
        "description": "Employment relationship",
    }]


def test_relation_property_add_and_list(runner):
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
    rows = invoke_json(runner, DB + ["schema", "relation", "property", "list", "--format", "json"])
    assert rows == [{"relation": "WORKED_AT", "property": "START_DATE", "description": "Start of employment"}]


def test_relation_domain_attach_and_list(runner):
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "An employed person"])
    invoke(runner, DB + ["schema", "class", "add", "COMPANY", "A business organization"])
    invoke(runner, DB + ["schema", "class", "add", "CONTRACTOR", "An independent contractor"])
    invoke(runner, DB + [
        "schema", "relation", "add", "WORKED_AT", "Employment",
        "--domain", "EMPLOYEE", "--range", "COMPANY",
    ])
    invoke(runner, DB + [
        "schema", "relation", "domain", "attach",
        "--relation", "WORKED_AT", "--class", "CONTRACTOR",
    ])
    rows = invoke_json(runner, DB + [
        "schema", "relation", "domain", "list", "--relation", "WORKED_AT", "--format", "json",
    ])
    assert {r["name"] for r in rows} == {"EMPLOYEE", "CONTRACTOR"}
    result = invoke(runner, DB + [
        "schema", "relation", "domain", "list", "--relation", "WORKED_AT",
    ])
    assert "CONTRACTOR" in result.output


def test_relation_range_attach_and_dedupe(runner):
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "An employed person"])
    invoke(runner, DB + ["schema", "class", "add", "COMPANY", "A business organization"])
    invoke(runner, DB + ["schema", "class", "add", "UNIVERSITY", "An academic institution"])
    invoke(runner, DB + [
        "schema", "relation", "add", "WORKED_AT", "Employment",
        "--domain", "EMPLOYEE", "--range", "COMPANY",
    ])
    invoke(runner, DB + [
        "schema", "relation", "range", "attach", "--relation", "WORKED_AT", "--class", "UNIVERSITY",
    ])
    invoke(runner, DB + [
        "schema", "relation", "range", "attach", "--relation", "WORKED_AT", "--class", "COMPANY",
    ])
    rows = invoke_json(runner, DB + [
        "schema", "relation", "range", "list", "--relation", "WORKED_AT", "--format", "json",
    ])
    assert sorted(r["name"] for r in rows) == ["COMPANY", "UNIVERSITY"]


def test_schema_search_keyword_mode(runner):
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "A person employed by an organization"])
    invoke(runner, DB + ["schema", "class", "add", "COMPANY", "A business organization"])
    invoke(runner, DB + [
        "schema", "relation", "add", "WORKED_AT", "Employment relationship",
        "--domain", "EMPLOYEE", "--range", "COMPANY",
    ])

    rows = invoke_json(runner, DB + ["schema", "search", "employment", "--format", "json"])
    names = {r["name"] for r in rows}
    assert "WORKED_AT" in names


def test_schema_search_type_filter(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human individual"])
    invoke(runner, DB + ["schema", "class", "add", "COMPANY", "A business organization"])
    invoke(runner, DB + [
        "schema", "relation", "add", "FOUNDED", "A business organization founding relationship",
        "--domain", "PERSON", "--range", "COMPANY",
    ])

    rows = invoke_json(runner, DB + [
        "schema", "search", "business organization", "--type", "class", "--format", "json",
    ])
    assert all(r["type"] == "CLASS" for r in rows)


def test_class_not_found_error(runner):
    result = runner.invoke(cli, DB + ["schema", "class", "update", "--class", "GHOST", "--description", "x"])
    assert result.exit_code != 0
    assert "not found" in result.output
