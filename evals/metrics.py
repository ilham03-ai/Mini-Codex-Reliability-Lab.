"""
Metrics computation for reliability evaluation.
"""
import json
import statistics
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
        "tests_pass_rate": round(success_rate, 3),  # same as success for this lab
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
        ["Tests pass", f"{metrics['tests_pass_rate']:.1%}"],
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
    print(f"\n💾 Results saved to {output_path}")
