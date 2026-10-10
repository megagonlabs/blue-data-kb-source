"""Console table and JSON report rendering for benchmark scores, plus a
separate detailed log (full prompts and tool-call inputs/outputs) for
debugging a run beyond what the aggregated score table shows.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from rich.table import Table

from .runner import RunResult
from .scoring import CaseScore


def _fmt_f1(prf1) -> str:
    """F1, then matched/expected and how many were produced — "0.00 (0/0, 2 got)" is two
    false positives where nothing was expected, which "(0/0)" alone hid."""
    return f"{prf1.f1:.2f} ({prf1.matched}/{prf1.expected_count}, {prf1.actual_count} got)"


def _fmt_ok(ok: bool) -> str:
    return "ok" if ok else "FAIL"


def _checks(s: CaseScore) -> str:
    failed = [name for name, ok in (
        ("schema", s.schema_reuse.ok), ("norm", s.normalization.ok),
        ("prov", s.provenance.ok), ("dom/rng", s.domain_range_compliance.ok),
    ) if not ok]
    return "ok" if not failed else "FAIL " + ",".join(failed)


def render_table(scores: list[CaseScore]) -> Table:
    table = Table(title="Extraction benchmark")
    table.add_column("case", overflow="fold", min_width=18)
    for col in ("model", "dedup", "entities F1", "facts F1", "props F1", "checks", "calls", "err", "tokens", "s"):
        table.add_column(col, no_wrap=True)
    for s in scores:
        table.add_row(
            s.case_id, s.model, s.dedup_mode,
            _fmt_f1(s.entities), _fmt_f1(s.facts), _fmt_f1(s.properties),
            _checks(s), str(s.tool_call_count), str(s.error_count),
            f"{(s.prompt_tokens + s.completion_tokens) / 1000:.0f}k", f"{s.latency_seconds:.0f}",
        )
    return table


def write_json(scores: list[CaseScore], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(s) for s in scores], indent=2, default=str))


def write_detailed_log(results: list[RunResult], path: Path) -> None:
    """Write one JSON object per line: the task spec, full LLM prompts/responses,
    the raw tool-call trace, and pre/post KB (schema, data) snapshots for each
    (case, model, mode) run — the detail the score table/JSON report
    intentionally omits.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in results:
            entry = {
                "case_id": r.case.id,
                "model": r.model,
                "dedup_mode": r.dedup_mode,
                "task": {
                    "description": r.case.description,
                    "steps": [asdict(s) for s in r.case.steps],
                    "seed_schema": r.case.seed_schema,
                },
                "expected": asdict(r.case.expected),
                "pre_kb": {"schema": r.pre_schema, "data": r.pre_data},
                "post_kb": {"schema": r.post_schema, "data": r.post_data},
                # Per step: what schema expansion added, then what extraction added.
                "steps": [asdict(st) for st in r.steps],
                "final_state": r.final_state,
                "llm_calls": [asdict(c) for c in r.llm_calls],
                "tool_calls": [asdict(c) for c in r.trace],
            }
            f.write(json.dumps(entry, default=str) + "\n")


# ---------------------------------------------------------------------------
# Markdown run report — what each pass and stage extracted, against the gold
# ---------------------------------------------------------------------------

_KIND_ORDER = ("classes", "relations", "properties", "entities", "facts", "values")


def _md_added(added: dict[str, list[str]], indent: str = "") -> list[str]:
    lines = [f"{indent}- **{kind}**: " + ", ".join(f"`{x}`" for x in added[kind])
             for kind in _KIND_ORDER if added.get(kind)]
    return lines or [f"{indent}- _(nothing)_"]


def _md_seed(seed: dict) -> list[str]:
    lines = []
    for c in seed.get("classes", []):
        props = ", ".join(p["name"] for p in c.get("properties", []))
        lines.append(f"- class `{c['name']}`" + (f" — properties: {props}" if props else ""))
    for r in seed.get("relations", []):
        dom = r.get("domain") if isinstance(r.get("domain"), str) else "|".join(r.get("domain") or [])
        rng = r.get("range") if isinstance(r.get("range"), str) else "|".join(r.get("range") or [])
        props = ", ".join(p["name"] for p in r.get("properties", []))
        lines.append(f"- relation `{dom} -{r['name']}-> {rng}`" + (f" — properties: {props}" if props else ""))
    return lines or ["- _(empty — everything comes from schema expansion)_"]


