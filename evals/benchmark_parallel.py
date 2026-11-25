"""Measure speedup of the parallel runner vs sequential, without spending API.

The runner's design uses `asyncio.to_thread(run_task_sync, ...)`. The agent
loop's cost is dominated by sync HTTP calls to the Anthropic SDK. `time.sleep`
releases the GIL exactly like a blocking socket read does, so a `sleep(d)`
exercises the same parallelism path as a real SDK call of duration `d` —
without spending API budget. (This is NOT how we'd measure GPU contention or
pure-Python CPU work; both of those would be throttled by the GIL.)

Methodology
-----------
We take the per-run durations from the most recent REAL run (run_v3.json),
build a synthetic workload whose each "task" sleeps for its real duration,
then run that workload through `parallel_runner.run_parallel` at N ∈
{1, 2, 4, 8}. The wall-clock at N=1 is the sequential baseline; at N≥2 the
ratio is the speedup.

What this measures: the overhead of the orchestrator (semaphore, gather,
to_thread, cancellation plumbing) under realistic task-duration shape.
What this doesn't measure: anything that depends on CPU-bound Python work,
or on Docker daemon contention. Docker ops are a few hundred ms each in
the real path, which is already included in the real-run durations.

Run: python -m evals.benchmark_parallel [--output reports/benchmark.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from evals.parallel_runner import ParallelSpec, run_parallel, speedup


DEFAULT_DURATIONS_SOURCE = Path(__file__).parent.parent / "reports" / "run_v3.json"


def _load_durations(source: Path) -> list[float]:
    """Pull the per-run elapsed_s list from a run_eval output JSON."""
    data = json.loads(source.read_text())
    return [r.get("elapsed_s", 0.0) for r in data.get("results", [])]


def _fake_run_task(task: dict, **kwargs) -> dict:
    """Pretends to be run_task: sleeps for task['_duration_s'] seconds.

    Honours cancel_event so `--per-task-timeout` works end-to-end.
    """
    cancel = kwargs.get("cancel_event")
    duration = task.get("_duration_s", 0.0)
    start = time.time()
    while time.time() - start < duration:
        if cancel is not None and cancel.is_set():
            break
        time.sleep(min(0.05, duration))
    return {
        "task_id": task["id"],
        "task_title": task.get("title", ""),
        "final_test_passed": True,
        "iterations": 1,
        "total_tool_calls": 1,
        "patches_applied": 0,
        "failed_tool_calls": 0,
        "elapsed_s": round(time.time() - start, 2),
    }


def _specs(durations: list[float]) -> list[ParallelSpec]:
    return [
        ParallelSpec(
            task={"id": f"t{i:02d}", "title": "synthetic", "issue": "x",
                  "workspace": "/tmp/fake", "_duration_s": d},
            seed=0,
            run_task_fn=_fake_run_task,
            kwargs={},
        )
        for i, d in enumerate(durations)
    ]


def benchmark(durations: list[float], concurrencies: list[int]) -> dict:
    sum_task = sum(durations)
    per_run = {}
    for n in concurrencies:
        specs = _specs(durations)
        start = time.time()
        _outcomes, wall, _label = asyncio.run(run_parallel(
            specs, max_concurrency=n, install_signal_handlers=False,
        ))
        elapsed = time.time() - start
        per_run[n] = {
            "wall_clock_s": round(wall, 3),
            "end_to_end_s": round(elapsed, 3),
            "speedup_vs_seq": round(per_run[1]["wall_clock_s"] / wall, 3) if 1 in per_run and wall > 0 else None,
            "efficiency": round((per_run[1]["wall_clock_s"] / wall) / n, 3) if 1 in per_run and wall > 0 else None,
        }
    return {
        "n_tasks": len(durations),
        "sum_of_task_durations_s": round(sum_task, 3),
        "task_duration_min_s": round(min(durations), 3) if durations else 0,
        "task_duration_max_s": round(max(durations), 3) if durations else 0,
        "task_duration_median_s": round(statistics.median(durations), 3) if durations else 0,
        "concurrencies": per_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure parallel runner speedup")
    parser.add_argument(
        "--source",
        default=str(DEFAULT_DURATIONS_SOURCE),
        help="run_eval output JSON to pull per-task durations from",
    )
    parser.add_argument(
        "--output",
        default="reports/benchmark_parallel.json",
        help="Where to write the benchmark result",
    )
    parser.add_argument(
        "--concurrencies",
        default="1,2,4,8",
        help="Comma-separated list of max_concurrency values to measure",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Scale all task durations by this factor (e.g. 0.1 for a fast dev run)",
    )
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        print(f"ERROR: {source} not found. Run `python -m evals.run_eval` first "
              f"or pass --source pointing at another run JSON.", file=sys.stderr)
        sys.exit(1)

    durations = _load_durations(source)
    if not durations:
        print(f"ERROR: no durations in {source}", file=sys.stderr)
        sys.exit(1)

    if args.scale != 1.0:
        durations = [d * args.scale for d in durations]

    concurrencies = [int(x) for x in args.concurrencies.split(",")]

    print(f"Benchmark source  : {source}")
    print(f"Tasks             : {len(durations)}")
    print(f"Sum of durations  : {sum(durations):.2f}s")
    print(f"Scale             : {args.scale}")
    print(f"Concurrencies     : {concurrencies}")
    print()

    result = benchmark(durations, concurrencies)

    print(f"{'N':>4} {'wall (s)':>10} {'speedup':>10} {'efficiency':>12}")
    print("-" * 40)
    for n in concurrencies:
        row = result["concurrencies"][n]
        sp = row["speedup_vs_seq"] if row["speedup_vs_seq"] is not None else 1.0
        eff = row["efficiency"] if row["efficiency"] is not None else 1.0
        print(f"{n:>4} {row['wall_clock_s']:>10.2f} {sp:>9.2f}x {eff:>11.1%}")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
