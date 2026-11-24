from evals.metrics import per_task_aggregate


def _r(task_id, passed, iters, tools, failed_tools, elapsed):
    return {
        "task_id": task_id,
        "final_test_passed": passed,
        "iterations": iters,
        "total_tool_calls": tools,
        "failed_tool_calls": failed_tools,
        "elapsed_s": elapsed,
    }


def test_aggregate_single_run_has_zero_stddev():
    agg = per_task_aggregate([_r("t1", True, 5, 6, 0, 25.0)])
    assert len(agg) == 1
    row = agg[0]
    assert row["n"] == 1
    assert row["passes"] == 1
    assert row["pass_rate"] == 1.0
    assert row["iterations_mean"] == 5
    assert row["iterations_std"] == 0.0


def test_aggregate_mixed_runs_compute_rate_and_variance():
    results = [
        _r("t1", True, 4, 5, 0, 20.0),
        _r("t1", True, 6, 7, 1, 30.0),
        _r("t1", False, 15, 20, 2, 60.0),
    ]
    agg = per_task_aggregate(results)
    row = agg[0]
    assert row["n"] == 3
    assert row["passes"] == 2
    assert round(row["pass_rate"], 3) == 0.667
    assert row["iterations_mean"] == 8.33
    assert row["iterations_std"] > 0
    assert row["failed_tool_calls_mean"] == 1.0


def test_aggregate_groups_by_task_id():
    results = [
        _r("a", True, 5, 5, 0, 10),
        _r("b", False, 15, 15, 1, 20),
        _r("a", True, 5, 5, 0, 10),
    ]
    agg = per_task_aggregate(results)
    assert {r["task_id"] for r in agg} == {"a", "b"}
    a_row = next(r for r in agg if r["task_id"] == "a")
    assert a_row["n"] == 2
    assert a_row["pass_rate"] == 1.0


def test_aggregate_exposes_fix_match_distribution():
    """A task where 2/3 seeds got `correct` and 1/3 got `partial` must show up
    as a distribution, not collapse to a single pass-rate number."""
    runs = [
        {**_r("t1", True, 5, 5, 0, 10), "reference_match": {"fix_match": "correct"}},
        {**_r("t1", True, 5, 5, 0, 10), "reference_match": {"fix_match": "correct"}},
        {**_r("t1", True, 5, 5, 0, 10), "reference_match": {"fix_match": "partial"}},
    ]
    agg = per_task_aggregate(runs)
    row = agg[0]
    assert row["fix_match_distribution"] == {"correct": 2, "partial": 1}


def test_aggregate_fix_match_defaults_to_no_reference():
    """Runs without a reference_match field should land in a no_reference
    bucket rather than crashing the aggregator."""
    runs = [_r("t1", True, 5, 5, 0, 10)]
    agg = per_task_aggregate(runs)
    assert agg[0]["fix_match_distribution"] == {"no_reference": 1}
