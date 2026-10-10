"""Extraction benchmark run history (#134): `results/history.jsonl`, one summary
line per run, and `results/README.md` rendered from it with trend charts. Both
are committed; the per-run detail under `.reports/` is not.

`run.py` appends to the history at the end of every full-suite live run. This
module is for the rest:

Usage (from `lib/`):
    python -m tests.benchmarks.extraction.history add .reports/live-<stamp>.json --note "baseline"
    python -m tests.benchmarks.extraction.history render
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..common import history
from ..common.cases import load_cases
from .report import CASES_DIR, _score

RESULTS_DIR = Path(__file__).parent / "results"
HISTORY = RESULTS_DIR / "history.jsonl"
PAGE = RESULTS_DIR / "README.md"

HOW_TO = """
Each full live run of the extraction benchmark (`python -m tests.benchmarks.extraction.run`, from
`lib/`) appends one summary line to `history.jsonl` and regenerates this page; commit both to
record the run. Partial runs (`--case`, `--mode`) are not recorded. The detailed per-stage report
of a run stays in `.reports/` (gitignored). To record a past run or re-render this page:
`python -m tests.benchmarks.extraction.history add <scores.json> [--note ...]` and
`python -m tests.benchmarks.extraction.history render`.
"""


def record(summary: dict, history_path: Path = HISTORY, page_path: Path = PAGE) -> None:
    history.append(history_path, summary)
    render(history_path, page_path)


def render(history_path: Path = HISTORY, page_path: Path = PAGE) -> None:
    page_path.write_text(history.render(history.load(history_path),
                                        title="Extraction benchmark results", how_to=HOW_TO))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="append a past run's score JSON to the history")
    add.add_argument("scores", type=Path)
    add.add_argument("--note", default="")
    add.add_argument("--judge-model", default="gpt-4o-mini")
    add.add_argument("--run-id", default=None, help="default: the live-<stamp> of the file name")
    add.add_argument("--commit", default=None, help="commit the run was made at (default: HEAD, marked * if dirty)")
    sub.add_parser("render", help="regenerate results/README.md from history.jsonl")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.cmd == "add":
        cases = {c.id: c for c in load_cases(CASES_DIR)}
        scores = [_score(d, cases[d["case_id"]].category) for d in json.loads(args.scores.read_text())]
        run_id = args.run_id or (args.scores.stem.removeprefix("live-") if args.scores.stem.startswith("live-") else None)
        record(history.summarize(scores, judge_model=args.judge_model, note=args.note, run_id=run_id,
                                       commit=args.commit))
    else:
        render()
    print(f"Wrote {PAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
