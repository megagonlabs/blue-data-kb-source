"""Live judge benchmark runner: score the three ingestion judges against the
real OpenAI API, and optionally (re)record the VCR cassettes that
`test_judges_benchmark.py` replays.

Usage (from `lib/`, with OPENAI_API_KEY set):
    python -m tests.benchmarks.judges.run
    python -m tests.benchmarks.judges.run --model gpt-4o --model gpt-4o-mini
    python -m tests.benchmarks.judges.run --case salary_on_employee
    python -m tests.benchmarks.judges.run --record   # refresh the pytest suite's cassettes
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from datetime import datetime
from pathlib import Path

from ..common.judge_cases import cassette_filename, load_cases
from ..common.judge_reporting import render_table, write_json
from ..common.judge_runner import run_judge_case
from ..common.judge_scoring import JudgeScore, score_case

CASES_DIR = Path(__file__).parent / "cases"
REPORTS_DIR = Path(__file__).parent / ".reports"
DEFAULT_MODEL = "gpt-4o"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", action="append", dest="models",
                        help=f"Model to run (repeatable, default: {DEFAULT_MODEL})")
    parser.add_argument("--case", action="append", dest="case_ids",
                        help="Case id to run (repeatable, default: all cases)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Where to write the JSON score report "
                             "(default: .reports/live-<execution timestamp>.json)")
    parser.add_argument("--record", action="store_true",
                        help="Also (re)write the cassette files test_judges_benchmark.py replays")
    return parser.parse_args(argv)


def _cassette_context(case_id: str, record: bool):
    if not record:
        return contextlib.nullcontext()
    import vcr

    cassette_dir = Path(__file__).parent / "cassettes"
    my_vcr = vcr.VCR(cassette_library_dir=str(cassette_dir), record_mode="all",
                     filter_headers=["authorization"])
    return my_vcr.use_cassette(cassette_filename(case_id))


async def _run(args: argparse.Namespace) -> list[JudgeScore]:
    from rich.console import Console

    all_cases = {c.id: c for c in load_cases(CASES_DIR)}
    case_ids = args.case_ids or list(all_cases)
    models = args.models or [DEFAULT_MODEL]
    console = Console()
    scores: list[JudgeScore] = []
    work = [(all_cases[cid], m) for cid in case_ids for m in models]
    for i, (case, model) in enumerate(work, 1):
        console.print(f"[{i}/{len(work)}] {case.id} ({case.judge}, {model})")
        with _cassette_context(case.id, args.record and model == models[0]):
            output = await run_judge_case(case, model)
        scores.append(score_case(case, model, output))
    return scores


def main(argv: list[str]) -> int:
    from rich.console import Console

    args = _parse_args(argv)
    scores = asyncio.run(_run(args))
    Console().print(render_table(scores))
    out = args.out or REPORTS_DIR / f"live-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    write_json(scores, out)
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
