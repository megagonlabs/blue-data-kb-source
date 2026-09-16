"""`kb profile` — the command group, and the configuration it drives."""
import json
from pathlib import Path

from blue_data_kb_cli.commands import cli
from blue_data_kb_cli.profile import cli_spec

from .conftest import invoke


def profiles_file(tmp_path) -> Path:
    return tmp_path / ".profiles"


# ------------------------------------------------------------------ #
# spec                                                                #
# ------------------------------------------------------------------ #

def test_spec_merges_the_family_parameters_with_this_cli_s_own():
    spec = cli_spec()
    assert "OPENAI_API_KEY" in spec                      # family
    assert "BLUE_DATA_CLI_FORMAT" in spec                # family
    assert "BLUE_DATA_KB_SOURCE_DATA_DIR" in spec        # this CLI
    assert "BLUE_DATA_KB_SOURCE_EXTRACTION_LLM_MODEL" in spec


def test_only_four_parameters_are_asked_on_a_first_run():
    """`kb profile config` walks the required set; keeping it small is the
    difference between a usable first run and an interrogation."""
    assert [p.name for p in cli_spec().required()] == [
        "OPENAI_API_KEY",
        "BLUE_DATA_LLM_MODEL",
        "BLUE_DATA_KB_SOURCE_DATA_DIR",
        "BLUE_DATA_KB_SOURCE_DATABASE",
    ]


def test_every_llm_model_parameter_inherits_toward_the_family_default():
    spec = cli_spec()
    for fn in ("EXTRACTION", "QUERY", "VALIDATION", "SCHEMA_EXPANSION", "INGESTION", "ASSERTION"):
        param = spec[f"BLUE_DATA_KB_SOURCE_{fn}_LLM_MODEL"]
        assert param.inherits == ("BLUE_DATA_KB_SOURCE_LLM_MODEL", "BLUE_DATA_LLM_MODEL")


# ------------------------------------------------------------------ #
# add / list / select / remove                                        #
# ------------------------------------------------------------------ #

def test_add_creates_a_profile_and_list_shows_it(runner, tmp_path):
    invoke(runner, ["profile", "add", "staging"])
    out = json.loads(invoke(runner, ["profile", "list", "--format", "json"]).output)
    assert "staging" in out["profiles"]


def test_adding_a_duplicate_profile_fails_cleanly(runner):
    invoke(runner, ["profile", "add", "staging"])
    result = runner.invoke(cli, ["profile", "add", "staging"])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_select_changes_the_profile_subsequent_commands_use(runner):
    invoke(runner, ["profile", "add", "staging"])
    invoke(runner, ["profile", "select", "staging"])
    out = json.loads(invoke(runner, ["profile", "list", "--format", "json"]).output)
    assert out["selected"] == "staging"


def test_selecting_an_unknown_profile_fails_cleanly(runner):
    result = runner.invoke(cli, ["profile", "select", "ghost"])
    assert result.exit_code != 0
    assert "no such profile" in result.output


def test_remove_requires_confirmation(runner):
    invoke(runner, ["profile", "add", "staging"])
    result = runner.invoke(cli, ["profile", "remove", "staging"], input="n\n")
    assert result.exit_code != 0
    out = json.loads(invoke(runner, ["profile", "list", "--format", "json"]).output)
    assert "staging" in out["profiles"]


def test_remove_with_confirm_is_non_interactive(runner):
    invoke(runner, ["profile", "add", "staging"])
    invoke(runner, ["profile", "remove", "staging", "--confirm"])
    out = json.loads(invoke(runner, ["profile", "list", "--format", "json"]).output)
    assert "staging" not in out["profiles"]


# ------------------------------------------------------------------ #
# config                                                              #
# ------------------------------------------------------------------ #

def test_config_with_a_param_and_value_sets_it_directly(runner, tmp_path):
    invoke(runner, ["profile", "config", "BLUE_DATA_LLM_MODEL", "gpt-5"])
    assert "gpt-5" in profiles_file(tmp_path).read_text()


