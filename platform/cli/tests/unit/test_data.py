import json
import re

from blue_data_kb_cli.commands import _print_metadata_table, cli, console
from blue_data_kb_cli.config import _make_kb

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
    detailed = invoke(runner, DB + ["data", "source", "list", "--show-detailed"])
    assert "Created" in detailed.output and "Modified" in detailed.output


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


def test_entity_extraction_add_attaches_additional_text(runner):
    _seed_schema_and_entities(runner)

    invoke(runner, DB + [
        "data", "entity", "extraction", "add",
        "--entity", "Alice",
        "--text", "Alice was later promoted to team lead.",
    ])

    rows = invoke_json(runner, DB + ["data", "entity", "list", "--show-provenance", "--format", "json"])
    alice_row = next(r for r in rows if r["name"] == "Alice")
    assert [e["text"] for e in alice_row["evidence"]] == [
        "Alice is a software engineer.", "Alice was later promoted to team lead."
    ]


def test_entity_property_extraction_add_attaches_additional_text(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + [
        "schema", "class", "property", "add", "AGE", "Age in years", "--class", "EMPLOYEE",
    ])
    invoke(runner, DB + [
        "data", "entity", "property", "set", "28",
        "--entity", "Alice", "--property", "AGE", "--type", "NUMBER",
    ])

    invoke(runner, DB + [
        "data", "entity", "property", "extraction", "add",
        "--entity", "Alice", "--property", "AGE",
        "--text", "Alice turned 28 last month.",
    ])

    rows = invoke_json(runner, DB + [
        "data", "entity", "property", "list", "--entity", "Alice", "--show-provenance", "--format", "json",
    ])
    age_row = next(r for r in rows if r["property"] == "AGE")
    assert "Alice turned 28 last month." in [e["text"] for e in age_row["evidence"]]


def test_fact_extraction_add_attaches_additional_text(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
        "--text", "Alice joined IBM in June 1998.",
    ])

    rows = invoke_json(runner, DB + ["data", "fact", "list", "--format", "json"])
    fact_id = rows[0]["id"]

    invoke(runner, DB + [
        "data", "fact", "extraction", "add",
        "--fact-id", fact_id,
        "--text", "Alice was promoted while at IBM.",
    ])

    rows = invoke_json(runner, DB + ["data", "fact", "list", "--show-provenance", "--format", "json"])
    assert "Alice was promoted while at IBM." in [e["text"] for e in rows[0]["evidence"]]


def test_fact_property_extraction_add_attaches_additional_text(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
    ])
    fact_rows = invoke_json(runner, DB + ["data", "fact", "list", "--format", "json"])
    fact_id = fact_rows[0]["id"]
    invoke(runner, DB + [
        "data", "fact", "property", "set", "1998-06-01",
        "--fact-id", fact_id, "--property", "START_DATE", "--type", "DATE",
    ])

    invoke(runner, DB + [
        "data", "fact", "property", "extraction", "add",
        "--fact-id", fact_id, "--property", "START_DATE",
        "--text", "The start date was confirmed as June 1998.",
    ])

    rows = invoke_json(runner, DB + [
        "data", "fact", "property", "list", "--fact-id", fact_id, "--show-provenance", "--format", "json",
    ])
    start_date_row = next(r for r in rows if r["property"] == "START_DATE")
    assert "The start date was confirmed as June 1998." in [
        e["text"] for e in start_date_row["evidence"]
    ]


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
    assert rows == [{"id": fact_id, "head": "Alice", "relation": "WORKED_AT", "tail": "IBM",
                     "confidence": None}]

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


def test_entity_class_attach_and_list(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human individual"])
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "An employed person"])
    invoke(runner, DB + ["data", "entity", "add", "--name", "Alice", "--class", "PERSON"])

    result = invoke(runner, DB + [
        "data", "entity", "class", "attach", "--entity", "Alice", "--class", "EMPLOYEE",
    ])
    assert "attached" in result.output
    rows = invoke_json(runner, DB + [
        "data", "entity", "class", "list", "--entity", "Alice", "--format", "json",
    ])
    assert {r["name"] for r in rows} == {"PERSON", "EMPLOYEE"}


