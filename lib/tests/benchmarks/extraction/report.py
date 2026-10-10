"""Re-render the Markdown report of a past extraction benchmark run from its saved
files — the score JSON and the detailed JSONL `run.py` writes — without re-running it.

Usage (from `lib/`):
    python -m tests.benchmarks.extraction.report .reports/live-<stamp>.json
    python -m tests.benchmarks.extraction.report <scores.json> --detail <detail.jsonl> --out report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from ..common import scoring
from ..common.cases import load_cases
from ..common.reporting import write_markdown_report
from ..common.runner import StepResult

CASES_DIR = Path(__file__).parent / "cases"


def _score(d: dict, category: str) -> scoring.CaseScore:
    return scoring.CaseScore(
        case_id=d["case_id"], model=d["model"], dedup_mode=d["dedup_mode"],
        entities=scoring.PRF1(**d["entities"]), facts=scoring.PRF1(**d["facts"]),
        properties=scoring.PRF1(**d["properties"]),
        schema_reuse=scoring.SchemaReuseResult(**d["schema_reuse"]),
        normalization=scoring.NormalizationResult(**d["normalization"]),
        provenance=scoring.ProvenanceResult(**d["provenance"]),
        domain_range_compliance=scoring.ComplianceResult(**d["domain_range_compliance"]),
        tool_call_count=d["tool_call_count"], error_count=d["error_count"],
        prompt_tokens=d["prompt_tokens"], completion_tokens=d["completion_tokens"],
        latency_seconds=d["latency_seconds"],
        alignment={k: scoring.Alignment(**{f: [tuple(x) if isinstance(x, list) else x for x in v]
                                           for f, v in a.items()})
                   for k, a in d.get("alignment", {}).items()},
        category=d.get("category", category),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scores", type=Path, help="score JSON written by run.py")
    parser.add_argument("--detail", type=Path, default=None,
                        help="detailed JSONL (default: <scores>.detail.jsonl beside it)")
    parser.add_argument("--out", type=Path, default=None, help="Markdown path (default: <scores>.md)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    detail = args.detail or args.scores.with_name(args.scores.stem + ".detail.jsonl")
    out = args.out or args.scores.with_suffix(".md")

    cases = {c.id: c for c in load_cases(CASES_DIR)}
    scores = [_score(d, cases[d["case_id"]].category) for d in json.loads(args.scores.read_text())]
    results = []
    for line in detail.read_text().splitlines():
        r = json.loads(line)
        results.append(SimpleNamespace(
            case=cases[r["case_id"]], model=r["model"],
            dedup_mode=r.get("dedup_mode", r.get("schema_discovery_mode")),
            steps=[StepResult(**st) for st in r.get("steps", [])],
        ))
    write_markdown_report(results, scores, out)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
