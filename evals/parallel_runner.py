"""Parallel task execution over the eval fleet, with timeouts, cancellation
and a bounded number of concurrent Docker containers.

Design
------
- Each (task, seed) becomes one asyncio task via `asyncio.to_thread(run_task_sync, …)`.
- A `Semaphore(max_concurrency)` bounds how many tasks (and therefore how many
  Docker containers) run at once.
- `asyncio.wait_for(…, timeout=per_task_timeout)` enforces a per-task wall-clock
  budget. On timeout we:
    * set the task's `cancel_event` so the agent loop stops before its next
      Anthropic SDK call (cooperative cancellation — Python threads can't be
      pre-empted),
    * run `docker kill` against the labelled containers the task spawned,
    * return a shaped-like-the-others result dict with
      `failure_category="timeout"` and `root_cause="harness_error"`
      (via the classifier's error-field detection).
- SIGINT is intercepted at the loop level: we set a shared `stop_event`, each
  task's cancel_event is flipped, the gather is awaited to let in-flight tasks
  return gracefully, then any stray containers labelled by this run are killed.

What this module does NOT do
----------------------------
- It does NOT use `anthropic.AsyncAnthropic`. The agent loop stays sync so the
  existing 129-test suite keeps passing without any rewrite. We get concurrency
  from the thread pool and cancellation from a cooperative flag.
- It does NOT forcibly kill a running agent thread mid-SDK-call — Python's
  threading API doesn't allow it safely. The thread will exit at its next
  natural yield point (between iterations).

What this module DOES do
------------------------
- Bounded container concurrency (semaphore).
- Per-task timeouts that actually fire from the caller's perspective.
- SIGINT cancellation that shuts the fleet down without leaking containers.
- Measures wall-clock and reports `speedup = sequential_s / parallel_s`
  against a `--parallel 1` baseline, so the ratio is directly reported.
"""
from __future__ import annotations

import asyncio
import contextlib
import signal
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from sandbox.docker_runner import kill_containers_with_label


@dataclass
class ParallelSpec:
    """One unit of work for the parallel runner."""
    task: dict
    seed: int | None
    run_task_fn: Callable  # the sync function to call (signature: run_task from run_eval)
    kwargs: dict           # extra kwargs forwarded to run_task


@dataclass
class ParallelOutcome:
    """Result of one unit of work after it completes (or times out / is cancelled)."""
    result: dict
    duration_s: float
    timed_out: bool
    cancelled: bool
    container_kill_info: dict | None


def _timeout_result(spec: ParallelSpec, duration_s: float, cancel_cause: str) -> dict:
    """Shape a cancellation outcome like a normal run_task result dict."""
    task = spec.task
    return {
        "task_id": task["id"],
        "task_title": task.get("title", ""),
        "seed": spec.seed,
        "final_test_passed": False,
        "iterations": 0,
        "total_tool_calls": 0,
        "patches_applied": 0,
        "failed_tool_calls": 0,
        "elapsed_s": round(duration_s, 2),
        "failure_category": cancel_cause,  # "timeout" | "cancelled"
        "error": f"run aborted by harness: {cancel_cause}",
    }


async def _run_one(
    spec: ParallelSpec,
    *,
    sem: asyncio.Semaphore,
    per_task_timeout: float | None,
    container_label: str,
    stop_event: asyncio.Event,
) -> ParallelOutcome:
    """Run one unit of work under the semaphore, with timeout + cancel support."""
    # Pre-flight: if SIGINT already arrived, don't even start.
    if stop_event.is_set():
        return ParallelOutcome(
            result=_timeout_result(spec, 0.0, "cancelled"),
            duration_s=0.0, timed_out=False, cancelled=True,
            container_kill_info=None,
        )

    cancel_event = threading.Event()
    async with sem:
        if stop_event.is_set():
            return ParallelOutcome(
                result=_timeout_result(spec, 0.0, "cancelled"),
                duration_s=0.0, timed_out=False, cancelled=True,
                container_kill_info=None,
            )

        kwargs = dict(spec.kwargs)
        kwargs["container_label"] = container_label
        kwargs["cancel_event"] = cancel_event

        thread_coro = asyncio.to_thread(spec.run_task_fn, spec.task, **{
            "seed": spec.seed,
            **kwargs,
        })

        start = time.time()
        timed_out = False
        cancelled = False
        kill_info: dict | None = None
        try:
            if per_task_timeout is not None:
                result = await asyncio.wait_for(thread_coro, timeout=per_task_timeout)
            else:
                result = await thread_coro
        except asyncio.TimeoutError:
            timed_out = True
            # Flip the cancel flag so the thread exits at next iteration
            # boundary. Kill any Docker container this task spawned so pytest
            # inside the thread returns fast.
            cancel_event.set()
            kill_info = kill_containers_with_label(container_label)
            duration = time.time() - start
            result = _timeout_result(spec, duration, "timeout")
        except asyncio.CancelledError:
            cancelled = True
            cancel_event.set()
            kill_info = kill_containers_with_label(container_label)
            duration = time.time() - start
            # CancelledError must propagate so asyncio.gather knows.
            # But we want to also surface a result. Create the dict then re-raise
            # inside a wrapper that gather will collect.
            outcome = ParallelOutcome(
                result=_timeout_result(spec, duration, "cancelled"),
                duration_s=duration, timed_out=False, cancelled=True,
                container_kill_info=kill_info,
            )
            raise _CancelledWithOutcome(outcome)
        else:
            duration = time.time() - start

        return ParallelOutcome(
            result=result,
            duration_s=duration,
            timed_out=timed_out,
            cancelled=cancelled,
            container_kill_info=kill_info,
        )