def test_entity_class_attach_is_deduplicated(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human individual"])
    invoke(runner, DB + ["data", "entity", "add", "--name", "Alice", "--class", "PERSON"])

    invoke(runner, DB + [
        "data", "entity", "class", "attach", "--entity", "Alice", "--class", "PERSON",
    ])
    rows = invoke_json(runner, DB + [
        "data", "entity", "class", "list", "--entity", "Alice", "--format", "json",
    ])
    assert [r["name"] for r in rows] == ["PERSON"]


def test_entity_class_attach_unknown_class_errors(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human individual"])
    invoke(runner, DB + ["data", "entity", "add", "--name", "Alice", "--class", "PERSON"])

    result = runner.invoke(cli, DB + [
        "data", "entity", "class", "attach", "--entity", "Alice", "--class", "NOPE",
    ])
    assert result.exit_code != 0
    assert "NOPE" in result.output


def test_entity_class_list_table_shows_the_attached_class(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human individual"])
    invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "An employed person"])
    invoke(runner, DB + ["data", "entity", "add", "--name", "Alice", "--class", "PERSON"])
    invoke(runner, DB + [
        "data", "entity", "class", "attach", "--entity", "Alice", "--class", "EMPLOYEE",
    ])
    result = invoke(runner, DB + ["data", "entity", "class", "list", "--entity", "Alice"])
    assert "PERSON" in result.output
    assert "EMPLOYEE" in result.output


def test_entity_class_attach_by_ids(runner):
    class_out = invoke(runner, DB + ["schema", "class", "add", "EMPLOYEE", "An employed person"])
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human individual"])
    entity_out = invoke(runner, DB + [
        "data", "entity", "add", "--name", "Alice", "--class", "PERSON",
    ])
    class_id, entity_id = _extract_id(class_out.output), _extract_id(entity_out.output)

    invoke(runner, DB + [
        "data", "entity", "class", "attach", "--entity-id", entity_id, "--class-id", class_id,
    ])
    rows = invoke_json(runner, DB + [
        "data", "entity", "class", "list", "--entity-id", entity_id, "--format", "json",
    ])
    assert {r["name"] for r in rows} == {"PERSON", "EMPLOYEE"}


def test_fact_add_confidence_shows_in_list(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
        "--text", "Alice joined IBM in June 1998.", "--confidence", "0.9",
    ])
    rows = invoke_json(runner, DB + ["data", "fact", "list", "--format", "json"])
    assert [r["confidence"] for r in rows] == [0.9]
    result = invoke(runner, DB + ["data", "fact", "list"])
    assert "Conf." in result.output
    assert "0.9" in result.output


def test_fact_confidence_rises_with_stronger_evidence(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
        "--text", "Alice joined IBM.", "--confidence", "0.4",
    ])
    invoke(runner, DB + [
        "data", "fact", "extraction", "add", "--fact", "Alice_WORKED_AT_IBM",
        "--text", "Confirmed by the resume.", "--confidence", "0.9",
        "--extraction-type", "external_record",
    ])
    rows = invoke_json(runner, DB + ["data", "fact", "list", "--format", "json"])
    assert [r["confidence"] for r in rows] == [0.9]


def test_confidence_without_text_is_rejected(runner):
    _seed_schema_and_entities(runner)
    result = runner.invoke(cli, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
        "--confidence", "0.9",
    ])
    assert result.exit_code != 0
    assert "confidence requires text" in result.output


def test_confidence_out_of_range_is_rejected(runner):
    _seed_schema_and_entities(runner)
    result = runner.invoke(cli, DB + [
        "data", "entity", "add", "--name", "Carol", "--class", "EMPLOYEE",
        "--text", "Carol exists.", "--confidence", "1.5",
    ])
    assert result.exit_code != 0
    assert "[0.0, 1.0]" in result.output


def test_extraction_type_reaches_the_stored_extraction(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + [
        "data", "entity", "extraction", "add", "--entity", "Alice",
        "--text", "Q42 occupation engineer", "--extraction-type", "external_record",
    ])
    kb = _make_kb("main")
    types = {
        n["properties"].get("extraction_type")
        for n in kb.match_nodes("main", label="EXTRACTION")
    }
    assert "external_record" in types


def test_unknown_extraction_type_is_rejected(runner):
    _seed_schema_and_entities(runner)
    result = runner.invoke(cli, DB + [
        "data", "entity", "extraction", "add", "--entity", "Alice",
        "--text", "More evidence.", "--extraction-type", "text-span",
    ])
    assert result.exit_code != 0
    assert "text_span" in result.output


def test_detailed_list_shows_fact_confidence(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
        "--text", "Alice joined IBM.", "--confidence", "0.7",
    ])
    result = invoke(runner, DB + ["data", "list", "--show-detailed"])
    assert "Conf." in result.output
    assert "0.7" in result.output