def test_config_with_only_a_param_prompts_for_it(runner, tmp_path):
    result = runner.invoke(
        cli, ["profile", "config", "BLUE_DATA_LLM_MODEL"], input="gpt-5\n"
    )
    assert result.exit_code == 0, result.output
    assert "gpt-5" in profiles_file(tmp_path).read_text()


def test_config_with_no_param_walks_every_required_parameter(runner, tmp_path):
    n = len(cli_spec().required())
    result = runner.invoke(cli, ["profile", "config"], input="\n" * n)
    assert result.exit_code == 0, result.output
    # Each required parameter's prompt text appears once.
    for p in cli_spec().required():
        assert p.prompt.split("(")[0].strip()[:20] in result.output


def test_config_rejects_an_unknown_parameter(runner):
    result = runner.invoke(cli, ["profile", "config", "NOT_A_PARAM", "x"])
    assert result.exit_code != 0
    assert "unknown configuration parameter" in result.output


def test_config_validates_against_the_cast(runner):
    result = runner.invoke(
        cli, ["profile", "config", "BLUE_DATA_KB_SOURCE_EXTRACTION_MAX_TURNS", "not-a-number"]
    )
    assert result.exit_code != 0
    assert "BLUE_DATA_KB_SOURCE_EXTRACTION_MAX_TURNS" in result.output


def test_config_writes_to_the_named_profile_not_the_selected_one(runner, tmp_path):
    invoke(runner, ["profile", "add", "staging"])
    invoke(runner, ["profile", "config", "--profile", "staging", "BLUE_DATA_LLM_MODEL", "gpt-5"])
    text = profiles_file(tmp_path).read_text()
    assert text.index("[staging]") < text.index("gpt-5")


# ------------------------------------------------------------------ #
# secrets                                                             #
# ------------------------------------------------------------------ #

def test_a_secret_cannot_be_passed_on_the_command_line(runner):
    """It would land in shell history — the issue is explicit about this."""
    result = runner.invoke(cli, ["profile", "config", "OPENAI_API_KEY", "sk-leaked"])
    assert result.exit_code != 0
    assert "shell history" in result.output


def test_a_prompted_secret_is_not_stored_in_the_clear(runner, tmp_path):
    result = runner.invoke(cli, ["profile", "config", "OPENAI_API_KEY"], input="sk-secret-value\n")
    assert result.exit_code == 0, result.output
    stored = profiles_file(tmp_path).read_text()
    assert "sk-secret-value" not in stored
    assert "enc:" in stored


def test_a_secret_can_be_stored_as_an_environment_reference(runner, tmp_path):
    result = runner.invoke(cli, ["profile", "config", "OPENAI_API_KEY"], input="$MY_KEY\n")
    assert result.exit_code == 0, result.output
    assert "$MY_KEY" in profiles_file(tmp_path).read_text()


def test_inspect_masks_a_stored_secret(runner):
    runner.invoke(cli, ["profile", "config", "OPENAI_API_KEY"], input="sk-secret-value\n")
    out = invoke(runner, ["profile", "inspect", "--format", "json"]).output
    assert "sk-secret-value" not in out
    assert "***" in out


def test_inspect_names_a_referenced_variable_and_whether_it_resolves(runner, monkeypatch):
    runner.invoke(cli, ["profile", "config", "OPENAI_API_KEY"], input="$MY_KEY\n")
    out = json.loads(invoke(runner, ["profile", "inspect", "--format", "json"]).output)
    assert out["values"]["OPENAI_API_KEY"]["value"] == "$MY_KEY (unset)"

    monkeypatch.setenv("MY_KEY", "sk-live")
    out = json.loads(invoke(runner, ["profile", "inspect", "--format", "json"]).output)
    assert out["values"]["OPENAI_API_KEY"]["value"] == "$MY_KEY (set)"


