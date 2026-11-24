#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent.loop import run_agent
from evals.failure_taxonomy import (
    classify_failure,
    classify_root_cause,
    classify_against_reference,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mini Codex Reliability Lab — single task runner"
    )
    parser.add_argument("--issue", required=True, help="Bug description / issue text")
    parser.add_argument("--workspace", required=True, help="Path to workspace directory")
    parser.add_argument("--max-iterations", type=int, default=15, help="Max agent iterations")
    parser.add_argument("--verbose", action="store_true", help="Print agent trace")
    parser.add_argument("--output", help="Save result JSON to this file")

    # Sandbox — same split as run_eval.
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run pytest on the host Python instead of inside Docker (default: Docker).",
    )

    # Model / sampling — same flags as run_eval.
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--model", default=None, help="Loop model id (default: agent.loop.MODEL).")
    parser.add_argument("--planner-model", default=None,
                        help="Planner model id (default: same as --model).")

    # Ablation knobs — same as run_eval.
    parser.add_argument("--no-planner", action="store_true")
    parser.add_argument("--no-resolver", action="store_true")
    parser.add_argument("--no-inspect-error", action="store_true")
    parser.add_argument(
        "--exposed-tools",
        help="Comma-separated subset of tools to expose (overrides --no-inspect-error).",
    )

    # Optional reference block (path to JSON) so single-task runs can get
    # reference-based classification without a tasks.jsonl entry.
    parser.add_argument(
        "--reference",
        help="Path to a reference JSON ({files, must_contain?, must_not_contain?, "
             "forbid_test_edits?}) used for fix_match classification.",
    )

    args = parser.parse_args()

    workspace = str(Path(args.workspace).resolve())

    # Docker is the default to match run_eval; local is opt-in for ad-hoc
    # debugging. On a host without a running Docker daemon, don't fail
    # silently inside the first run_tests call — detect it up front and
    # print an actionable line so the user knows what to pass.
    force_docker = not args.local
    if force_docker:
        from sandbox.docker_runner import _docker_available
        if not _docker_available():
            print(
                "WARNING: --local not passed (Docker is the default) but no "
                "Docker daemon is reachable. The final pytest verification "
                "WILL fail. Pass --local to run on host Python, or start "
                "Docker and retry."
            )

    from agent.loop import MODEL as DEFAULT_MODEL
    effective_model = args.model or DEFAULT_MODEL
    use_planner = not args.no_planner
    effective_planner_model = args.planner_model or effective_model
    planner_note = ""
    if use_planner and effective_planner_model != effective_model:
        planner_note = "  [MISMATCH: planner uses a different model than the loop]"

    print(f"\nMini Codex Reliability Lab")
    print(f"   Issue    : {args.issue[:80]}")
    print(f"   Workspace: {workspace}")
    print(f"   Max iters: {args.max_iterations}")
    print(f"   Sandbox  : {'local (host Python)' if args.local else 'docker'}")
    print(f"   Model    : {effective_model}")
    print(f"   Planner model: {effective_planner_model if use_planner else '(planner off)'}{planner_note}")

    exposed_tools = args.exposed_tools.split(",") if args.exposed_tools else None
    reference = None
    if args.reference:
        reference = json.loads(Path(args.reference).read_text())

    run_kwargs = dict(
        issue=args.issue,
        workspace=workspace,
        max_iterations=args.max_iterations,
        verbose=args.verbose,
        force_docker=force_docker,
        temperature=args.temperature,
        use_planner=not args.no_planner,
        use_resolver=not args.no_resolver,
        expose_inspect_error=not args.no_inspect_error,
        exposed_tools=exposed_tools,
    )
    if args.model:
        run_kwargs["model"] = args.model
    if args.planner_model:
        run_kwargs["planner_model"] = args.planner_model

    result = run_agent(**run_kwargs)

    # Reference-based classification + root cause so single-task output is
    # shaped like the batch eval's.
    result["reference_match"] = classify_against_reference(result, reference)
    result["root_cause"] = classify_root_cause(result, reference)

    passed = result.get("final_test_passed", False)
    status = "PASS" if passed else "FAIL"
    if result["root_cause"] == "test_tampering":
        status = "TAMPER"

    print(f"\n{'='*60}")
    print(f"Result       : {status}")
    print(f"  Iterations : {result['iterations']}")
    print(f"  Tool calls : {result['total_tool_calls']}")
    print(f"  Patches    : {result['patches_applied']}")
    print(f"  Time       : {result['elapsed_s']}s")
    print(f"  Fix match  : {result['reference_match']['fix_match']}")
    print(f"  Root cause : {result['root_cause']}")
    print(f"  Final-test reused: {result.get('final_verification_reused', False)}")
    print(f"  Tool breakdown: {result.get('tool_call_breakdown', {})}")

    if not passed:
        cat = classify_failure(result)
        print(f"  Failure type: {cat.value if cat else 'unknown'}")

    if result.get("final_response"):
        print(f"\nAgent summary:\n{result['final_response'][:500]}")

    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2))
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
