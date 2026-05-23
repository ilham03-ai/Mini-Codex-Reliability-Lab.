import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

# Make sure project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.loop import run_agent
from evals.metrics import compute_metrics, print_metrics_table, print_per_task_table, save_results
from evals.failure_taxonomy import classify_failure, build_failure_report

ROOT = Path(__file__).parent.parent
TASKS_FILE = ROOT / "evals" / "tasks.jsonl"


def load_tasks(filter_ids: list[str] | None = None) -> list[dict]:
    tasks = []
    with open(TASKS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    if filter_ids:
        tasks = [t for t in tasks if t["id"] in filter_ids]
    return tasks


def run_task(task: dict, isolated: bool = False, verbose: bool = False) -> dict:
    workspace = str(ROOT / task["workspace"])

    if isolated:
        tmp = tempfile.mkdtemp(prefix=f"codex_{task['id']}_")
        shutil.copytree(workspace, tmp, dirs_exist_ok=True)
        workspace = tmp

    print(f"\n{'='*60}")
    print(f"▶ {task['id']}: {task['title']}")
    print(f"  workspace: {workspace}")

    try:
        result = run_agent(
            issue=task["issue"],
            workspace=workspace,
            max_iterations=15,
            verbose=verbose,
            force_docker=isolated,
        )
        result["task_id"] = task["id"]
        result["task_title"] = task["title"]

        status = "✅ PASS" if result["final_test_passed"] else "❌ FAIL"
        print(f"  {status} | {result['iterations']} iters | "
              f"{result['total_tool_calls']} tool calls | {result['elapsed_s']}s")

        if not result["final_test_passed"]:
            cat = classify_failure(result)
            result["failure_category"] = cat.value if cat else "unknown"
            print(f"  Failure: {result['failure_category']}")

    except Exception as e:
        print(f"  Exception: {e}")
        result = {
            "task_id": task["id"],
            "task_title": task["title"],
            "final_test_passed": False,
            "iterations": 0,
            "total_tool_calls": 0,
            "patches_applied": 0,
            "elapsed_s": 0.0,
            "failure_category": "exception",
            "error": str(e),
        }
    finally:
        if isolated and tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini Codex Reliability Lab — Eval Runner")
    parser.add_argument("--tasks", help="Comma-separated task IDs to run (default: all)")
    parser.add_argument("--output", default="results.json", help="Output JSON file")
    parser.add_argument(
        "--isolated",
        action="store_true",
        help="Copy workspaces to tmp dirs and force Docker sandbox (Docker must be running)",
    )
    parser.add_argument("--verbose", action="store_true", help="Show agent trace")
    args = parser.parse_args()

    filter_ids = args.tasks.split(",") if args.tasks else None
    tasks = load_tasks(filter_ids)

    if not tasks:
        print("No tasks found.")
        sys.exit(1)

    print(f"\nMini Codex Reliability Lab — eval")
    print(f"   Running {len(tasks)} task(s)")
    print(f"   Isolated: {args.isolated}")

    results = []
    for task in tasks:
        result = run_task(task, isolated=args.isolated, verbose=args.verbose)
        results.append(result)

    print(f"\n{'='*60}")
    print("METRICS SUMMARY")

    metrics = compute_metrics(results)
    print_metrics_table(metrics)
    print_per_task_table(results)

    failure_report = build_failure_report(results)
    if failure_report["total_failed"] > 0:
        print(f"\nFailure breakdown ({failure_report['total_failed']} failures):")
        for cat, count in failure_report["by_category"].items():
            desc = failure_report["category_descriptions"].get(cat, cat)
            print(f"  {cat}: {count}  — {desc}")

    save_results(results, metrics, args.output)


if __name__ == "__main__":
    main()