def test_property_commands_thread_confidence_and_type(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + [
        "schema", "class", "property", "add", "AGE", "Age of a person", "--class", "EMPLOYEE",
    ])
    invoke(runner, DB + [
        "data", "entity", "property", "set", "30", "--entity", "Alice", "--property", "AGE",
        "--type", "NUMBER", "--text", "Alice is 30.", "--confidence", "0.8",
        "--extraction-type", "external_record",
    ])
    result = invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
    ])
    fact_id = _extract_id(result.output)
    invoke(runner, DB + [
        "data", "fact", "property", "set", "1998-06-01", "--fact-id", fact_id,
        "--property", "START_DATE", "--text", "Joined in June 1998.", "--confidence", "0.5",
    ])
    invoke(runner, DB + [
        "data", "fact", "property", "extraction", "add", "--fact-id", fact_id,
        "--property", "START_DATE", "--text", "The resume says June 1998.",
        "--confidence", "0.7", "--extraction-type", "assertion",
    ])
    invoke(runner, DB + [
        "data", "entity", "property", "extraction", "add", "--entity", "Alice",
        "--property", "AGE", "--text", "Her profile lists 30.",
        "--confidence", "0.6", "--extraction-type", "external_record",
    ])
    kb = _make_kb("main")
    scored = {
        (n["properties"].get("confidence"), n["properties"].get("extraction_type"))
        for n in kb.match_nodes("main", label="EXTRACTION")
    }
    assert (0.8, "external_record") in scored   # entity property set
    assert (0.5, "text_span") in scored         # fact property set
    assert (0.7, "assertion") in scored         # fact property extraction add
    assert (0.6, "external_record") in scored   # entity property extraction add


def test_unscored_fact_is_marked_not_printed_as_none(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
        "--text", "No score stated.",
    ])
    result = invoke(runner, DB + ["data", "fact", "list"])
    assert "Conf." in result.output
    assert "--" in result.output      # same marker the per-evidence column uses
    assert "None" not in result.output


def test_show_provenance_expands_one_row_per_evidence(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + ["data", "source", "add", "resume.pdf", "--name", "resume"])
    invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
        "--text", "Alice joined IBM.", "--source", "resume", "--confidence", "0.9",
    ])
    invoke(runner, DB + [
        "data", "fact", "extraction", "add", "--fact", "Alice_WORKED_AT_IBM",
        "--text", "Q937 employer IBM", "--confidence", "0.6",
    ])
    invoke(runner, DB + [
        "data", "fact", "extraction", "add", "--fact", "Alice_WORKED_AT_IBM",
        "--text", "A colleague said so.",
    ])
    result = invoke(runner, DB + ["data", "fact", "list", "--show-provenance"])
    for text in ("Alice joined IBM.", "Q937 employer IBM", "A colleague said so."):
        assert text in result.output
    assert "0.6" in result.output          # this piece's own score, not just the fact's
    assert "--" in result.output           # the unscored piece is marked, not blank
    assert result.output.count("resume") == 1   # named on its own record, not on all three
    assert result.output.count("WORKED_AT") == 1   # the fact is named once, not per row


def test_row_without_evidence_still_lists(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
    ])
    result = invoke(runner, DB + ["data", "fact", "list", "--show-provenance"])
    assert "WORKED_AT" in result.output


def test_detailed_provenance_shows_each_records_source_date(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + ["data", "source", "add", "resume.pdf", "--name", "cv",
                         "--original", "1998"])
    invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
        "--text", "Joined IBM.", "--source", "cv", "--confidence", "0.9",
    ])
    result = invoke(runner, DB + ["data", "list", "--show-provenance", "--show-detailed"])
    assert "1998" in result.output      # the record's own source date, not the fact's timestamps


def test_metadata_table_shows_one_row_per_evidence():
    metadata = {"n1": {
        "type": "FACT",
        "name": "Alice_WORKED_AT_IBM",
        "created": None,
        "modified": None,
        "evidence": [
            {"text": "Alpha", "confidence": 0.9, "source": "cv", "source_original": "1998"},
            {"text": "Beta", "confidence": None, "source": None, "source_original": None},
        ],
    }}
    with console.capture() as captured:
        _print_metadata_table(metadata)
    out = captured.get()
    # single-token values: Rich wraps long cells at 80 columns and would split them
    assert "Alpha" in out and "Beta" in out
    assert "0.9" in out and "--" in out
    assert out.count("Alice") == 1   # the item is named once, not per evidence row


