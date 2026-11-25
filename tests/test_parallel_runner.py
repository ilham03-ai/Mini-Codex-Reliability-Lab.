"""Tests for the parallel runner: concurrency bound, per-task timeout,
graceful cancellation, Docker container labeling.

We don't need real Anthropic calls or real Docker containers for these tests —
the ParallelSpec's `run_task_fn` is an injection point that we set to a
synchronous stub for each scenario. The runner itself is the unit under test.
"""
import asyncio
import threading
import time

import pytest

from evals.parallel_runner import (
    ParallelSpec,
    ParallelOutcome,
    run_parallel,
    speedup,
    summarize,
)


def _make_spec(task_id: str, fn, **kwargs) -> ParallelSpec:
    return ParallelSpec(
        task={"id": task_id, "title": f"task {task_id}", "issue": "x",
              "workspace": "/tmp/fake"},
        seed=0,
        run_task_fn=fn,
        kwargs=kwargs,
    )


# ---------------------------------------------------------------------------
# Concurrency bound — a Semaphore(N) must cap actual concurrent executions
# ---------------------------------------------------------------------------

def test_semaphore_caps_concurrent_executions():
    max_c = 2
    # Shared counter of in-flight tasks.
    in_flight = {"n": 0, "peak": 0}
    lock = threading.Lock()

    def _fn(task, **_kw):
        with lock:
            in_flight["n"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["n"])
        # Hold the semaphore long enough that if N were higher the peak
        # would show it.
        time.sleep(0.2)
        with lock:
            in_flight["n"] -= 1
        return {"task_id": task["id"], "final_test_passed": True,
                "iterations": 1, "total_tool_calls": 1, "patches_applied": 0,
                "elapsed_s": 0.2, "failed_tool_calls": 0}

    specs = [_make_spec(f"t{i}", _fn) for i in range(6)]
    outcomes, wall_s, label = asyncio.run(run_parallel(
        specs, max_concurrency=max_c, install_signal_handlers=False,
    ))
    assert len(outcomes) == 6
    assert all(o.result["final_test_passed"] for o in outcomes)
    assert in_flight["peak"] <= max_c, (
        f"semaphore breach: peak concurrent = {in_flight['peak']}, max = {max_c}"
    )
    # Wall-clock is bounded below by ceil(6/2) * 0.2 = 0.6s.
    assert wall_s >= 0.55, f"wall-clock {wall_s} too short for the work"
    assert label.startswith("codex-lab-")


# ---------------------------------------------------------------------------
# Per-task timeout — the runner must return a timeout outcome and signal
# cooperative cancellation to the thread (via cancel_event in kwargs).
# ---------------------------------------------------------------------------

def test_per_task_timeout_fires_and_marks_outcome():
    def _slow(task, **kw):
        # Honour the cancel_event so we don't burn CPU after timeout fires.
        cancel = kw.get("cancel_event")
        start = time.time()
        while time.time() - start < 2.0:
            if cancel is not None and cancel.is_set():
                break
            time.sleep(0.05)
        return {"task_id": task["id"], "final_test_passed": True,
                "iterations": 1, "total_tool_calls": 0, "patches_applied": 0,
                "elapsed_s": 2.0, "failed_tool_calls": 0}

    specs = [_make_spec("slow", _slow)]
    outcomes, wall_s, _ = asyncio.run(run_parallel(
        specs, max_concurrency=1, per_task_timeout=0.3,
        install_signal_handlers=False,
    ))
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.timed_out is True
    assert o.result["failure_category"] == "timeout"
    assert o.result["final_test_passed"] is False
    # The runner returns promptly; the background thread may linger but the
    # caller sees <= 0.5s (generous bound for scheduling).
    assert wall_s < 1.0, f"timeout should return fast, wall {wall_s}"


def test_timeout_propagates_cancel_event_to_thread():
    """The runner must signal cancellation by setting the cancel_event that
    it passes into the task kwargs; otherwise the thread would keep burning
    API budget after we've already told the caller it timed out."""
    saw_cancel = {"flag": False}
    done = threading.Event()

    def _check(task, **kw):
        cancel = kw.get("cancel_event")
        start = time.time()
        while time.time() - start < 3.0:
            if cancel is not None and cancel.is_set():
                saw_cancel["flag"] = True
                done.set()
                return {"task_id": task["id"], "final_test_passed": False,
                        "iterations": 1, "total_tool_calls": 0,
                        "patches_applied": 0, "elapsed_s": 0.1,
                        "failed_tool_calls": 0}
            time.sleep(0.05)
        done.set()
        return {"task_id": task["id"], "final_test_passed": True,
                "iterations": 1, "total_tool_calls": 0, "patches_applied": 0,
                "elapsed_s": 3.0, "failed_tool_calls": 0}

    specs = [_make_spec("c", _check)]
    asyncio.run(run_parallel(
        specs, max_concurrency=1, per_task_timeout=0.2,
        install_signal_handlers=False,
    ))
    # Give the background thread up to 1.5s to notice the cancel flag.
    done.wait(timeout=1.5)
    assert saw_cancel["flag"], "cancel_event was never set in the task kwargs"


