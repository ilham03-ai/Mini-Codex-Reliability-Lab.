"""The eval runner (`run_task`) must never mutate the source workspace.

This mirrors the baseline's own `test_baseline_never_mutates_source_tree`
invariant. The baseline had a test for it; the agent-driven runner didn't —
exactly the asymmetry that bit us before, when only `--isolated` copied and
run 2+ silently reused a dirty workspace.

The test uses a fake anthropic client so no API call fires, and seeds the
fake model with ONE tool call that writes "+2" into a file inside what it
thinks is the workspace. The source tree hash must stay the same afterwards.
"""
import hashlib
import json
import sys
import types
from pathlib import Path

import pytest


def _digest(root: Path) -> list[tuple[str, str]]:
    parts = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(x in p.parts for x in ("__pycache__", ".pytest_cache")):
            continue
        parts.append((str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest()))
    return parts


def _make_workspace(tmp_path: Path) -> Path:
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


class _TextBlock:
    type = "text"
    def __init__(self, text):
        self.text = text


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


def _install_fake_anthropic(monkeypatch, turns_iter):
    class _FakeClient:
        def __init__(self):
            class _M:
                def create(self2, **_kwargs):
                    return next(turns_iter)
            self.messages = _M()
    fake = types.ModuleType("anthropic")
    fake.Anthropic = _FakeClient
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    import agent.planner as planner
    monkeypatch.setattr(planner, "create_plan", lambda *a, **kw: "")


def test_run_task_does_not_touch_source_tree(monkeypatch, tmp_path):
    """Model performs an edit_file tool call — the source workspace must be
    untouched afterwards; only the tmp copy inside run_task sees the edit."""
    ws = _make_workspace(tmp_path)

    # Agent: on turn 1 edit the file, on turn 2 run tests, then end.
    turns = iter([
        _FakeResp(
            [_ToolUseBlock("c1", "edit_file", {
                "path": "src/m.py",
                "old_content": "return 1",
                "new_content": "return 2",  # would turn tests red; irrelevant for the invariant
            })],
            "tool_use",
        ),
        _FakeResp(
            [_ToolUseBlock("c2", "run_tests", {"command": "pytest tests/ -q"})],
            "tool_use",
        ),
        _FakeResp([_TextBlock("done")], "end_turn"),
    ])
    _install_fake_anthropic(monkeypatch, turns)

    before = _digest(ws)

    # Build a tasks.jsonl-style entry for run_task.
    task = {
        "id": "t_inv",
        "title": "invariant check",
        "issue": "fix f in src/m.py",
        "workspace": str(ws),
        "reference": {"files": ["src/m.py"]},
    }
    # We must patch run_task's ROOT-relative workspace lookup: it does
    # `ROOT / task["workspace"]`. Passing an absolute path works because
    # `Path(ROOT) / abs` == abs in pathlib.
    from evals.run_eval import run_task
    result = run_task(task, isolated=False, verbose=False)

    after = _digest(ws)
    assert before == after, (
        f"source workspace mutated by run_task:\n"
        f"  before: {before}\n"
        f"  after : {after}"
    )
    # Sanity: run_task did produce some output.
    assert "task_id" in result
    assert result["task_id"] == "t_inv"
