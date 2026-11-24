"""CLI entry point for the naive baseline over the task fleet.

Run with:
    python3 -m evals.run_baseline --output reports/baseline_results.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from evals.naive_baseline import run_naive_baseline

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Naive non-LLM baseline runner")
    parser.add_argument("--tasks", help="Comma-separated task IDs (default: all)")
    parser.add_argument("--output", default="reports/baseline_results.json")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run pytest on the host Python instead of inside Docker (default: Docker). "
             "Pass this to match --local on run_eval when comparing baseline to agent.",
    )
    args = parser.parse_args()

    filter_ids = args.tasks.split(",") if args.tasks else None
    tasks = load_tasks(filter_ids)

    sandbox_name = "local (host Python)" if args.local else "docker"
    print(f"\nNaive baseline — {len(tasks)} task(s) | sandbox: {sandbox_name}")
    results = []
    for t in tasks:
        workspace = str(ROOT / t["workspace"])
        r = run_naive_baseline(
            t["issue"], workspace, verbose=args.verbose,
            reference=t.get("reference"),
            force_docker=not args.local,
        )
        r["task_id"] = t["id"]
        r["task_title"] = t["title"]
        results.append(r)
        tag = "PASS" if r["passed"] else "FAIL"
        fix_match = r.get("reference_match", {}).get("fix_match", "no_reference")
        fp = "fix_pass" if r.get("fix_passed") else "fix_fail"
        print(f"  {t['id']}: {tag} [{fp}/{fix_match}] | tried={r['mutation_tried']} | "
              f"applied={r['mutation_applied']!r} | t={r['elapsed_s']}s")
        if not r["passed"]:
            print(f"      reason: {r['reason']}")

    passed = sum(1 for r in results if r["passed"])
    fix_passed = sum(1 for r in results if r.get("fix_passed"))
    # pass_but_wrong_fix are mutations that turned pytest green but didn't
    # touch the reference file (or missed required tokens). Counting them
    # separately keeps the baseline honest.
    pass_but_wrong = passed - fix_passed
    summary = {
        "n": len(results),
        "passed": passed,
        "fix_passed": fix_passed,
        "pass_but_wrong_fix": pass_but_wrong,
        "pass_rate": round(passed / len(results), 3) if results else 0,
        "fix_pass_rate": round(fix_passed / len(results), 3) if results else 0,
    }
    print(
        f"\nBaseline summary: pytest-green {passed}/{len(results)} "
        f"({summary['pass_rate']:.1%}); honest fix {fix_passed}/{len(results)} "
        f"({summary['fix_pass_rate']:.1%}); pass_but_wrong_fix {pass_but_wrong}"
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