def _md_outcome(score: CaseScore) -> list[str]:
    lines = ["| | F1 | ✓ matched (expected → extracted) | ✗ missing | + extra |", "|---|---|---|---|---|"]
    for kind, prf1 in (("entities", score.entities), ("facts", score.facts), ("properties", score.properties)):
        al = score.alignment.get(kind)
        if al is None:
            continue
        matched = "<br>".join(f"`{e}` → `{a}`" for e, a in al.matched) or "—"
        missing = "<br>".join(f"`{m}`" for m in al.missing) or "—"
        extra = "<br>".join(f"`{x}`" for x in al.extra) or "—"
        lines.append(f"| **{kind}** | {prf1.f1:.2f} | {matched} | {missing} | {extra} |")
    return lines


def _md_checks(score: CaseScore) -> list[str]:
    problems = []
    sr = score.schema_reuse
    if sr.reused_missing:
        problems.append(f"- schema reuse — declared a new element instead of reusing: {', '.join(sr.reused_missing)}")
    if sr.wrongly_declared:
        problems.append(f"- schema reuse — declared, but must not be: {', '.join(sr.wrongly_declared)}")
    if sr.expansion_missing:
        problems.append(f"- schema expansion — expected but not declared: {', '.join(sr.expansion_missing)}")
    for m in score.normalization.mismatches:
        problems.append(f"- normalization — {m}")
    if score.provenance.missing:
        problems.append(f"- provenance — no source span: {', '.join(score.provenance.missing)}")
    if score.provenance.fabricated:
        problems.append(f"- provenance — span not in the source text: {', '.join(score.provenance.fabricated)}")
    for v in score.domain_range_compliance.violations:
        problems.append(f"- domain/range — {v}")
    return problems or ["- all deterministic checks passed"]


def write_markdown_report(results: list[RunResult], scores: list[CaseScore], path: Path) -> None:
    """One readable report for a set of runs: per run, the input, the seed schema, what
    schema expansion added, what each extraction stage added, and the outcome against
    the gold — matched, missing, extra — plus any failed deterministic check (#134)."""
    out = ["# Extraction benchmark report", "",
           "| case | model | dedup | entities F1 | facts F1 | props F1 | checks | calls | tokens |",
           "|---|---|---|---|---|---|---|---|---|"]
    for s in scores:
        out.append(f"| {s.case_id} | {s.model} | {s.dedup_mode} | {_fmt_f1(s.entities)} | {_fmt_f1(s.facts)} | "
                   f"{_fmt_f1(s.properties)} | {_checks(s)} | {s.tool_call_count} | "
                   f"{(s.prompt_tokens + s.completion_tokens) / 1000:.0f}k |")
    for r, s in zip(results, scores):
        out += ["", "---", "", f"## {r.case.id} · {r.model} · dedup={r.dedup_mode}", "", r.case.description.strip(), "",
                "### Seed schema", *_md_seed(r.case.seed_schema)]
        for i, st in enumerate(r.steps, 1):
            label = f"Step {i}" if len(r.steps) > 1 else "Run"
            out += ["", f"### {label} — input", "", *[f"> {line}" for line in st.input_text.strip().splitlines()]]
            if st.queries:
                out += ["", "Queries: " + "; ".join(f"_{q}_" for q in st.queries)]
            if st.expansion_ran:
                out += ["", f"**Schema expansion** added ({st.expansion_tool_calls} tool calls):", *_md_added(st.expansion_added)]
            else:
                out += ["", "**Schema expansion**: off for this case (extraction against the seed schema only)."]
            out += ["", f"**Extraction** ({st.extraction_tool_calls} tool calls), by stage:"]
            for stg in st.extraction_stages:
                out += [f"- *{stg['stage']}* (pass {stg['pass']}):", *_md_added(stg["added"], indent="  ")]
        out += ["", "### Outcome vs expected", "", *_md_outcome(s), "", "### Checks", *_md_checks(s)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")
