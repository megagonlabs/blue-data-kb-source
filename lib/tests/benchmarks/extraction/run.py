"""Live extraction benchmark runner: compare models/modes across cases against
the real OpenAI API, and optionally (re)record the VCR cassettes that
`test_extraction_benchmark.py` replays.

Usage (from `lib/`, with OPENAI_API_KEY set):
    python -m tests.benchmarks.extraction.run
    python -m tests.benchmarks.extraction.run --model gpt-4o --model gpt-4o-mini
    python -m tests.benchmarks.extraction.run --case schema_reuse_synonym --mode llm
    python -m tests.benchmarks.extraction.run --record   # refresh the pytest suite's cassettes
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from ...conftest import MemoryKBSource
from ..common import runner, scoring
from ..common.cases import Case, cassette_filename, load_cases
from ..common.reporting import render_table, write_detailed_log, write_json

CASES_DIR = Path(__file__).parent / "cases"
REPORTS_DIR = Path(__file__).parent / ".reports"


def _default_out_paths() -> tuple[Path, Path]:
    """Default report paths, timestamped so successive live runs don't clobber each other."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return REPORTS_DIR / f"live-{stamp}.json", REPORTS_DIR / f"live-{stamp}.detail.jsonl"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", action="append", dest="models",
                         help="Model to run (repeatable, default: gpt-4o)")
    parser.add_argument("--case", action="append", dest="case_ids",
                         help="Case id to run (repeatable, default: all cases)")
    parser.add_argument("--mode", action="append", dest="modes", choices=["search", "llm", "none"],
                         help="schema_discovery_mode to run (repeatable, default: each case's own list)")
    parser.add_argument("--judge-model", default="gpt-4o-mini",
                         help="Model used for LLM-judge scoring (default: gpt-4o-mini)")
    parser.add_argument("--out", type=Path, default=None,
                         help="Where to write the JSON score report "
                              "(default: .reports/live-<execution timestamp>.json)")
    parser.add_argument("--detail-out", type=Path, default=None,
                         help="Where to write the detailed prompt/tool-call log "
                              "(default: .reports/live-<execution timestamp>.detail.jsonl)")
    parser.add_argument("--record", action="store_true",
                         help="Also (re)write the cassette files test_extraction_benchmark.py replays")
    return parser.parse_args(argv)


def _cassette_context(case_id: str, mode: str, record: bool):
    if not record:
        return contextlib.nullcontext()
    import vcr

    cassette_dir = Path(__file__).parent / "cassettes"
    my_vcr = vcr.VCR(cassette_library_dir=str(cassette_dir), record_mode="all",
                      filter_headers=["authorization"])
    return my_vcr.use_cassette(cassette_filename(case_id, mode))


async def _run_one(
    case: Case, model: str, mode: str, judge_client, judge_model: str, record: bool
) -> tuple[runner.RunResult, scoring.CaseScore]:
    kb = MemoryKBSource(name="benchmark-live")
    with _cassette_context(case.id, mode, record):
        result = await runner.run_case(kb, case, model, mode)
    score = await scoring.score_case(
        case, model, mode,
        result.collector, result.trace,
        kb, runner.DB,
        judge_client, judge_model,
        result.prompt_tokens, result.completion_tokens, result.latency_seconds,
    )
    return result, score


def _plan_work(args: argparse.Namespace, all_cases: dict[str, Case]) -> list[tuple[Case, str, str]]:
    """Flatten --case/--model/--mode selections into an ordered work list, so
    progress can be reported as a plain x/total count."""
    case_ids = args.case_ids or list(all_cases)
    models = args.models or ["gpt-4o"]
    work: list[tuple[Case, str, str]] = []
    for case_id in case_ids:
        case = all_cases[case_id]
        modes = args.modes or list(case.schema_discovery_modes)
        for model in models:
            for mode in modes:
                work.append((case, model, mode))
    return work


async def _run(args: argparse.Namespace) -> tuple[list[runner.RunResult], list[scoring.CaseScore]]:
    import openai
    from rich.console import Console

    all_cases = {c.id: c for c in load_cases(CASES_DIR)}
    work = _plan_work(args, all_cases)
    judge_client = openai.AsyncOpenAI()
    console = Console()

    total = len(work)
    console.print(f"Running {total} (case, model, mode) combination(s)...")
    results: list[runner.RunResult] = []
    scores: list[scoring.CaseScore] = []
    for i, (case, model, mode) in enumerate(work, start=1):
        console.print(f"[dim]\\[{i}/{total}][/dim] {case.id} · {model} · {mode} ...", end=" ")
        start = time.monotonic()
        try:
            result, score = await _run_one(case, model, mode, judge_client, args.judge_model, args.record)
        except Exception:
            elapsed = time.monotonic() - start
            console.print(f"[red]failed[/red] ({elapsed:.1f}s)")
            raise
        elapsed = time.monotonic() - start
        console.print(f"[green]done[/green] ({elapsed:.1f}s)")
        results.append(result)
        scores.append(score)
    return results, scores


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set; live runs need it.", file=sys.stderr)
        return 1

    default_out, default_detail_out = _default_out_paths()
    out_path = args.out or default_out
    detail_out_path = args.detail_out or default_detail_out

    results, scores = asyncio.run(_run(args))

    from rich.console import Console

    Console().print(render_table(scores))
    write_json(scores, out_path)
    write_detailed_log(results, detail_out_path)
    print(f"Wrote {len(scores)} score(s) to {out_path}")
    print(f"Wrote detailed prompt/tool-call log to {detail_out_path}")
    if args.record:
        print(f"Cassettes (re)written under {Path(__file__).parent / 'cassettes'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
