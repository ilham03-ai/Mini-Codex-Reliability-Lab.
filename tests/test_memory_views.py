"""Test that the three AgentMemory views cover disjoint responsibilities."""
from agent.memory import AgentMemory


def _mem():
    m = AgentMemory("fix it", "/ws")
    m.increment_iteration()
    m.record_tool_call(
        "read_file", {"path": "src/foo.py"},
        {"success": True, "content": "ok"}, 0.01,
    )
    m.record_tool_call(
        "edit_file", {"path": "src/foo.py", "old_content": "a", "new_content": "b"},
        {"success": True, "diff": "- a\n+ b\n"}, 0.02,
    )
    m.record_tool_call(
        "run_tests", {"command": "pytest -q"},
        {"success": True, "returncode": 0, "stdout": "1 passed", "stderr": ""},
        0.5,
    )
    m.set_final_test_result({"success": True, "stdout": "1 passed"})
    return m


def test_summary_is_small_and_stable():
    s = _mem().summary()
    # No field whose length grows with run length.
    assert set(s.keys()) == {
        "issue", "workspace", "iterations", "total_tool_calls",
        "patches_applied", "tests_modified_count", "final_test_passed",
        "final_verification_reused", "elapsed_s",
    }


def test_final_test_result_reaches_raw_and_flat_views():
    m = _mem()
    m.set_final_test_result({
        "success": True, "stdout": "1 passed", "reused_from_last_tool_call": True,
    })
    raw = m.raw()
    assert "final_test_result" in raw
    assert raw["final_test_result"]["reused_from_last_tool_call"] is True
    flat = m.to_dict()
    # final_verification_reused must surface in the flat view so downstream
    # readers see which runs reused the verification vs re-ran pytest.
    assert flat.get("final_verification_reused") is True


def test_diagnostics_holds_classifier_inputs():
    d = _mem().diagnostics()
    assert "failed_tool_calls" in d
    assert "first_edit_file" in d
    assert "files_read" in d
    assert "tool_call_breakdown" in d


def test_raw_holds_the_heavy_fields():
    r = _mem().raw()
    assert "patches" in r
    assert "test_results" in r


def test_flat_view_is_backwards_compatible():
    flat = _mem().to_dict()
    # Keys classifiers read directly must all be present at top level.
    for k in [
        "iterations", "total_tool_calls", "patches_applied",
        "failed_tool_calls", "failed_tool_breakdown", "files_read",
        "first_edit_file", "read_before_first_edit",
        "tests_modified", "tests_modified_count",
        "final_test_passed", "elapsed_s", "patches", "test_results",
    ]:
        assert k in flat, f"missing from flat view: {k}"


def test_nested_view_separates_groups():
    nested = _mem().to_dict(view="nested")
    assert set(nested.keys()) == {"summary", "diagnostics", "raw"}
    assert "final_test_passed" in nested["summary"]
    assert "failed_tool_calls" in nested["diagnostics"]
    assert "patches" in nested["raw"]
