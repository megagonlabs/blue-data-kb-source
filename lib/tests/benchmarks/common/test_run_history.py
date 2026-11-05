"""Offline tests for the querying and judges run history."""
from ..judges import history as judges
from ..querying import history as querying
from . import run_history

_SUITE = run_history.Suite(title="T", how_to="How.", metrics={"a": "metric a", "b": "metric b"})


def _row(key, category, a, b=None, issues=()):
    return {"key": key, "category": category, "a": a, "b": b, "issues": list(issues)}


def _summary(rows, run_id="20261105-101500"):
    return run_history.summarize(rows, _SUITE, models=["m"], run_id=run_id, commit="abc1234")


def test_means_skip_a_metric_that_does_not_apply():
    summary = _summary([_row("x", "c1", 1.0, 0.5), _row("y", "c1", 0.5)])
    assert summary["overall"] == {"a": 0.75, "b": 0.5}
    assert summary["by_category"]["c1"]["runs"] == 2


def test_issues_are_counted_per_run_and_per_category():
    summary = _summary([_row("x", "c1", 1.0, issues=["bad", "worse"]), _row("y", "c2", 1.0)])
    assert summary["with_issues"] == 1
    assert summary["by_category"]["c1"]["with_issues"] == 1
    assert summary["by_category"]["c2"]["with_issues"] == 0


def test_a_metric_no_run_has_shows_a_dash_and_charts_as_zero():
    page = run_history.render([_summary([_row("x", "c1", 1.0)])], _SUITE)
    assert "| **all** | 1.00 | — | 0 |" in page
    assert "    line [0.00]" in page


def test_a_gap_in_a_metric_keeps_the_chart_line_at_its_last_value():
    runs = [_summary([_row("x", "c1", 1.0, 0.4)], "20261105-101500"),
            _summary([_row("x", "c1", 1.0)], "20261105-111500")]
    assert "    line [0.40, 0.40]" in run_history.render(runs, _SUITE)


def test_a_pipe_in_an_issue_cannot_break_the_table():
    page = run_history.render([_summary([_row("x", "c1", 1.0, issues=["a|b"])])], _SUITE)
    assert "a\\|b" in page


def test_an_empty_history_renders():
    assert "_No runs recorded yet._" in run_history.render([], _SUITE)


def test_validate_scores_the_mean_of_supporting_and_counter():
    row = querying.to_row({
        "case_id": "v", "model": "m", "search_mode": "keyword", "operation": "validate",
        "rows": None, "schema_match": None, "supporting": {"f1": 0.0}, "counter": {"f1": 1.0},
        "nl_fidelity": {"faithful": False, "issues": ["omits it"]},
        "error_count": 2, "prompt_tokens": 3, "completion_tokens": 4, "tool_call_count": 5,
    }, "validation")
    assert (row["answer"], row["columns"], row["nl"], row["tokens"]) == (0.5, None, 0.0, 7)
    assert row["issues"] == ["2 failed tool call(s)", "NL answer not faithful: omits it"]


def test_every_querying_case_has_a_category():
    cases = querying.load_cases(querying.CASES_DIR)
    assert cases and all(c.category != "uncategorized" for c in cases)


def test_a_failed_judge_case_names_what_it_produced():
    row = judges.to_row({"case_id": "c", "judge": "placement", "model": "m", "passed": False,
                         "output": "(nothing)", "detail": "wanted {PERSON}"})
    assert (row["passed"], row["issues"]) == (0.0, ["(nothing): wanted {PERSON}"])
