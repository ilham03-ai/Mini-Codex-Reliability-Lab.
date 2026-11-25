from evals.failure_taxonomy import (
    classify_root_cause,
    build_root_cause_report,
    ROOT_CAUSE_TOOL,
    ROOT_CAUSE_CONTEXT,
    ROOT_CAUSE_MODEL,
    ROOT_CAUSE_TAMPER,
    ROOT_CAUSE_NO_PATCH,
    ROOT_CAUSE_HARNESS_ERROR,
)


def _result(**overrides):
    base = {
        "task_id": "task_x",
        "final_test_passed": False,
        "iterations": 5,
        "total_tool_calls": 8,
        "patches_applied": 1,
        "failed_tool_calls": 0,
        "failed_tool_breakdown": {},
        "files_read": ["src/foo.py"],
        "first_edit_file": "src/foo.py",
        "read_before_first_edit": ["src/foo.py"],
        "tests_modified_count": 0,
        "tests_modified": [],
    }
    base.update(overrides)
    return base


def test_success_returns_none():
    assert classify_root_cause(_result(final_test_passed=True)) is None


def test_two_failed_calls_is_tool_failure():
    r = _result(failed_tool_calls=2, failed_tool_breakdown={"edit_file": 2})
    assert classify_root_cause(r) == ROOT_CAUSE_TOOL


def test_zero_patches_all_edit_rejections_is_tool_failure():
    r = _result(
        patches_applied=0,
        failed_tool_calls=1,
        failed_tool_breakdown={"edit_file": 1},
        first_edit_file="",
        read_before_first_edit=[],
    )
    assert classify_root_cause(r) == ROOT_CAUSE_TOOL


def test_very_few_tool_calls_is_context_failure():
    r = _result(total_tool_calls=2, patches_applied=1)
    assert classify_root_cause(r) == ROOT_CAUSE_CONTEXT


def test_edit_without_reading_file_is_context_failure():
    r = _result(
        first_edit_file="src/bar.py",
        read_before_first_edit=["src/other.py"],
    )
    assert classify_root_cause(r) == ROOT_CAUSE_CONTEXT


def test_patched_right_file_after_reading_is_model_failure():
    # Tools worked, context was collected, patch landed, tests still red.
    r = _result(
        first_edit_file="src/foo.py",
        read_before_first_edit=["src/foo.py"],
        failed_tool_calls=0,
        total_tool_calls=10,
        patches_applied=2,
    )
    assert classify_root_cause(r) == ROOT_CAUSE_MODEL


def test_tool_priority_over_context():
    # Both fingerprints: edited without reading AND two tool failures.
    # Tool wins (priority order: tool > context > model).
    r = _result(
        failed_tool_calls=2,
        failed_tool_breakdown={"edit_file": 2},
        first_edit_file="src/bar.py",
        read_before_first_edit=[],
    )
    assert classify_root_cause(r) == ROOT_CAUSE_TOOL


def test_single_failed_edit_followed_by_success_is_model():
    # Agent fumbled one edit but recovered — this is normal and shouldn't
    # count as a tool failure of the loop.
    r = _result(
        failed_tool_calls=1,
        failed_tool_breakdown={"edit_file": 1},
        patches_applied=1,
    )
    assert classify_root_cause(r) == ROOT_CAUSE_MODEL


def test_zero_patches_no_failed_calls_is_no_patch():
    """A failing run that produced NO patches and had NO tool-contract
    failures is distinct from TOOL/CONTEXT/MODEL: the agent (or baseline)
    simply never emitted a fix. Separate bucket."""
    r = _result(
        patches_applied=0,
        failed_tool_calls=0,
        first_edit_file="",
        read_before_first_edit=[],
    )
    assert classify_root_cause(r) == ROOT_CAUSE_NO_PATCH


def test_zero_patches_with_edit_rejections_still_tool():
    """If patches=0 but every attempt was bounced by edit_file rejections,
    that's a tool failure (there WAS an attempt), not no_patch."""
    r = _result(
        patches_applied=0,
        failed_tool_calls=1,
        failed_tool_breakdown={"edit_file": 1},
        first_edit_file="",
        read_before_first_edit=[],
    )
    assert classify_root_cause(r) == ROOT_CAUSE_TOOL


def test_api_exception_is_harness_error_not_no_patch():
    """A run that crashed before executing (API error, credit exhaustion,
    harness exception) has patches=0 AND tool_calls=0 AND an `error` field
    populated. The classifier must NOT confuse this with 'the agent chose
    not to patch' — harness errors are their own bucket."""
    r = _result(
        final_test_passed=False,
        patches_applied=0,
        total_tool_calls=0,
        failed_tool_calls=0,
        failed_tool_breakdown={},
        first_edit_file="",
        read_before_first_edit=[],
        failure_category="exception",
        error="Error code: 400 - credit balance too low",
    )
    assert classify_root_cause(r) == ROOT_CAUSE_HARNESS_ERROR


def test_harness_error_detected_via_error_field_alone():
    r = _result(
        final_test_passed=False,
        patches_applied=0,
        total_tool_calls=0,
        error="some other exception during setup",
    )
    assert classify_root_cause(r) == ROOT_CAUSE_HARNESS_ERROR


def test_harness_error_priority_above_tool_failure():
    """Even if a run has many refused tool calls, if the error field is set
    the classifier labels it HARNESS_ERROR (the tool-call count is incidental
    to a run that was aborted)."""
    r = _result(
        final_test_passed=False,
        failed_tool_calls=99,
        failed_tool_breakdown={"edit_file": 99},
        error="exception mid-run",
    )
    assert classify_root_cause(r) == ROOT_CAUSE_HARNESS_ERROR


def test_pass_with_test_modification_is_tampering():
    # A run that passed only because it edited the test suite is NOT a success.
    r = _result(
        final_test_passed=True,
        tests_modified_count=1,
        tests_modified=["tests/test_formatter.py"],
    )
    assert classify_root_cause(r) == ROOT_CAUSE_TAMPER


def test_build_report_counts_and_per_task():
    results = [
        _result(task_id="a", final_test_passed=True),
        _result(task_id="b", failed_tool_calls=2,
                failed_tool_breakdown={"edit_file": 2}),
        _result(task_id="c", total_tool_calls=2),
        _result(task_id="d"),  # model failure
        _result(task_id="e", final_test_passed=True,
                tests_modified_count=1,
                tests_modified=["tests/test_e.py"]),  # tampered
    ]
    report = build_root_cause_report(results)
    assert report["total"] == 5
    assert report["solved_clean"] == 1
    assert report["tampered"] == 1
    assert report["failed"] == 3
    assert report["counts"][ROOT_CAUSE_TOOL] == 1
    assert report["counts"][ROOT_CAUSE_CONTEXT] == 1
    assert report["counts"][ROOT_CAUSE_MODEL] == 1
    assert report["counts"][ROOT_CAUSE_TAMPER] == 1
    ids = {t["task_id"]: t["root_cause"] for t in report["per_task"]}
    assert ids == {
        "a": None,
        "b": ROOT_CAUSE_TOOL,
        "c": ROOT_CAUSE_CONTEXT,
        "d": ROOT_CAUSE_MODEL,
        "e": ROOT_CAUSE_TAMPER,
    }