# ------------------------------------------------------------------ #
# inspect / precedence                                                #
# ------------------------------------------------------------------ #

def test_inspect_reports_where_each_value_came_from(runner, monkeypatch):
    invoke(runner, ["profile", "config", "BLUE_DATA_LLM_MODEL", "from-profile"])
    out = json.loads(invoke(runner, ["profile", "inspect", "--format", "json"]).output)
    assert out["values"]["BLUE_DATA_LLM_MODEL"] == {"value": "from-profile", "source": "profile:default"}

    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "from-env")
    out = json.loads(invoke(runner, ["profile", "inspect", "--format", "json"]).output)
    assert out["values"]["BLUE_DATA_LLM_MODEL"] == {"value": "from-env", "source": "environment"}


def test_a_specific_model_falls_back_to_the_kb_wide_one(runner):
    invoke(runner, ["profile", "config", "BLUE_DATA_KB_SOURCE_LLM_MODEL", "kb-wide"])
    out = json.loads(invoke(runner, ["profile", "inspect", "--format", "json"]).output)
    assert out["values"]["BLUE_DATA_KB_SOURCE_EXTRACTION_LLM_MODEL"]["value"] == "kb-wide"

    invoke(runner, ["profile", "config", "BLUE_DATA_KB_SOURCE_EXTRACTION_LLM_MODEL", "extraction-only"])
    out = json.loads(invoke(runner, ["profile", "inspect", "--format", "json"]).output)
    assert out["values"]["BLUE_DATA_KB_SOURCE_EXTRACTION_LLM_MODEL"]["value"] == "extraction-only"
    assert out["values"]["BLUE_DATA_KB_SOURCE_QUERY_LLM_MODEL"]["value"] == "kb-wide"


def _holds_class(tmp_path, database: str, name: str) -> bool:
    """Did `database` actually receive the class?

    Asserting on *where the write landed* rather than on which files exist:
    every invocation materializes whatever database it resolved, so the set of
    files says nothing about which one a given command targeted.
    """
    path = tmp_path / f"{database}.json"
    return path.exists() and name in path.read_text()


def test_the_profile_supplies_the_default_database(runner, tmp_path):
    invoke(runner, ["profile", "config", "BLUE_DATA_KB_SOURCE_DATABASE", "from-profile"])
    invoke(runner, ["schema", "class", "add", "PERSON", "A human"])
    assert _holds_class(tmp_path, "from-profile", "PERSON")


def test_database_select_outranks_the_profile(runner, tmp_path):
    """`database select` is the more specific and more recent act."""
    invoke(runner, ["profile", "config", "BLUE_DATA_KB_SOURCE_DATABASE", "from-profile"])
    invoke(runner, ["database", "select", "chosen"])
    invoke(runner, ["schema", "class", "add", "PERSON", "A human"])
    assert _holds_class(tmp_path, "chosen", "PERSON")
    assert not _holds_class(tmp_path, "from-profile", "PERSON")


def test_the_database_option_outranks_everything(runner, tmp_path):
    invoke(runner, ["profile", "config", "BLUE_DATA_KB_SOURCE_DATABASE", "from-profile"])
    invoke(runner, ["database", "select", "chosen"])
    invoke(runner, ["--database", "explicit", "schema", "class", "add", "PERSON", "A human"])
    assert _holds_class(tmp_path, "explicit", "PERSON")
    assert not _holds_class(tmp_path, "chosen", "PERSON")
    assert not _holds_class(tmp_path, "from-profile", "PERSON")


def test_an_unconfigured_cli_still_runs(runner):
    """A fresh install must not require `profile add` before anything works."""
    invoke(runner, ["schema", "class", "add", "PERSON", "A human"])
    out = json.loads(invoke(runner, ["profile", "inspect", "--format", "json"]).output)
    assert out["values"]["BLUE_DATA_LLM_MODEL"] == {"value": "gpt-4o", "source": "default"}


