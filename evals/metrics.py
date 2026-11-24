"""
Metrics computation for reliability evaluation.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from tabulate import tabulate


def compute_metrics(results: list[dict]) -> dict:
    if not results:
        return {}

    total = len(results)
    solved = [r for r in results if r.get("final_test_passed")]
    failed = [r for r in results if not r.get("final_test_passed")]

    success_rate = len(solved) / total

    # Patch compilation: at least one edit_file succeeded
    patch_compiled = [
        r for r in results
        if r.get("patches_applied", 0) > 0
    ]

    # Tool call stats
    tool_counts = [r.get("total_tool_calls", 0) for r in results]
    retry_counts = [r.get("iterations", 0) for r in results]
    elapsed_times = [r.get("elapsed_s", 0.0) for r in results]

    return {
        "total_tasks": total,
        "solved": len(solved),
        "failed": len(failed),
        "task_success_rate": round(success_rate, 3),
        "patch_compile_rate": round(len(patch_compiled) / total, 3),
        "avg_tool_calls": round(statistics.mean(tool_counts), 1) if tool_counts else 0,
        "avg_iterations": round(statistics.mean(retry_counts), 1) if retry_counts else 0,
        "avg_time_s": round(statistics.mean(elapsed_times), 1) if elapsed_times else 0,
        "median_time_s": round(statistics.median(elapsed_times), 1) if elapsed_times else 0,
        "tool_call_breakdown": _aggregate_tool_breakdown(results),
    }


def _aggregate_tool_breakdown(results: list[dict]) -> dict:
    totals: dict[str, int] = {}
    for r in results:
        for tool, count in r.get("tool_call_breakdown", {}).items():
            totals[tool] = totals.get(tool, 0) + count
    return totals


def print_metrics_table(metrics: dict) -> None:
    rows = [
        ["Task success rate", f"{metrics['task_success_rate']:.1%}"],
        ["Patch compiles", f"{metrics['patch_compile_rate']:.1%}"],
        ["Avg tool calls / task", str(metrics["avg_tool_calls"])],
        ["Avg iterations / task", str(metrics["avg_iterations"])],
        ["Avg time / task (s)", str(metrics["avg_time_s"])],
        ["Solved / Total", f"{metrics['solved']} / {metrics['total_tasks']}"],
    ]
    print("\n" + tabulate(rows, headers=["Metric", "Value"], tablefmt="github"))


def print_per_task_table(results: list[dict]) -> None:
    rows = []
    for r in results:
        rows.append([
            r.get("task_id", "?"),
            "✅" if r.get("final_test_passed") else "❌",
            r.get("iterations", 0),
            r.get("total_tool_calls", 0),
            r.get("patches_applied", 0),
            f"{r.get('elapsed_s', 0):.1f}s",
        ])
    print("\n" + tabulate(
        rows,
        headers=["Task", "Pass", "Iters", "Tools", "Patches", "Time"],
        tablefmt="github",
    ))


def save_results(results: list[dict], metrics: dict, output_path: str) -> None:
    data = {"metrics": metrics, "results": results}
    Path(output_path).write_text(json.dumps(data, indent=2))
    print(f"\nResults saved to {output_path}")


def _mean_std(xs: list[float]) -> tuple[float, float]:
    if not xs:
        return 0.0, 0.0
    if len(xs) == 1:
        return float(xs[0]), 0.0
    return statistics.mean(xs), statistics.stdev(xs)


def per_task_aggregate(results: list[dict]) -> list[dict]:
    """Group results by task_id and compute pass rate + mean ± stddev per metric.

    When a task was run once this just surfaces the single-run numbers with
    stddev=0. When run N times it reports the proper reliability figure:
    pass_rate across N seeds.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        groups[r.get("task_id", "?")].append(r)

    out = []
    for task_id, runs in groups.items():
        n = len(runs)
        passes = sum(1 for r in runs if r.get("final_test_passed"))
        pass_rate = passes / n if n else 0.0
        iters_mean, iters_std = _mean_std([r.get("iterations", 0) for r in runs])
        tools_mean, tools_std = _mean_std([r.get("total_tool_calls", 0) for r in runs])
        ftools_mean, ftools_std = _mean_std([r.get("failed_tool_calls", 0) for r in runs])
        t_mean, t_std = _mean_std([r.get("elapsed_s", 0.0) for r in runs])
        # fix_match distribution across seeds. A task that's 2× correct +
        # 1× partial is visible here, instead of collapsing to "2/3 pass".
        fix_match_dist: dict[str, int] = {}
        for r in runs:
            fm = (r.get("reference_match") or {}).get("fix_match", "no_reference")
            fix_match_dist[fm] = fix_match_dist.get(fm, 0) + 1
        out.append({
            "task_id": task_id,
            "n": n,
            "pass_rate": round(pass_rate, 3),
            "passes": passes,
            "iterations_mean": round(iters_mean, 2),
            "iterations_std": round(iters_std, 2),
            "tool_calls_mean": round(tools_mean, 2),
            "tool_calls_std": round(tools_std, 2),
            "failed_tool_calls_mean": round(ftools_mean, 2),
            "failed_tool_calls_std": round(ftools_std, 2),
            "elapsed_s_mean": round(t_mean, 2),
            "elapsed_s_std": round(t_std, 2),
            "fix_match_distribution": fix_match_dist,
        })
    return out


def print_aggregate_table(agg: list[dict]) -> None:
    rows = []
    for a in agg:
        rows.append([
            a["task_id"],
            f"{a['passes']}/{a['n']}",
            f"{a['pass_rate']:.1%}",
            f"{a['iterations_mean']:.1f} ± {a['iterations_std']:.1f}",
            f"{a['tool_calls_mean']:.1f} ± {a['tool_calls_std']:.1f}",
            f"{a['failed_tool_calls_mean']:.1f} ± {a['failed_tool_calls_std']:.1f}",
            f"{a['elapsed_s_mean']:.1f} ± {a['elapsed_s_std']:.1f}",
        ])
    print("\n" + tabulate(
        rows,
        headers=["Task", "Pass/N", "Rate", "Iters (μ±σ)", "Tools", "Refused tools", "Elapsed (s)"],
        tablefmt="github",
    ))
