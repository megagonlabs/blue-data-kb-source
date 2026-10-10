"""Committed run history: one summary line per benchmark run, appended to a JSONL
file, and a Markdown page rendered from it with trend charts (#134).

The per-run reports under `.reports/` hold the details and stay out of git; the
history keeps only what is needed to see progress over time — mean F1 per kind,
per category and per case, deterministic-check failures and cost — so it is
small enough to commit and diff.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .scoring import CaseScore

KINDS = ("entities", "facts", "properties")
_SPARK = "▁▂▃▄▅▆▇█"
# Line colours in the trend chart, in KINDS order; the caption names them.
_PALETTE = ("#2563eb", "#16a34a", "#dc2626")
_CHART_RUNS = 30


def _mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 3) if xs else 0.0


def _checks_failed(s: CaseScore) -> list[str]:
    return [name for name, ok in (
        ("schema", s.schema_reuse.ok), ("norm", s.normalization.ok),
        ("prov", s.provenance.ok), ("dom/rng", s.domain_range_compliance.ok),
    ) if not ok]


def _f1s(scores: list[CaseScore]) -> dict[str, float]:
    return {k: _mean([getattr(s, k).f1 for s in scores]) for k in KINDS}


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True,
                              cwd=Path(__file__).parent).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def summarize(scores: list[CaseScore], *, judge_model: str, note: str = "",
              run_id: str | None = None, commit: str | None = None) -> dict[str, Any]:
    """The history line for one run of the benchmark."""
    by_cat: dict[str, list[CaseScore]] = {}
    for s in scores:
        by_cat.setdefault(s.category, []).append(s)
    return {
        "run_id": run_id or datetime.now().strftime("%Y%m%d-%H%M%S"),
        "date": datetime.now().isoformat(timespec="seconds"),
        "commit": commit or _git("rev-parse", "--short", "HEAD"),
        "dirty": False if commit else bool(_git("status", "--porcelain", "--untracked-files=no")),
        "models": sorted({s.model for s in scores}),
        "judge_model": judge_model,
        "cases": len({s.case_id for s in scores}),
        "runs": len(scores),
        "overall": _f1s(scores),
        "check_failures": sum(1 for s in scores if _checks_failed(s)),
        "tokens": sum(s.prompt_tokens + s.completion_tokens for s in scores),
        "tool_calls": sum(s.tool_call_count for s in scores),
        "by_category": {c: {"runs": len(g), **_f1s(g), "check_failures": sum(1 for s in g if _checks_failed(s))}
                        for c, g in sorted(by_cat.items())},
        "by_case": {f"{s.case_id} · {s.model} · {s.dedup_mode}": {
            "category": s.category, **{k: round(getattr(s, k).f1, 3) for k in KINDS},
            "checks_failed": _checks_failed(s)} for s in scores},
        "note": note,
    }


def append(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")


def load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _spark(values: list[float]) -> str:
    return "".join(_SPARK[min(int(v * len(_SPARK)), len(_SPARK) - 1)] for v in values)


def _delta(cur: float, prev: float | None) -> str:
    if prev is None or round(cur - prev, 2) == 0:
        return ""
    return f" {'▲' if cur > prev else '▼'}{abs(cur - prev):.2f}"


def _cell(series: list[float | None]) -> str:
    """Latest value, change since the previous run that had it, and a sparkline."""
    known = [v for v in series if v is not None]
    if not known:
        return "—"
    prev = known[-2] if len(known) > 1 else None
    spark = f" `{_spark(known)}`" if len(known) > 1 else ""
    return f"{known[-1]:.2f}{_delta(known[-1], prev)}{spark}"


def _label(run: dict[str, Any]) -> str:
    return run["run_id"][2:8] + "-" + run["run_id"][9:13] if len(run["run_id"]) >= 13 else run["run_id"]


def _trend_chart(runs: list[dict[str, Any]]) -> list[str]:
    runs = runs[-_CHART_RUNS:]
    xs = ", ".join(f'"{_label(r)}"' for r in runs)
    out = ["```mermaid",
           '%%{init: {"themeVariables": {"xyChart": {"plotColorPalette": "' + ", ".join(_PALETTE) + '"}}}}%%',
           "xychart-beta", '    title "Mean F1 per run"', f"    x-axis [{xs}]", '    y-axis "F1" 0 --> 1']
    for k in KINDS:
        vals = ", ".join(f"{r['overall'][k]:.2f}" for r in runs)
        out.append(f"    line [{vals}]")
    return out + ["```", "", "*Lines: blue = entities, green = facts, red = properties.*"]


def _category_chart(run: dict[str, Any]) -> list[str]:
    cats = sorted(run["by_category"].items(), key=lambda kv: _mean([kv[1][k] for k in KINDS]))
    xs = ", ".join(f'"{c}"' for c, _ in cats)
    vals = ", ".join(f"{_mean([v[k] for k in KINDS]):.2f}" for _, v in cats)
    return ["```mermaid", "xychart-beta horizontal", '    title "Latest run: mean F1 per category (weakest first)"',
            f"    x-axis [{xs}]", '    y-axis "mean of entities, facts, properties F1" 0 --> 1',
            f"    bar [{vals}]", "```"]


def render(runs: list[dict[str, Any]], *, title: str, how_to: str) -> str:
    """The history page: latest run, trends, per-category and per-case tables, all runs."""
    out = [f"# {title}", "", "Generated from `history.jsonl` — do not edit by hand.", "", how_to.strip(), ""]
    if not runs:
        return "\n".join(out + ["_No runs recorded yet._", ""])
    last = runs[-1]
    dirty = " (uncommitted changes)" if last.get("dirty") else ""
    out += ["## Latest run", "",
            f"`{last['run_id']}` · commit `{last.get('commit') or '?'}`{dirty} · {', '.join(last['models'])} · "
            f"{last['cases']} cases, {last['runs']} runs · judge {last['judge_model']}"
            + (f" · *{last['note']}*" if last.get("note") else ""), "",
            "| | entities F1 | facts F1 | props F1 | runs failing a check |", "|---|---|---|---|---|",
            "| **all** | " + " | ".join(_cell([r["overall"][k] for r in runs]) for k in KINDS)
            + f" | {last['check_failures']} |", ""]
    out += ["Each cell: latest value, ▲/▼ change since the previous run, and the history as a sparkline "
            "(`▁` = 0, `█` = 1).", "", "## Trend", "", *_trend_chart(runs), "",
            "## By category", "", *_category_chart(last), "",
            "| category | runs | entities F1 | facts F1 | props F1 | failing a check |", "|---|---|---|---|---|---|"]
    for cat, v in last["by_category"].items():
        series = {k: [r.get("by_category", {}).get(cat, {}).get(k) for r in runs] for k in KINDS}
        out.append(f"| {cat} | {v['runs']} | " + " | ".join(_cell(series[k]) for k in KINDS)
                   + f" | {v['check_failures']} |")
    out += ["", "## By case (latest run)", "", "<details><summary>Show all cases</summary>", "",
            "| case · model · dedup | category | entities F1 | facts F1 | props F1 | failed checks |",
            "|---|---|---|---|---|---|"]
    for key, v in sorted(last["by_case"].items(), key=lambda kv: (kv[1]["category"], kv[0])):
        series = {k: [r.get("by_case", {}).get(key, {}).get(k) for r in runs] for k in KINDS}
        out.append(f"| {key} | {v['category']} | " + " | ".join(_cell(series[k]) for k in KINDS)
                   + f" | {', '.join(v['checks_failed']) or '—'} |")
    out += ["", "</details>", "", "## All runs", "",
            "| run | commit | models | cases | runs | entities | facts | props | failing a check | tokens | note |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in reversed(runs):
        o = r["overall"]
        out.append(f"| {r['run_id']} | `{r.get('commit') or '?'}`{'*' if r.get('dirty') else ''} | "
                   f"{', '.join(r['models'])} | {r['cases']} | {r['runs']} | {o['entities']:.2f} | {o['facts']:.2f} | "
                   f"{o['properties']:.2f} | {r['check_failures']} | {r['tokens'] / 1e6:.1f}M | {r.get('note', '')} |")
    out += ["", "`*` = run made with uncommitted changes.", ""]
    return "\n".join(out)
