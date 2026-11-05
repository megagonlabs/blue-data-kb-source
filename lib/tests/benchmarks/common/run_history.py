"""Committed run history for the querying and judges benchmarks: the same
`history.jsonl` plus rendered `README.md` as extraction's (see `history.py`), for
suites whose scores are named metrics rather than entity/fact/property F1.

A run is a list of rows, one per scored run of a case: `key` and `category`, a
value per metric (None where the metric doesn't apply), `issues`, and optionally
`tokens` and `tool_calls`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import history


@dataclass(frozen=True)
class Suite:
    title: str
    how_to: str
    metrics: dict[str, str]       # key -> label; the first one ranks the categories
    group_label: str = "category"


def _mean_of(rows: list[dict[str, Any]], metric: str) -> float | None:
    values = [r[metric] for r in rows if r.get(metric) is not None]
    return history._mean(values) if values else None


def _group(rows: list[dict[str, Any]], suite: Suite) -> dict[str, float | None]:
    return {m: _mean_of(rows, m) for m in suite.metrics}


def summarize(rows: list[dict[str, Any]], suite: Suite, *, models: list[str], judge_model: str | None = None,
              note: str = "", run_id: str | None = None, commit: str | None = None) -> dict[str, Any]:
    """The history line for one run of the benchmark."""
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    return {
        "run_id": run_id,
        "date": datetime.strptime(run_id, "%Y%m%d-%H%M%S").isoformat(),  # a past run keeps its own date
        "commit": commit or history._git("rev-parse", "--short", "HEAD"),
        "dirty": False if commit else bool(history._git("status", "--porcelain", "--untracked-files=no")),
        "models": sorted(set(models)),
        "judge_model": judge_model,
        "runs": len(rows),
        "overall": _group(rows, suite),
        "with_issues": sum(1 for r in rows if r["issues"]),
        "tokens": sum(r.get("tokens", 0) for r in rows),
        "tool_calls": sum(r.get("tool_calls", 0) for r in rows),
        "by_category": {c: {"runs": len(g), **_group(g, suite), "with_issues": sum(1 for r in g if r["issues"])}
                        for c, g in sorted(by_cat.items())},
        "by_case": {r["key"]: {"category": r["category"], **{m: r.get(m) for m in suite.metrics},
                               "issues": r["issues"]} for r in rows},
        "note": note,
    }


def _trend_chart(runs: list[dict[str, Any]], suite: Suite) -> list[str]:
    runs = runs[-history._CHART_RUNS:]
    xs = ", ".join(f'"{history._label(r)}"' for r in runs)
    palette = history._PALETTE[:len(suite.metrics)]
    out = ["```mermaid",
           '%%{init: {"themeVariables": {"xyChart": {"plotColorPalette": "' + ", ".join(palette) + '"}}}}%%',
           "xychart-beta", '    title "Mean score per run"', f"    x-axis [{xs}]", '    y-axis "score" 0 --> 1']
    for m in suite.metrics:
        # A chart line needs a number per run: a gap keeps the last known value,
        # and a metric no run has plots as 0.
        vals = [r["overall"].get(m) for r in runs]
        known = next((v for v in vals if v is not None), 0.0)
        filled = []
        for v in vals:
            known = known if v is None else v
            filled.append(f"{known:.2f}")
        out.append(f"    line [{', '.join(filled)}]")
    names = ("blue", "green", "red")
    caption = ", ".join(f"{names[i]} = {label}" for i, label in enumerate(suite.metrics.values()))
    return out + ["```", "", f"*Lines: {caption}.*"]


def _category_chart(run: dict[str, Any], suite: Suite) -> list[str]:
    main = next(iter(suite.metrics))
    cats = sorted(run["by_category"].items(), key=lambda kv: (kv[1].get(main) or 0.0, kv[0]))
    xs = ", ".join(f'"{c}"' for c, _ in cats)
    vals = ", ".join(f"{v.get(main) or 0.0:.2f}" for _, v in cats)
    return ["```mermaid", "xychart-beta horizontal",
            f'    title "Latest run: {suite.metrics[main]} per {suite.group_label} (weakest first)"',
            f"    x-axis [{xs}]", f'    y-axis "{suite.metrics[main]}" 0 --> 1', f"    bar [{vals}]", "```"]


def render(runs: list[dict[str, Any]], suite: Suite) -> str:
    """The history page: latest run, trend, per-category and per-case tables, all runs."""
    out = [f"# {suite.title}", "", "Generated from `history.jsonl` — do not edit by hand.", "",
           suite.how_to.strip(), ""]
    if not runs:
        return "\n".join(out + ["_No runs recorded yet._", ""])
    last = runs[-1]
    labels = list(suite.metrics.values())
    dirty = " (uncommitted changes)" if last.get("dirty") else ""
    judge = f" · judge {last['judge_model']}" if last.get("judge_model") else ""
    out += ["## Latest run", "",
            f"`{last['run_id']}` · commit `{last.get('commit') or '?'}`{dirty} · {', '.join(last['models'])} · "
            f"{last['runs']} runs{judge}" + (f" · *{last['note']}*" if last.get("note") else ""), "",
            "| | " + " | ".join(labels) + " | runs with an issue |", "|---" * (len(labels) + 2) + "|",
            "| **all** | " + " | ".join(history._cell([r["overall"].get(m) for r in runs]) for m in suite.metrics)
            + f" | {last['with_issues']} |", ""]
    out += ["Each cell: latest value, ▲/▼ change since the previous run, and the history as a sparkline "
            "(`▁` = 0, `█` = 1).", "", "## Trend", "", *_trend_chart(runs, suite), "",
            f"## By {suite.group_label}", "", *_category_chart(last, suite), "",
            f"| {suite.group_label} | runs | " + " | ".join(labels) + " | with an issue |",
            "|---" * (len(labels) + 3) + "|"]
    for cat, v in last["by_category"].items():
        cells = [history._cell([r.get("by_category", {}).get(cat, {}).get(m) for r in runs]) for m in suite.metrics]
        out.append(f"| {cat} | {v['runs']} | " + " | ".join(cells) + f" | {v['with_issues']} |")
    out += ["", "## By case (latest run)", "", "<details><summary>Show all cases</summary>", "",
            f"| case | {suite.group_label} | " + " | ".join(labels) + " | issues |",
            "|---" * (len(labels) + 3) + "|"]
    for key, v in sorted(last["by_case"].items(), key=lambda kv: (kv[1]["category"], kv[0])):
        cells = [history._cell([r.get("by_case", {}).get(key, {}).get(m) for r in runs]) for m in suite.metrics]
        issues = "; ".join(v["issues"]).replace("|", "\\|") or "—"
        out.append(f"| {key} | {v['category']} | " + " | ".join(cells) + f" | {issues} |")
    out += ["", "</details>", "", "## All runs", "",
            "| run | commit | models | runs | " + " | ".join(labels) + " | with an issue | tokens | note |",
            "|---" * (len(labels) + 8) + "|"]
    for r in reversed(runs):
        scores = " | ".join("—" if r["overall"].get(m) is None else f"{r['overall'][m]:.2f}" for m in suite.metrics)
        tokens = f"{r['tokens'] / 1e3:.0f}K" if r.get("tokens") else "—"
        out.append(f"| {r['run_id']} | `{r.get('commit') or '?'}`{'*' if r.get('dirty') else ''} | "
                   f"{', '.join(r['models'])} | {r['runs']} | {scores} | {r['with_issues']} | {tokens} | "
                   f"{r.get('note', '')} |")
    out += ["", "`*` = run made with uncommitted changes.", ""]
    return "\n".join(out)


def record(summary: dict[str, Any], suite: Suite, history_path: Path, page_path: Path) -> None:
    history.append(history_path, summary)
    write_page(suite, history_path, page_path)


def write_page(suite: Suite, history_path: Path, page_path: Path) -> None:
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(render(history.load(history_path), suite))
