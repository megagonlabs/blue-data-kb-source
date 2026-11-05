"""Judge benchmark run history: `results/history.jsonl`, one summary line per run,
and `results/README.md` rendered from it with trend charts. Both are committed;
the per-run report under `.reports/` is not.

`run.py` appends to the history at the end of every full-suite live run. This
module is for the rest:

Usage (from `lib/`):
    python -m tests.benchmarks.judges.history add .reports/live-<stamp>.json --note "baseline"
    python -m tests.benchmarks.judges.history render
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..common import run_history

RESULTS_DIR = Path(__file__).parent / "results"
HISTORY = RESULTS_DIR / "history.jsonl"
PAGE = RESULTS_DIR / "README.md"

HOW_TO = """
Each full live run of the judge benchmark (`python -m tests.benchmarks.judges.run`, from `lib/`)
appends one summary line to `history.jsonl` and regenerates this page; commit both to record the
run. Partial runs (`--case`) are not recorded. To record a past run or re-render this page:
`python -m tests.benchmarks.judges.history add <scores.json> [--note ...]` and
`python -m tests.benchmarks.judges.history render`.

Checks are deterministic. `placement` and `class_name` have exact gold answers. `description` has
guards only (not empty, 3 words or more, 300 characters or fewer), so its pass rate says nothing
about quality: a person reads those in the run's table.
"""

SUITE = run_history.Suite(
    title="Judge benchmark results", how_to=HOW_TO, metrics={"passed": "pass rate"}, group_label="judge",
)


def to_row(score: dict[str, Any]) -> dict[str, Any]:
    """A score from the JSON report, as a run_history row."""
    return {
        "key": f"{score['case_id']} · {score['model']}", "category": score["judge"],
        "passed": float(score["passed"]),
        "issues": [] if score["passed"] else [f"{score['output']}: {score['detail']}"],
    }


def summarize(scores: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    return run_history.summarize([to_row(s) for s in scores], SUITE, models=[s["model"] for s in scores], **kwargs)


def record(summary: dict[str, Any]) -> None:
    run_history.record(summary, SUITE, HISTORY, PAGE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="append a past run's score JSON to the history")
    add.add_argument("scores", type=Path)
    add.add_argument("--note", default="")
    add.add_argument("--run-id", default=None, help="default: the live-<stamp> of the file name")
    add.add_argument("--commit", default=None, help="commit the run was made at (default: HEAD, marked * if dirty)")
    sub.add_parser("render", help="regenerate results/README.md from history.jsonl")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.cmd == "add":
        run_id = args.run_id or (args.scores.stem.removeprefix("live-") if args.scores.stem.startswith("live-") else None)
        record(summarize(json.loads(args.scores.read_text()), note=args.note, run_id=run_id, commit=args.commit))
    else:
        run_history.write_page(SUITE, HISTORY, PAGE)
    print(f"Wrote {PAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
