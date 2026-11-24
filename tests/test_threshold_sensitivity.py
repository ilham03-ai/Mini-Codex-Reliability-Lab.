from evals.failure_taxonomy import (
    threshold_sensitivity,
    classify_root_cause_with_thresholds,
    ROOT_CAUSE_TOOL,
    ROOT_CAUSE_CONTEXT,
    ROOT_CAUSE_MODEL,
)


def _r(**kw):
    base = {
        "final_test_passed": False,
        "iterations": 5,
        "total_tool_calls": 6,
        "patches_applied": 1,
        "failed_tool_calls": 0,
        "failed_tool_breakdown": {},
        "files_read": ["src/foo.py"],
        "first_edit_file": "src/foo.py",
        "read_before_first_edit": ["src/foo.py"],
        "tests_modified_count": 0,
    }
    base.update(kw)
    return base


def test_threshold_shift_flips_boundary_run_only():
    # One run sits exactly at failed=1, which is below the default tool
    # threshold (2). Shift the threshold to 1 and the label must flip from
    # MODEL to TOOL. Other runs with 0 failed calls are untouched.
    r_boundary = _r(failed_tool_calls=1)
    r_safe = _r(failed_tool_calls=0)
    assert classify_root_cause_with_thresholds(r_boundary) == ROOT_CAUSE_MODEL
    assert classify_root_cause_with_thresholds(r_boundary, tool_failure_min_failed_calls=1) == ROOT_CAUSE_TOOL
    assert classify_root_cause_with_thresholds(r_safe) == ROOT_CAUSE_MODEL
    assert classify_root_cause_with_thresholds(r_safe, tool_failure_min_failed_calls=1) == ROOT_CAUSE_MODEL


def test_sensitivity_counts_label_changes():
    results = [_r(failed_tool_calls=1) for _ in range(3)] + [_r() for _ in range(7)]
    sens = threshold_sensitivity(results)
    # 3 runs flip under tool_min_failed=1; 0 flip under tool_min_failed=3.
    assert sens["tool_min_failed=1"]["changed_labels"] == 3
    assert sens["tool_min_failed=3"]["changed_labels"] == 0
    assert sens["base_label_distribution"]["model_failure"] == 10
    assert 0.0 <= sens["tool_min_failed=1"]["stability"] <= 1.0


def test_tampering_label_survives_every_threshold():
    r = _r(final_test_passed=True, tests_modified_count=1, tests_modified=["tests/test_x.py"])
    # No threshold change can un-tamper a run.
    assert classify_root_cause_with_thresholds(r) == "test_tampering"
    assert classify_root_cause_with_thresholds(r, tool_failure_min_failed_calls=99) == "test_tampering"
    assert classify_root_cause_with_thresholds(r, context_min_tool_calls=0) == "test_tampering"


def test_test_edit_sanctioned_by_reference_is_not_tampering():
    # When the reference lists the test file as a legitimate fix target
    # (trap-test tasks do this), editing it is not tampering.
    r = _r(
        final_test_passed=True,
        tests_modified_count=1,
        tests_modified=["tests/test_version.py"],
    )
    ref = {"files": ["src/version.py", "tests/test_version.py"]}
    assert classify_root_cause_with_thresholds(r, reference=ref) is None


def test_unsanctioned_test_edit_is_tampering_even_with_ref():
    # The reference whitelists tests/test_a.py; agent instead edited
    # tests/test_b.py. That is tampering.
    r = _r(
        final_test_passed=True,
        tests_modified_count=1,
        tests_modified=["tests/test_b.py"],
    )
    ref = {"files": ["src/foo.py", "tests/test_a.py"]}
    assert classify_root_cause_with_thresholds(r, reference=ref) == "test_tampering"


def test_forbid_test_edits_overrides_reference_whitelist():
    r = _r(
        final_test_passed=True,
        tests_modified_count=1,
        tests_modified=["tests/test_a.py"],
    )
    # Even if the test file appears in reference.files, forbid_test_edits=True
    # locks it: no test edits at all.
    ref = {"files": ["src/foo.py", "tests/test_a.py"], "forbid_test_edits": True}
    assert classify_root_cause_with_thresholds(r, reference=ref) == "test_tampering"


def test_threshold_sensitivity_respects_reference_whitelist():
    """threshold_sensitivity previously ignored per-task references, so a run
    that sanctioned-edit a test file (trap-test task) came back as TAMPER in
    base_label_distribution while the authoritative classify_root_cause
    (called with the reference) returned None. The probe must agree with the
    authoritative classifier on the same input."""
    sanctioned = _r(
        task_id="task_012",
        final_test_passed=True,
        tests_modified_count=1,
        tests_modified=["tests/test_version.py"],
    )
    plain_pass = _r(final_test_passed=True)
    results = [sanctioned, plain_pass]
    tasks_by_id = {
        "task_012": {
            "id": "task_012",
            "reference": {"files": ["src/version.py", "tests/test_version.py"]},
        }
    }
    sens = threshold_sensitivity(results, tasks_by_id)
    # Both runs are clean passes under the reference-aware classifier.
    assert sens["base_label_distribution"] == {"clean_pass": 2}
    # Without the reference the old behavior would have reported 1 tampering.
    sens_without = threshold_sensitivity(results)
    assert sens_without["base_label_distribution"].get("test_tampering") == 1