# ---------------------------------------------------------------------------
# Container labelling — the label returned by run_parallel must be a usable tag
# ---------------------------------------------------------------------------

def test_container_label_is_unique_and_tagged_in_kwargs():
    seen_labels = []

    def _capture(task, **kw):
        seen_labels.append(kw.get("container_label"))
        return {"task_id": task["id"], "final_test_passed": True,
                "iterations": 1, "total_tool_calls": 0, "patches_applied": 0,
                "elapsed_s": 0.0, "failed_tool_calls": 0}

    specs = [_make_spec(f"t{i}", _capture) for i in range(4)]
    _outcomes, _wall, label = asyncio.run(run_parallel(
        specs, max_concurrency=2, install_signal_handlers=False,
    ))
    # Every task got the SAME label (so a single kill cleans them all up).
    assert set(seen_labels) == {label}
    # Two independent calls to run_parallel must produce different labels.
    _o2, _w2, label2 = asyncio.run(run_parallel(
        [_make_spec("one", _capture)], max_concurrency=1,
        install_signal_handlers=False,
    ))
    assert label != label2


# ---------------------------------------------------------------------------
# summarize() and speedup() behave sensibly
# ---------------------------------------------------------------------------

def test_summarize_counts_timeouts_and_cancels():
    def _ok(task, **kw):
        return {"task_id": task["id"], "final_test_passed": True,
                "iterations": 1, "total_tool_calls": 0, "patches_applied": 0,
                "elapsed_s": 0.1, "failed_tool_calls": 0}

    # Build a synthetic outcome list — we don't need to run the runner for this.
    outs = [
        ParallelOutcome(result={"task_id": "a"}, duration_s=0.1,
                        timed_out=False, cancelled=False, container_kill_info=None),
        ParallelOutcome(result={"task_id": "b"}, duration_s=0.3,
                        timed_out=True, cancelled=False, container_kill_info={}),
        ParallelOutcome(result={"task_id": "c"}, duration_s=0.0,
                        timed_out=False, cancelled=True, container_kill_info={}),
    ]
    s = summarize(outs, wall_clock_s=0.8)
    assert s["total"] == 3
    assert s["completed_normally"] == 1
    assert s["timed_out"] == 1
    assert s["cancelled"] == 1
    assert s["wall_clock_s"] == 0.8


def test_speedup_math():
    s = speedup(60.0, 20.0)
    assert s["speedup"] == 3.0
    assert s["sequential_wall_s"] == 60.0
    assert s["parallel_wall_s"] == 20.0


def test_speedup_zero_parallel_returns_zero():
    s = speedup(10.0, 0.0)
    assert s["speedup"] == 0.0


# ---------------------------------------------------------------------------
# max_concurrency validation
# ---------------------------------------------------------------------------

def test_max_concurrency_must_be_positive():
    with pytest.raises(ValueError, match="max_concurrency"):
        asyncio.run(run_parallel([], max_concurrency=0,
                                 install_signal_handlers=False))


# ---------------------------------------------------------------------------
# Wall-clock really is shorter when parallelism works
# ---------------------------------------------------------------------------

def test_parallel_wall_clock_is_shorter_than_sum_of_durations():
    """Each 'task' sleeps 0.2s. Four of them run with N=4 → wall ~0.2s,
    while the sum of durations is ~0.8s. The ratio proves parallelism
    actually happened, not just that the API was exercised."""
    def _sleep(task, **_kw):
        time.sleep(0.2)
        return {"task_id": task["id"], "final_test_passed": True,
                "iterations": 1, "total_tool_calls": 0, "patches_applied": 0,
                "elapsed_s": 0.2, "failed_tool_calls": 0}

    specs = [_make_spec(f"t{i}", _sleep) for i in range(4)]
    outcomes, wall_s, _ = asyncio.run(run_parallel(
        specs, max_concurrency=4, install_signal_handlers=False,
    ))
    sum_s = sum(o.duration_s for o in outcomes)
    assert wall_s < sum_s / 2, (
        f"wall-clock {wall_s} is not meaningfully less than sum {sum_s}; "
        f"parallelism may not be firing"
    )
