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


# ------------------------------------------------------------------ #
# Schema asserts                                                       #
# ------------------------------------------------------------------ #

NONE_MODE = ["--schema-creation-mode", "none"]


def test_class_assert_creates_then_reuses(runner):
    first = invoke(runner, DB + ["schema", "class", "assert", "PERSON"] + NONE_MODE)
    assert "created" in first.output
    again = invoke(runner, DB + ["schema", "class", "assert", "PERSON"] + NONE_MODE)
    assert "reused, exact match" in again.output


def test_relation_assert_reports_its_cascade(runner):
    result = invoke(runner, DB + [
        "schema", "relation", "assert", "WORKED_AT",
        "--domain", "PERSON", "--range", "COMPANY",
    ] + NONE_MODE)
    assert "created:" in result.output
    for token in ("PERSON", "COMPANY", "WORKED_AT"):
        assert token in result.output


def test_relation_assert_widens_an_existing_relation(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human"])
    invoke(runner, DB + ["schema", "class", "add", "COMPANY", "A business"])
    invoke(runner, DB + ["schema", "class", "add", "AGENCY", "A public body"])
    invoke(runner, DB + ["schema", "relation", "add", "WORKED_AT", "Employment",
                         "--domain", "PERSON", "--range", "COMPANY"])
    result = invoke(runner, DB + [
        "schema", "relation", "assert", "WORKED_AT",
        "--domain", "AGENCY", "--range", "COMPANY",
    ] + NONE_MODE)
    assert "widened: WORKED_AT" in result.output


def test_class_property_assert_creates_then_reuses(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human"])
    first = invoke(runner, DB + ["schema", "class", "property", "assert", "AGE",
                                 "--class", "PERSON"] + NONE_MODE)
    assert "created" in first.output
    again = invoke(runner, DB + ["schema", "class", "property", "assert", "AGE",
                                 "--class", "PERSON"] + NONE_MODE)
    assert "reused, exact match" in again.output


def test_relation_property_assert_requires_the_relation(runner):
    result = runner.invoke(cli, DB + ["schema", "relation", "property", "assert", "SINCE",
                                      "--relation", "MISSING"] + NONE_MODE)
    assert result.exit_code != 0
    assert "assert" in result.output


def test_class_parent_assert_wires_then_holds(runner):
    first = invoke(runner, DB + ["schema", "class", "parent", "assert",
                                 "EMPLOYEE", "PERSON"] + NONE_MODE)
    assert "created" in first.output
    again = invoke(runner, DB + ["schema", "class", "parent", "assert",
                                 "EMPLOYEE", "PERSON"] + NONE_MODE)
    assert "already present" in again.output


def test_relation_parent_assert_needs_both_relations(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human"])
    invoke(runner, DB + ["schema", "class", "add", "COMPANY", "A business"])
    for rel in ("WORKED_AT", "AFFILIATED_WITH"):
        invoke(runner, DB + ["schema", "relation", "add", rel, "Some relation",
                             "--domain", "PERSON", "--range", "COMPANY"])
    result = invoke(runner, DB + ["schema", "relation", "parent", "assert",
                                  "WORKED_AT", "AFFILIATED_WITH"] + NONE_MODE)
    assert "created" in result.output
