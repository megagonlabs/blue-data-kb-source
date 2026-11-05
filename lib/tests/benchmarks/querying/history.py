"""Querying benchmark run history: `results/history.jsonl`, one summary line per
run, and `results/README.md` rendered from it with trend charts. Both are
committed; the per-run detail under `.reports/` is not.

`run.py` appends to the history at the end of every full-suite live run. This
module is for the rest:

Usage (from `lib/`):
    python -m tests.benchmarks.querying.history add .reports/live-<stamp>.json --note "baseline"
    python -m tests.benchmarks.querying.history render
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..common import run_history
from ..common.query_cases import load_cases

CASES_DIR = Path(__file__).parent / "cases"
RESULTS_DIR = Path(__file__).parent / "results"
HISTORY = RESULTS_DIR / "history.jsonl"
PAGE = RESULTS_DIR / "README.md"

HOW_TO = """
Each full live run of the querying benchmark (`python -m tests.benchmarks.querying.run`, from
`lib/`) appends one summary line to `history.jsonl` and regenerates this page; commit both to
record the run. Partial runs (`--case`, `--search-mode`) are not recorded. To record a past run or
re-render this page: `python -m tests.benchmarks.querying.history add <scores.json> [--note ...]`
and `python -m tests.benchmarks.querying.history render`.

**Answer F1** is the F1 of `answer`'s and `enrich`'s rows against the gold rows, and for `validate`
the mean of the supporting and counter facts' F1. **Column-name F1** is the overlap of `answer`'s
column names with the gold ones. **NL faithful** is the share of natural-language answers that an
LLM judge found true to the gold facts, on the few cases that test them.
"""

SUITE = run_history.Suite(
    title="Querying benchmark results", how_to=HOW_TO,
    metrics={"answer": "answer F1", "columns": "column-name F1", "nl": "NL faithful"},
)


def _f1(prf1: dict | None) -> float | None:
    return prf1["f1"] if prf1 else None


def to_row(score: dict[str, Any], category: str) -> dict[str, Any]:
    """A score from the JSON report, as a run_history row."""
    if score["operation"] == "validate":
        answer = (score["supporting"]["f1"] + score["counter"]["f1"]) / 2
    else:
        answer = score["rows"]["f1"]
    nl = score.get("nl_fidelity")
    issues = []
    if score["error_count"]:
        issues.append(f"{score['error_count']} failed tool call(s)")
    if nl and not nl["faithful"]:
        issues.append("NL answer not faithful: " + "; ".join(nl["issues"]))
    return {
        "key": f"{score['case_id']} · {score['model']} · {score['search_mode']}", "category": category,
        "answer": round(answer, 3), "columns": _f1(score.get("schema_match")),
        "nl": None if nl is None else float(nl["faithful"]), "issues": issues,
        "tokens": score["prompt_tokens"] + score["completion_tokens"], "tool_calls": score["tool_call_count"],
    }


def summarize(scores: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    categories = {c.id: c.category for c in load_cases(CASES_DIR)}
    rows = [to_row(s, categories[s["case_id"]]) for s in scores]
    return run_history.summarize(rows, SUITE, models=[s["model"] for s in scores], **kwargs)


def record(summary: dict[str, Any]) -> None:
    run_history.record(summary, SUITE, HISTORY, PAGE)


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
        run_id = args.run_id or (args.scores.stem.removeprefix("live-") if args.scores.stem.startswith("live-") else None)
        record(summarize(json.loads(args.scores.read_text()), judge_model=args.judge_model, note=args.note,
                         run_id=run_id, commit=args.commit))
    else:
        run_history.write_page(SUITE, HISTORY, PAGE)
    print(f"Wrote {PAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
