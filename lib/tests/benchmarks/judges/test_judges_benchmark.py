"""Report-only judge benchmark, replayed from committed VCR cassettes.

Like the extraction benchmark, this suite never fails on a judgment-quality
regression — placement and naming misses show as FAIL rows in the printed
table, and descriptions are printed for eyeballing; only a harness error
fails. For live runs and to (re)record cassettes use `run.py` here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ..common.judge_cases import cassette_filename, load_cases
from ..common.judge_reporting import render_table, write_json
from ..common.judge_runner import run_judge_case
from ..common.judge_scoring import JudgeScore, score_case

CASES_DIR = Path(__file__).parent / "cases"
CASSETTE_DIR = Path(__file__).parent / "cassettes"
REPORT_PATH = Path(__file__).parent / ".reports" / "latest.json"
# gpt-4o deliberately, not the siblings' gpt-4o-mini: judges default to gpt-4o in
# the library, so the cassettes measure the model the assert path actually uses.
DEFAULT_MODEL = "gpt-4o"

pytestmark = pytest.mark.vcr

_cases = {c.id: c for c in load_cases(CASES_DIR)}
_collected_scores: list[JudgeScore] = []


@pytest.fixture(scope="session", autouse=True)
def _print_and_write_report():
    yield
    if _collected_scores:
        from rich.console import Console

        Console().print(render_table(_collected_scores))
        write_json(_collected_scores, REPORT_PATH)


@pytest.mark.parametrize("case_id", list(_cases), ids=list(_cases))
async def test_case(case_id):
    cassette_path = CASSETTE_DIR / cassette_filename(case_id)
    if not cassette_path.exists():
        pytest.skip(f"no cassette recorded for {cassette_path.name!r} — "
                    "this suite only replays committed cassettes")
    case = _cases[case_id]
    output = await run_judge_case(case, DEFAULT_MODEL)
    _collected_scores.append(score_case(case, DEFAULT_MODEL, output))