class _CancelledWithOutcome(asyncio.CancelledError):
    """CancelledError carrying the partial outcome so gather can collect it."""
    def __init__(self, outcome: ParallelOutcome):
        super().__init__()
        self.outcome = outcome


async def run_parallel(
    specs: list[ParallelSpec],
    *,
    max_concurrency: int,
    per_task_timeout: float | None = None,
    label_prefix: str = "codex-lab",
    install_signal_handlers: bool = True,
) -> tuple[list[ParallelOutcome], float, str]:
    """Run every spec concurrently, bounded by max_concurrency.

    Returns (outcomes, wall_clock_s, container_label).

    The container label is a short unique tag that was attached to every
    Docker container spawned during this run; callers can `docker ps
    --filter label=<label>` to audit, or `kill_containers_with_label(label)`
    to clean up any stragglers (we already do this on timeout/cancel).
    """
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be >= 1")

    sem = asyncio.Semaphore(max_concurrency)
    stop_event = asyncio.Event()
    container_label = f"{label_prefix}-{uuid.uuid4().hex[:8]}"

    loop = asyncio.get_running_loop()
    signal_handlers_installed: list[int] = []
    if install_signal_handlers:
        def _on_signal():
            stop_event.set()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.add_signal_handler(sig, _on_signal)
                signal_handlers_installed.append(sig)

    tasks = [
        asyncio.create_task(_run_one(
            spec, sem=sem, per_task_timeout=per_task_timeout,
            container_label=container_label, stop_event=stop_event,
        ))
        for spec in specs
    ]

    start = time.time()

    async def _watch_stop():
        await stop_event.wait()
        for t in tasks:
            if not t.done():
                t.cancel()

    stop_watcher = asyncio.create_task(_watch_stop())

    outcomes: list[ParallelOutcome] = []
    try:
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        for item in gathered:
            if isinstance(item, _CancelledWithOutcome):
                outcomes.append(item.outcome)
            elif isinstance(item, asyncio.CancelledError):
                # Task was cancelled before _run_one could build its outcome.
                outcomes.append(ParallelOutcome(
                    result={"task_id": "?", "final_test_passed": False,
                            "failure_category": "cancelled",
                            "error": "cancelled before start"},
                    duration_s=0.0, timed_out=False, cancelled=True,
                    container_kill_info=None,
                ))
            elif isinstance(item, Exception):
                # Something else went wrong inside _run_one — shape it like
                # the others so the classifier can handle it (harness_error).
                outcomes.append(ParallelOutcome(
                    result={"task_id": "?", "final_test_passed": False,
                            "failure_category": "exception",
                            "error": f"{type(item).__name__}: {item}"},
                    duration_s=0.0, timed_out=False, cancelled=False,
                    container_kill_info=None,
                ))
            else:
                outcomes.append(item)
    finally:
        wall_clock = time.time() - start
        stop_watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_watcher
        # Belt-and-braces: kill any container still around that was labelled
        # with our tag (shouldn't happen if timeout path fired, but if a task
        # exited by exception the container is still removed by --rm).
        if stop_event.is_set():
            kill_containers_with_label(container_label)
        for sig in signal_handlers_installed:
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.remove_signal_handler(sig)

    return outcomes, wall_clock, container_label


def summarize(outcomes: list[ParallelOutcome], wall_clock_s: float) -> dict:
    """Shape a one-line summary dict for CLI output / JSON sidecar."""
    total = len(outcomes)
    completed = sum(1 for o in outcomes if not o.timed_out and not o.cancelled)
    timeouts = sum(1 for o in outcomes if o.timed_out)
    cancels = sum(1 for o in outcomes if o.cancelled)
    durations = [o.duration_s for o in outcomes if not o.timed_out and not o.cancelled]
    sum_task_s = sum(durations) if durations else 0.0
    return {
        "total": total,
        "completed_normally": completed,
        "timed_out": timeouts,
        "cancelled": cancels,
        "wall_clock_s": round(wall_clock_s, 2),
        "sum_of_task_durations_s": round(sum_task_s, 2),
        # Only meaningful once the caller has both a sequential and a parallel
        # wall_clock; computed in benchmark().
    }


def speedup(sequential_wall_s: float, parallel_wall_s: float) -> dict:
    """Compute speedup and efficiency given wall-clocks from both runs."""
    if parallel_wall_s <= 0:
        return {"speedup": 0.0, "parallel_wall_s": parallel_wall_s,
                "sequential_wall_s": sequential_wall_s, "efficiency": 0.0}
    s = sequential_wall_s / parallel_wall_s
    return {
        "sequential_wall_s": round(sequential_wall_s, 2),
        "parallel_wall_s": round(parallel_wall_s, 2),
        "speedup": round(s, 3),
    }
