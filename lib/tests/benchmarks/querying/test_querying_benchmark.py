"""Report-only querying benchmark (answer/validate/enrich), replayed from
committed VCR cassettes.

Unlike `lib/tests/unit/`, this suite never fails on a metric regression — it
prints/writes scores so drift is visible, but the harness itself erroring is
the only failure mode. For live, multi-model runs (and to (re)record
cassettes) use `run.py` in this directory instead.

Unlike extraction's `test_case`, this doesn't take the shared `blue-kb` fixture —
`run_query_case` builds its own KB per run since the search backend
(`enable_search`/`vector_source`/`embedding_fn`) is a construction-time choice
driven by the case's `search_mode`, not a per-call argument.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ..common import query_runner, query_scoring
from ..common.query_cases import case_search_mode_params, cassette_filename, load_cases
from ..common.query_reporting import render_query_table, write_detailed_log
from ..common.reporting import write_json

CASES_DIR = Path(__file__).parent / "cases"
CASSETTE_DIR = Path(__file__).parent / "cassettes"
REPORT_PATH = Path(__file__).parent / ".reports" / "latest.json"
DETAIL_LOG_PATH = Path(__file__).parent / ".reports" / "latest.detail.jsonl"
DEFAULT_MODEL = "gpt-4o-mini"

pytestmark = pytest.mark.vcr

_cases = {c.id: c for c in load_cases(CASES_DIR)}
_params = case_search_mode_params(list(_cases.values()))
_collected_scores: list[query_scoring.QueryCaseScore] = []
_collected_results: list[query_runner.QueryRunResult] = []


@pytest.fixture(scope="session", autouse=True)
def _print_and_write_report():
    yield
    if _collected_scores:
        from rich.console import Console

        Console().print(render_query_table(_collected_scores))
        write_json(_collected_scores, REPORT_PATH)
        write_detailed_log(_collected_results, DETAIL_LOG_PATH)


@pytest.mark.parametrize("case_id,search_mode", _params, ids=[f"{c}-{m}" for c, m in _params])
async def test_case(case_id, search_mode):
    cassette_path = CASSETTE_DIR / cassette_filename(case_id, search_mode)
    if not cassette_path.exists():
        pytest.skip(f"no cassette recorded for {cassette_path.name!r} — this suite only replays committed cassettes")

    import openai

    case = _cases[case_id]
    result = await query_runner.run_query_case(case, DEFAULT_MODEL, search_mode)
    judge_client = openai.AsyncOpenAI()
    score = await query_scoring.score_query_case(
        case, DEFAULT_MODEL, search_mode, result, judge_client, DEFAULT_MODEL,
    )
    _collected_scores.append(score)
    _collected_results.append(result)