def test_row_rules_appear_only_in_the_expanded_view(runner):
    _seed_schema_and_entities(runner)
    invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "IBM",
        "--text", "Joined IBM.", "--confidence", "0.9",
    ])
    invoke(runner, DB + [
        "data", "fact", "extraction", "add", "--fact", "Alice_WORKED_AT_IBM",
        "--text", "Per LinkedIn.",
    ])
    # a second fact, so the plain listing has two rows and a rule would show if drawn
    invoke(runner, DB + ["data", "entity", "add", "--name", "ACME", "--class", "COMPANY"])
    invoke(runner, DB + [
        "data", "fact", "add", "--head", "Alice", "--relation", "WORKED_AT", "--tail", "ACME",
    ])
    plain = invoke(runner, DB + ["data", "fact", "list"])
    expanded = invoke(runner, DB + ["data", "fact", "list", "--show-provenance"])
    assert "\u251c" not in plain.output       # no rule between records when not expanded
    assert "\u251c" in expanded.output


def test_every_listing_expands_its_table_not_just_its_json(runner):
    """The JSON path builds rows before rendering, so a blank table can pass a JSON test."""
    _seed_schema_and_entities(runner)
    invoke(runner, DB + ["data", "source", "add", "wikidata", "--name", "wd"])
    invoke(runner, DB + [
        "data", "entity", "extraction", "add", "--entity", "Alice",
        "--text", "Beta", "--source", "wd", "--confidence", "0.6",
    ])
    invoke(runner, DB + [
        "schema", "class", "property", "add", "AGE", "Age", "--class", "EMPLOYEE",
    ])
    invoke(runner, DB + [
        "data", "entity", "property", "set", "30", "--entity", "Alice", "--property", "AGE",
        "--type", "NUMBER", "--text", "Gamma", "--confidence", "0.8",
    ])
    result = invoke(runner, DB + ["data", "fact", "add", "--head", "Alice",
                                  "--relation", "WORKED_AT", "--tail", "IBM"])
    fact_id = _extract_id(result.output)
    invoke(runner, DB + [
        "data", "fact", "property", "set", "1998-06-01", "--fact-id", fact_id,
        "--property", "START_DATE", "--text", "Delta", "--confidence", "0.5",
    ])

    entities = invoke(runner, DB + ["data", "entity", "list", "--show-provenance"])
    assert "Beta" in entities.output and "0.6" in entities.output

    ent_props = invoke(runner, DB + [
        "data", "entity", "property", "list", "--entity", "Alice", "--show-provenance",
    ])
    assert "Gamma" in ent_props.output and "0.8" in ent_props.output

    fact_props = invoke(runner, DB + [
        "data", "fact", "property", "list", "--fact-id", fact_id, "--show-provenance",
    ])
    assert "Delta" in fact_props.output and "0.5" in fact_props.output


# ------------------------------------------------------------------ #
# Data asserts and bulk ingest                                         #
# ------------------------------------------------------------------ #

NONE_MODE = ["--schema-creation-mode", "none"]


def test_entity_assert_creates_with_everything_then_reuses(runner):
    first = invoke(runner, DB + ["data", "entity", "assert", "Zorbo"] + NONE_MODE)
    assert "created" in first.output
    assert "EVERYTHING" in first.output
    again = invoke(runner, DB + ["data", "entity", "assert", "Zorbo"] + NONE_MODE)
    assert "reused, exact match" in again.output


def test_fact_assert_then_dedupes(runner):
    args = DB + ["data", "fact", "assert", "--head", "Zorbo", "--relation", "KNOWS",
                 "--tail", "Blarp"] + NONE_MODE
    first = invoke(runner, args)
    assert "created" in first.output
    again = invoke(runner, args)
    assert "deduped, evidence attached" in again.output