def test_inspect_secret_display_matches_the_tier_that_won(runner, monkeypatch):
    """Showing the profile's stored reference while reporting 'environment' as
    the source would contradict itself."""
    runner.invoke(cli, ["profile", "config", "OPENAI_API_KEY"], input="$MY_KEY\n")

    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported")
    out = json.loads(invoke(runner, ["profile", "inspect", "--format", "json"]).output)
    assert out["values"]["OPENAI_API_KEY"] == {"value": "***", "source": "environment"}

    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("MY_KEY", "sk-referenced")
    out = json.loads(invoke(runner, ["profile", "inspect", "--format", "json"]).output)
    assert out["values"]["OPENAI_API_KEY"] == {"value": "$MY_KEY (set)", "source": "profile:default"}


def test_extract_options_exist_for_every_extraction_default():
    """The profile supplies defaults; each must be overridable per command."""
    from blue_data_kb_cli.commands import extract_cmd

    options = {p.name for p in extract_cmd.params}
    assert {"effort", "max_concurrency", "max_turns", "related_data_k"} <= options


def test_model_options_no_longer_hardcode_a_default():
    """A hardcoded default would mask the profile for every LLM command."""
    from blue_data_kb_cli.commands import answer_cmd, extract_cmd, validate_cmd

    for command in (extract_cmd, answer_cmd, validate_cmd):
        model = next(p for p in command.params if p.name == "model")
        assert model.default is None, command.name


def test_profile_commands_do_not_create_a_database(runner, tmp_path):
    """Configuration commands have no business touching the graph. The root
    group used to build — and create — the target database on every
    invocation, so `kb profile list` left a default.json behind."""
    invoke(runner, ["profile", "add", "staging"])
    invoke(runner, ["profile", "list"])
    invoke(runner, ["profile", "config", "BLUE_DATA_LLM_MODEL", "gpt-4o"])
    invoke(runner, ["profile", "inspect"])

    assert (tmp_path / ".profiles").exists()
    assert list(tmp_path.glob("*.json")) == []


def test_a_database_command_still_creates_its_database(runner, tmp_path):
    """The other half: laziness must not stop a real command from working."""
    invoke(runner, ["schema", "class", "add", "PERSON", "A human"])
    assert (tmp_path / "default.json").exists()


# ------------------------------------------------------------------ #
# first use                                                           #
# ------------------------------------------------------------------ #

def test_the_first_command_creates_the_default_profile(runner, tmp_path):
    """A fresh install must not tell the user to run `kb profile add` before
    anything works."""
    assert not (tmp_path / ".profiles").exists()
    out = json.loads(invoke(runner, ["profile", "list", "--format", "json"]).output)
    assert out == {"selected": "default", "profiles": ["default"]}


def test_the_created_profile_is_empty_so_values_track_the_spec_defaults(runner):
    """Seeding it with today's defaults would freeze them at install time."""
    out = json.loads(invoke(runner, ["profile", "inspect", "--format", "json"]).output)
    assert out["values"]["BLUE_DATA_LLM_MODEL"] == {"value": "gpt-4o", "source": "default"}


def test_first_use_creates_no_database(runner, tmp_path):
    invoke(runner, ["profile", "list"])
    assert (tmp_path / ".profiles").exists()
    assert list(tmp_path.glob("*.json")) == []


def test_a_removed_default_profile_is_not_resurrected(runner):
    """Initialization is keyed on the profiles file, not the section, so an
    explicit removal sticks."""
    invoke(runner, ["profile", "add", "staging"])
    invoke(runner, ["profile", "select", "staging"])
    invoke(runner, ["profile", "remove", "default", "--confirm"])
    out = json.loads(invoke(runner, ["profile", "list", "--format", "json"]).output)
    assert out["profiles"] == ["staging"]


def test_commands_work_against_a_freshly_created_empty_profile(runner, tmp_path):
    invoke(runner, ["schema", "class", "add", "PERSON", "A human"])
    assert (tmp_path / "default.json").exists()
