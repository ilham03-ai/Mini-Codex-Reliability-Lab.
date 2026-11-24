from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

# Make sure project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.loop import run_agent
from evals.metrics import (
    compute_metrics,
    print_metrics_table,
    print_per_task_table,
    print_aggregate_table,
    per_task_aggregate,
    save_results,
)
from evals.failure_taxonomy import (
    classify_failure,
    build_failure_report,
    build_root_cause_report,
    classify_root_cause,
    classify_against_reference,
    threshold_sensitivity,
)

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


def run_task(
    task: dict,
    isolated: bool = False,
    verbose: bool = False,
    temperature: float = 0.2,
    seed: int | None = None,
    use_planner: bool = True,
    use_resolver: bool = True,
    expose_inspect_error: bool = True,
    exposed_tools: list | None = None,
    model: str | None = None,
    planner_model: str | None = None,
) -> dict:
    workspace = str(ROOT / task["workspace"])
    tmp = None

    # Always run on a copy of the workspace so a bad patch from one run doesn't
    # bleed into the next. Previously --isolated was the only switch that copied,
    # which meant repeated runs over the same source silently reused a dirty
    # workspace on run 2+. For reproducibility we now copy every time, and
    # --isolated controls only whether tests execute inside Docker.
    tmp = tempfile.mkdtemp(prefix=f"codex_{task['id']}_s{seed or 0}_")
    shutil.copytree(workspace, tmp, dirs_exist_ok=True)
    workspace = tmp

    print(f"\n{'='*60}")
    print(f"▶ {task['id']}{f' [seed={seed}]' if seed is not None else ''}: {task['title']}")
    print(f"  workspace: {workspace}")

    try:
        agent_kwargs = dict(
            issue=task["issue"],
            workspace=workspace,
            max_iterations=15,
            verbose=verbose,
            force_docker=isolated,
            temperature=temperature,
            seed=seed,
            use_planner=use_planner,
            use_resolver=use_resolver,
            expose_inspect_error=expose_inspect_error,
            exposed_tools=exposed_tools,
        )
        if model is not None:
            agent_kwargs["model"] = model
        if planner_model is not None:
            agent_kwargs["planner_model"] = planner_model
        result = run_agent(**agent_kwargs)
        result["task_id"] = task["id"]
        result["task_title"] = task["title"]
        if seed is not None:
            result["seed"] = seed

        # Capture the final content of each reference file BEFORE cleanup, so
        # the reference check can verify tokens against what actually landed
        # on disk — not against the `+` lines of a diff, which miss rewrites
        # that touch more than a single line.
        ref = task.get("reference") or {}
        ref_files = ref.get("files") or []
        final_contents: dict[str, str] = {}
        for rf in ref_files:
            fp = Path(workspace) / rf
            try:
                final_contents[rf] = fp.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                final_contents[rf] = ""
        result["final_file_contents"] = final_contents

        # Reference-based classification runs on every result, success or fail.
        result["reference_match"] = classify_against_reference(
            result, task.get("reference")
        )

        # Compute the tamper-aware root cause on every run so a passing-but-
        # tampered run surfaces in the print line too. We pass the task's
        # reference so sanctioned test edits (trap-test tasks, config-bug
        # tasks) don't get mislabeled as tampering.
        result["root_cause"] = classify_root_cause(result, task.get("reference"))

        status = "PASS" if result["final_test_passed"] else "FAIL"
        if result["root_cause"] == "test_tampering":
            status = "TAMPER"
        print(f"  {status} | {result['iterations']} iters | "
              f"{result['total_tool_calls']} tool calls | {result['elapsed_s']}s | "
              f"fix_match={result['reference_match']['fix_match']}")

        if not result["final_test_passed"]:
            cat = classify_failure(result)
            result["failure_category"] = cat.value if cat else "unknown"
            print(f"  Failure: {result['failure_category']} | root_cause: {result['root_cause']}")

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
            "seed": seed,
        }
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini Codex Reliability Lab — Eval Runner")
    parser.add_argument("--tasks", help="Comma-separated task IDs to run (default: all)")
    parser.add_argument("--output", default="results.json", help="Output JSON file")
    parser.add_argument(
        "--isolated",
        action="store_true",
        help="[deprecated alias — kept for backwards compatibility] Force Docker; "
             "same as leaving --local off. Docker is the default.",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run pytest on the host Python instead of inside Docker. Faster "
             "for local development; use Docker (the default) for any numbers "
             "you plan to report.",
    )
    parser.add_argument("--verbose", action="store_true", help="Show agent trace")
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Sampling temperature for the agent (default: 0.2)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Run each task this many times with different seeds for variance (default: 1)",
    )
    parser.add_argument(
        "--no-planner",
        action="store_true",
        help="Ablation: skip the planner step; agent sees only the issue text",
    )
    parser.add_argument(
        "--no-resolver",
        action="store_true",
        help="Ablation: skip path resolution; agent sees the issue text verbatim",
    )
    parser.add_argument(
        "--no-inspect-error",
        action="store_true",
        help="Ablation: hide the inspect_error tool from the agent",
    )
    parser.add_argument(
        "--exposed-tools",
        help="Comma-separated subset of tools to expose (overrides --no-inspect-error). "
             "E.g. 'read_file,edit_file,run_tests' for a minimal-tools baseline.",
    )
    parser.add_argument(
        "--preset",
        choices=["full", "weak-agent"],
        default="full",
        help=(
            "Named presets. 'full' is the default harness. 'weak-agent' is the "
            "same model with planner, resolver, search_code and inspect_error "
            "all turned off — a fair LLM-level baseline against the full loop."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Loop model id (default: agent.loop.MODEL). Prints to the run config.",
    )
    parser.add_argument(
        "--planner-model",
        default=None,
        help="Planner model id (default: same as --model). Set to a different "
             "model only when you want --no-planner vs full to compare "
             "{M, plan by other-M} vs {M, no plan}; the mismatch is logged.",
    )
    args = parser.parse_args()

    # Preset applies as a set of defaults; explicit flags still win.
    if args.preset == "weak-agent":
        args.no_planner = True
        args.no_resolver = True
        if args.exposed_tools is None:
            args.exposed_tools = "read_file,edit_file,run_tests"

    filter_ids = args.tasks.split(",") if args.tasks else None
    tasks = load_tasks(filter_ids)

    if not tasks:
        print("No tasks found.")
        sys.exit(1)

    exposed_tools = args.exposed_tools.split(",") if args.exposed_tools else None
    use_planner = not args.no_planner
    use_resolver = not args.no_resolver
    expose_inspect_error = not args.no_inspect_error

    # Sandbox resolution: Docker is the default; --local flips to host Python;
    # --isolated is retained as an explicit "yes, use Docker" alias.
    use_docker = not args.local  # default True
    if args.isolated and args.local:
        print("Conflicting flags: --isolated and --local given together; "
              "--local wins.")
        use_docker = False

    # Resolve the models we'll print and pass through.
    from agent.loop import MODEL as DEFAULT_MODEL
    effective_model = args.model or DEFAULT_MODEL
    effective_planner_model = args.planner_model or effective_model
    planner_note = ""
    if use_planner and effective_planner_model != effective_model:
        planner_note = f"  [MISMATCH: planner uses a different model than the loop]"

    print(f"\nMini Codex Reliability Lab — eval")
    print(f"   Running {len(tasks)} task(s) × {args.repeats} repeat(s)  =  {len(tasks) * args.repeats} runs")
    print(f"   Model: {effective_model}")
    print(f"   Planner model: {effective_planner_model if use_planner else '(planner off)'}{planner_note}")
    print(f"   Temperature: {args.temperature}")
    print(f"   Sandbox: {'docker' if use_docker else 'local (host Python)'}")
    if not use_docker:
        print("   WARNING: --local runs pytest on the host Python; patches the")
        print("   agent applies touch your host environment indirectly (installed")
        print("   packages, Python version). Use Docker for publishable numbers.")
    print(f"   Ablations: planner={use_planner} resolver={use_resolver} "
          f"inspect_error={expose_inspect_error} exposed_tools={exposed_tools or 'all'}")

    results = []
    for task in tasks:
        for seed in range(args.repeats):
            result = run_task(
                task,
                isolated=use_docker,
                verbose=args.verbose,
                temperature=args.temperature,
                seed=seed if args.repeats > 1 else None,
                use_planner=use_planner,
                use_resolver=use_resolver,
                expose_inspect_error=expose_inspect_error,
                exposed_tools=exposed_tools,
                model=args.model,
                planner_model=args.planner_model,
            )
            results.append(result)

    print(f"\n{'='*60}")
    print("METRICS SUMMARY")

    metrics = compute_metrics(results)
    print_metrics_table(metrics)

    agg = per_task_aggregate(results)
    if args.repeats > 1:
        print_aggregate_table(agg)
    else:
        print_per_task_table(results)
    metrics["per_task_aggregate"] = agg

    failure_report = build_failure_report(results)
    if failure_report["total_failed"] > 0:
        print(f"\nFailure breakdown ({failure_report['total_failed']} failures):")
        for cat, count in failure_report["by_category"].items():
            desc = failure_report["category_descriptions"].get(cat, cat)
            print(f"  {cat}: {count}  — {desc}")

    tasks_by_id = {t["id"]: t for t in tasks}
    root_report = build_root_cause_report(results, tasks_by_id)
    if root_report["failed"] > 0:
        print(f"\nRoot cause (tool / context / model) — {root_report['failed']} failures:")
        for cause, count in root_report["counts"].items():
            desc = root_report["descriptions"].get(cause, cause)
            print(f"  {cause}: {count}  — {desc}")

    save_results(results, metrics, args.output)
    # Also dump the root-cause report next to the results file.
    root_out = str(Path(args.output).with_name(Path(args.output).stem + "_root_cause.json"))
    Path(root_out).write_text(json.dumps(root_report, indent=2))
    print(f"Root-cause report saved to {root_out}")

    sens = threshold_sensitivity(results, tasks_by_id)
    print(f"\nTaxonomy threshold sensitivity (lower is more fragile):")
    for name, stats in sens.items():
        if name == "base_label_distribution":
            continue
        print(f"  {name}: stability={stats['stability']:.1%} "
              f"({stats['changed_labels']}/{stats['total']} labels change)")
    print(f"  base label distribution: {sens['base_label_distribution']}")
    sens_out = str(Path(args.output).with_name(Path(args.output).stem + "_sensitivity.json"))
    Path(sens_out).write_text(json.dumps(sens, indent=2))
    print(f"Sensitivity report saved to {sens_out}")


if __name__ == "__main__":
    main()