def test_entity_property_assert_then_dedupes(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human"])
    invoke(runner, DB + ["data", "entity", "add", "--name", "Zorbo", "--class", "PERSON"])
    args = DB + ["data", "entity", "property", "assert", "--entity", "Zorbo",
                 "--property", "AGE", "--value", "30"] + NONE_MODE
    first = invoke(runner, args)
    assert "created" in first.output
    again = invoke(runner, args)
    assert "deduped, evidence attached" in again.output


def test_fact_property_assert_on_the_triple(runner):
    invoke(runner, DB + ["data", "fact", "assert", "--head", "Zorbo", "--relation", "KNOWS",
                         "--tail", "Blarp"] + NONE_MODE)
    result = invoke(runner, DB + ["data", "fact", "property", "assert",
                                  "--head", "Zorbo", "--relation", "KNOWS", "--tail", "Blarp",
                                  "--property", "SINCE", "--value", "1998"] + NONE_MODE)
    assert "created" in result.output


def test_ambiguous_assert_fails_with_candidates_in_the_message(runner):
    invoke(runner, DB + ["schema", "class", "add", "ALPHA_THING", "carries the beta token"])
    invoke(runner, DB + ["schema", "class", "add", "GAMMA_THING", "also the beta token"])
    result = runner.invoke(cli, DB + ["schema", "class", "assert", "beta"] + NONE_MODE)
    assert result.exit_code != 0
    assert "ambiguous between" in result.output
    assert "ALPHA_THING" in result.output


def test_ingest_file_report_and_json_format(runner, tmp_path):
    payload = {
        "classes": [{"name": "PERSON"}],
        "entities": [{"name": "Zorbo", "class_name": "PERSON"}],
        "entity_properties": [{"entity": "Zorbo", "property": "AGE", "value": "30"}],
    }
    path = tmp_path / "payload.json"
    path.write_text(json.dumps(payload))
    result = invoke(runner, DB + ["data", "ingest", str(path)] + NONE_MODE)
    assert "Ingested 3 rows" in result.output
    assert "written 3" in result.output
    assert "created:" in result.output
    # Re-ingest from stdin with --format json: converged counts, machine-readable.
    again = runner.invoke(cli, DB + ["data", "ingest", "-", "--format", "json"] + NONE_MODE,
                          input=json.dumps(payload))
    assert again.exit_code == 0, again.output
    report = json.loads(again.output)
    assert (report["written"], report["deduped"], report["skipped"]) == (0, 1, 2)


def test_ingest_failures_render_as_a_table(runner, tmp_path):
    payload = {"classes": [{"name": "SOLID"}, {"name": "BROKEN", "bogus": 1}]}
    path = tmp_path / "payload.json"
    path.write_text(json.dumps(payload))
    result = invoke(runner, DB + ["data", "ingest", str(path)] + NONE_MODE)
    assert "written 1" in result.output
    assert "failed 1" in result.output
    assert "Failures" in result.output
    assert "classes" in result.output


def test_assert_source_and_confidence_reach_the_listing(runner):
    invoke(runner, DB + ["data", "source", "add", "wikidata", "--name", "Wikidata"])
    invoke(runner, DB + ["data", "fact", "assert", "--head", "Zorbo", "--relation", "KNOWS",
                         "--tail", "Blarp", "--source", "Wikidata", "--confidence", "0.9"]
           + NONE_MODE)
    result = invoke(runner, DB + ["data", "fact", "list", "--show-provenance"])
    assert "0.9" in result.output
    assert "Wikidata" in result.output


def test_assert_records_assertion_typed_evidence_by_default(runner):
    invoke(runner, DB + ["schema", "class", "assert", "PERSON"] + NONE_MODE)
    kb = _make_kb("main")
    types = [n["properties"].get("extraction_type")
             for n in kb.match_nodes("main", label="EXTRACTION")]
    assert types == ["assertion"]


def test_property_assert_coerces_typed_values(runner):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human"])
    invoke(runner, DB + ["data", "entity", "add", "--name", "Zorbo", "--class", "PERSON"])
    invoke(runner, DB + ["data", "entity", "property", "assert", "--entity", "Zorbo",
                         "--property", "AGE", "--value", "30", "--type", "NUMBER"] + NONE_MODE)
    kb = _make_kb("main")
    literals = list(kb.match_nodes("main", label="LITERAL"))
    # The literal's `name` is always str(value), but `value` keeps the coerced
    # type: an uncoerced pass-through would store the string "30" here.
    assert [n["properties"]["value"] for n in literals] == [30]
    assert [n["properties"]["type"] for n in literals] == ["NUMBER"]


def test_ingest_reports_widened_relations(runner, tmp_path):
    invoke(runner, DB + ["schema", "class", "add", "PERSON", "A human"])
    invoke(runner, DB + ["schema", "class", "add", "COMPANY", "A business"])
    invoke(runner, DB + ["schema", "class", "add", "AGENCY", "A public body"])
    invoke(runner, DB + ["schema", "relation", "add", "WORKED_AT", "Employment",
                         "--domain", "PERSON", "--range", "COMPANY"])
    invoke(runner, DB + ["data", "entity", "add", "--name", "Bob", "--class", "AGENCY"])
    invoke(runner, DB + ["data", "entity", "add", "--name", "IBM", "--class", "COMPANY"])
    payload = {"facts": [{"head": "Bob", "relation": "WORKED_AT", "tail": "IBM"}]}
    path = tmp_path / "widen.json"
    path.write_text(json.dumps(payload))
    result = invoke(runner, DB + ["data", "ingest", str(path)] + NONE_MODE)
    assert "widened: WORKED_AT" in result.output
