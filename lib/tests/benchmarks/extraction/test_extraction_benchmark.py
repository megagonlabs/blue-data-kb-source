"""Report-only extraction benchmark, replayed from committed VCR cassettes.

Unlike `lib/tests/unit/`, this suite never fails on a metric regression — it
prints/writes scores so drift is visible, but the harness itself erroring is
the only failure mode. For live, multi-model runs (and to (re)record
cassettes) use `run.py` in this directory instead.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ..common import runner, scoring
from ..common.cases import cassette_filename, case_mode_params, load_cases
from ..common.reporting import render_table, write_detailed_log, write_json

CASES_DIR = Path(__file__).parent / "cases"
CASSETTE_DIR = Path(__file__).parent / "cassettes"
REPORT_PATH = Path(__file__).parent / ".reports" / "latest.json"
DETAIL_LOG_PATH = Path(__file__).parent / ".reports" / "latest.detail.jsonl"
DEFAULT_MODEL = "gpt-4o-mini"

pytestmark = pytest.mark.vcr

_cases = {c.id: c for c in load_cases(CASES_DIR)}
_params = case_mode_params(list(_cases.values()))
_collected_scores: list[scoring.CaseScore] = []
_collected_results: list[runner.RunResult] = []


@pytest.fixture(scope="session", autouse=True)
def _print_and_write_report():
    yield
    if _collected_scores:
        from rich.console import Console

        Console().print(render_table(_collected_scores))
        write_json(_collected_scores, REPORT_PATH)
        write_detailed_log(_collected_results, DETAIL_LOG_PATH)


@pytest.mark.parametrize("case_id,mode", _params, ids=[f"{c}-{m}" for c, m in _params])
async def test_case(kb, case_id, mode):
    cassette_path = CASSETTE_DIR / cassette_filename(case_id, mode)
    if not cassette_path.exists() and not os.environ.get("OPENAI_API_KEY"):
        pytest.skip(f"no cassette recorded for {cassette_path.name!r} and OPENAI_API_KEY is unset")

    import openai

    case = _cases[case_id]
    result = await runner.run_case(kb, case, DEFAULT_MODEL, mode)
    judge_client = openai.AsyncOpenAI()
    score = await scoring.score_case(
        case, DEFAULT_MODEL, mode,
        result.collector, result.trace,
        kb, runner.DB,
        judge_client, DEFAULT_MODEL,
        result.prompt_tokens, result.completion_tokens, result.latency_seconds,
    )
    _collected_scores.append(score)
    _collected_results.append(result)
