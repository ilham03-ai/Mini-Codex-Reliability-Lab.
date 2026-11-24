"""Verify that run_agent's final verification step reuses the last successful
tool-call pytest instead of paying for another pytest run, and that it still
runs a fresh pytest when the last tool call wasn't a green run_tests.

These tests exercise `run_agent` without an API key by patching anthropic.
"""
import json
import sys
import types
from pathlib import Path

import pytest


class _FakeContent:
    def __init__(self, blocks):
        self.content = blocks
        # end_turn once the model has nothing else to do.
        self.stop_reason = "end_turn"


class _TextBlock:
    type = "text"
    def __init__(self, text):
        self.text = text


class _FakeClient:
    def __init__(self):
        class _M:
            def create(self2, **_kwargs):
                return _FakeContent([_TextBlock("done")])
        self.messages = _M()


class _FakePlannerClient(_FakeClient):
    pass


def _patch_anthropic(monkeypatch):
    fake = types.ModuleType("anthropic")
    fake.Anthropic = _FakeClient
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    # also stub out planner.create_plan so it doesn't try to call the client.
    import agent.planner as planner
    monkeypatch.setattr(planner, "create_plan", lambda *a, **kw: "")


def _tiny_workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "src" / "m.py").write_text("def f():\n    return 1\n")
    (tmp_path / "tests" / "test_m.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
        "from src.m import f\n"
        "def test_f(): assert f() == 1\n"
    )
    return tmp_path


def test_final_verification_runs_pytest_when_no_prior_run(monkeypatch, tmp_path):
    """When the agent ends its loop with zero run_tests calls, the final
    verification MUST run pytest itself — the harness can't claim pass
    without an actual test run."""
    _patch_anthropic(monkeypatch)
    ws = _tiny_workspace(tmp_path)

    from agent.loop import run_agent
    result = run_agent(
        issue="noop",
        workspace=str(ws),
        max_iterations=1,
        use_planner=False,
        use_resolver=False,
    )
    assert result["final_test_passed"] is True
    # No reuse happened — there was no prior run_tests to borrow from.
    assert not result.get("test_results") or all(
        not r.get("reused_from_last_tool_call") for r in result["test_results"]
    )


def test_final_verification_reuses_last_green_run_tests(monkeypatch, tmp_path):
    """When the agent's last tool call is a green run_tests, the harness must
    reuse that result instead of launching another pytest. We prove this by
    counting the number of run_tests invocations the harness makes."""
    _patch_anthropic(monkeypatch)
    ws = _tiny_workspace(tmp_path)

    from agent import loop as loop_mod

    # Count calls to the actual sandbox runner.
    call_count = {"n": 0}
    original = loop_mod.run_tests
    def _counted(*a, **kw):
        call_count["n"] += 1
        return original(*a, **kw)
    monkeypatch.setattr(loop_mod, "run_tests", _counted)

    # Fake model: on turn 1 emit a run_tests tool call; on turn 2 end_turn.
    class _ToolUseBlock:
        type = "tool_use"
        def __init__(self, id, name, input):
            self.id = id
            self.name = name
            self.input = input

    class _FakeResp:
        def __init__(self, blocks, stop_reason):
            self.content = blocks
            self.stop_reason = stop_reason

    turns = iter([
        _FakeResp([_ToolUseBlock("call_1", "run_tests", {"command": "pytest tests/ -q"})], "tool_use"),
        _FakeResp([_TextBlock("tests pass")], "end_turn"),
    ])

    class _FakeClient2:
        def __init__(self):
            class _M:
                def create(self2, **_kwargs):
                    return next(turns)
            self.messages = _M()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _FakeClient2
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    result = loop_mod.run_agent(
        issue="noop",
        workspace=str(ws),
        max_iterations=3,
        use_planner=False,
        use_resolver=False,
    )
    assert result["final_test_passed"] is True
    # Exactly ONE actual pytest invocation: the agent's own run_tests tool call.
    # The final verification reused that result instead of running pytest twice.
    assert call_count["n"] == 1, (
        f"expected 1 pytest invocation (reuse last tool call), got {call_count['n']}"
    )
