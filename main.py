#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent.loop import run_agent
from evals.failure_taxonomy import classify_failure


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mini Codex Reliability Lab — single task runner"
    )
    parser.add_argument("--issue", required=True, help="Bug description / issue text")
    parser.add_argument("--workspace", required=True, help="Path to workspace directory")
    parser.add_argument("--max-iterations", type=int, default=15, help="Max agent iterations")
    parser.add_argument("--verbose", action="store_true", help="Print agent trace")
    parser.add_argument("--output", help="Save result JSON to this file")
    args = parser.parse_args()

    workspace = str(Path(args.workspace).resolve())
    print(f"\nMini Codex Reliability Lab")
    print(f"   Issue   : {args.issue[:80]}")
    print(f"   Workspace: {workspace}")
    print(f"   Max iters: {args.max_iterations}")

    result = run_agent(
        issue=args.issue,
        workspace=workspace,
        max_iterations=args.max_iterations,
        verbose=args.verbose,
    )

    passed = result.get("final_test_passed", False)
    status = "✅ PASS" if passed else "❌ FAIL"

    print(f"\n{'='*60}")
    print(f"Result: {status}")
    print(f"  Iterations   : {result['iterations']}")
    print(f"  Tool calls   : {result['total_tool_calls']}")
    print(f"  Patches      : {result['patches_applied']}")
    print(f"  Time         : {result['elapsed_s']}s")
    print(f"  Tool breakdown: {result.get('tool_call_breakdown', {})}")

    if not passed:
        cat = classify_failure(result)
        print(f"  Failure type : {cat.value if cat else 'unknown'}")

    if result.get("final_response"):
        print(f"\nAgent summary:\n{result['final_response'][:500]}")

    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2))
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
