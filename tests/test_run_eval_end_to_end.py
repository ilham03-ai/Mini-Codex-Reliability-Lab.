"""End-to-end smoke test for `run_eval.main()` with a fake anthropic client.

Unit tests cover the classifier, memory views, resolver, baseline and
run_task invariant separately. What they do NOT cover is the full
`run_eval` pipeline writing a JSON file with the shape downstream readers
(reports, dashboards, future tooling) depend on. This test runs the whole
thing on a 1-task fixture and locks the invariant that the following
fields reach the output JSON:

  - results[i].config.model
  - results[i].config.planner_model
  - results[i].final_verification_reused
  - results[i].root_cause
  - results[i].reference_match.fix_match
  - metrics.per_task_aggregate[i].fix_match_distribution
  - sensitivity JSON exists next to the main output

If any of these keys silently disappears in a refactor, this test fails.
"""
import hashlib
import json
import sys
import types
from pathlib import Path

import pytest


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


def _install_fake_anthropic(monkeypatch, turns_factory):
    """`turns_factory` is called once per `Anthropic()` instantiation and
    must return a fresh iterator of _FakeResp objects."""
    class _FakeClient:
        def __init__(self):
            self._turns = turns_factory()
            parent = self
            class _M:
                def create(self2, **_kwargs):
                    return next(parent._turns)
            self.messages = _M()
    fake = types.ModuleType("anthropic")
    fake.Anthropic = _FakeClient
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    # Planner is called via create_plan; stub it so planner-model wiring
    # doesn't require a working SDK call. The test still verifies the
    # config dict carries the right planner_model value.
    import agent.planner as planner
    monkeypatch.setattr(planner, "create_plan", lambda *a, **kw: "")


def _make_task_workspace(root: Path, task_id: str) -> Path:
    ws = root / task_id
    (ws / "src").mkdir(parents=True)
    (ws / "tests").mkdir()
    (ws / "src" / "__init__.py").write_text("")
    (ws / "tests" / "__init__.py").write_text("")
    (ws / "src" / "m.py").write_text("def f():\n    return 1\n")
    (ws / "tests" / "test_m.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
        "from src.m import f\n"
        "def test_f(): assert f() == 1\n"
    )
    return ws


def test_run_eval_writes_expected_shape_to_output_json(monkeypatch, tmp_path):
    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir()
    ws = _make_task_workspace(workspaces_root, "task_zzz")

    # tasks.jsonl pointing at that one workspace (absolute path so it
    # bypasses the ROOT-relative join in run_task).
    tasks_file = tmp_path / "tasks.jsonl"
    tasks_file.write_text(
        json.dumps({
            "id": "task_zzz",
            "title": "noop smoke task",
            "issue": "noop test for the smoke run",
            "workspace": str(ws),
            "source": "author_designed",
            "reference": {"files": ["src/m.py"]},
        }) + "\n"
    )

    # Fake model: on turn 1 emit a run_tests tool call (so reuse kicks in);
    # on turn 2 end_turn.
    def _turns():
        return iter([
            _FakeResp(
                [_ToolUseBlock("c1", "run_tests", {"command": "pytest tests/ -q"})],
                "tool_use",
            ),
            _FakeResp([_TextBlock("done")], "end_turn"),
        ])
    _install_fake_anthropic(monkeypatch, _turns)

    # Point run_eval at the ad-hoc tasks.jsonl and prepare CLI args.
    import evals.run_eval as run_eval
    monkeypatch.setattr(run_eval, "TASKS_FILE", tasks_file)
    output_json = tmp_path / "eval_out.json"
    monkeypatch.setattr(sys, "argv", [
        "run_eval",
        "--output", str(output_json),
        "--local",            # tests don't need Docker
        "--repeats", "1",
        "--model", "claude-sonnet-4-6",
    ])

    run_eval.main()

    # --- Output JSON exists and has the expected shape ---
    assert output_json.exists()
    data = json.loads(output_json.read_text())
    assert "metrics" in data and "results" in data
    results = data["results"]
    assert len(results) == 1
    r0 = results[0]

    # Config block carries the model knobs.
    assert "config" in r0
    assert r0["config"]["model"] == "claude-sonnet-4-6"
    assert r0["config"]["planner_model"] == "claude-sonnet-4-6"
    assert "use_planner" in r0["config"]

    # Final verification reused the agent's green run_tests → flag is true.
    assert r0.get("final_verification_reused") is True

    # Root cause classification ran and attached a field (None for clean pass).
    assert "root_cause" in r0

    # Reference-based classification ran and emitted a fix_match.
    from evals.failure_taxonomy import FIX_MATCH_LABELS
    assert r0["reference_match"]["fix_match"] in FIX_MATCH_LABELS
    # Exact label for this scenario: agent emitted zero patches (only a
    # run_tests call) → fix_match must be no_patch, not a weaker/stronger
    # label. Guards against a future refactor that collapses the taxonomy.
    assert r0["reference_match"]["fix_match"] == "no_patch", (
        "agent did zero edits — the expected label is no_patch, not "
        f"{r0['reference_match']['fix_match']}"
    )

    # Per-task aggregate carries fix_match_distribution.
    agg = data["metrics"]["per_task_aggregate"]
    assert isinstance(agg, list) and len(agg) == 1
    assert "fix_match_distribution" in agg[0]
    assert sum(agg[0]["fix_match_distribution"].values()) == 1

    # Sidecar sensitivity JSON lives next to the main output.
    sens_path = output_json.with_name(output_json.stem + "_sensitivity.json")
    assert sens_path.exists()
    sens = json.loads(sens_path.read_text())
    assert "base_label_distribution" in sens


def test_run_eval_source_tree_untouched_after_main(monkeypatch, tmp_path):
    """run_eval.main() over a task must not mutate the task's source workspace."""
    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir()
    ws = _make_task_workspace(workspaces_root, "task_zzz")

    tasks_file = tmp_path / "tasks.jsonl"
    tasks_file.write_text(json.dumps({
        "id": "task_zzz",
        "title": "noop",
        "issue": "noop",
        "workspace": str(ws),
        "reference": {"files": ["src/m.py"]},
    }) + "\n")

    # Fake model: emit a REAL edit_file that would mutate the file; then end.
    def _turns():
        return iter([
            _FakeResp(
                [_ToolUseBlock("c1", "edit_file", {
                    "path": "src/m.py",
                    "old_content": "return 1",
                    "new_content": "return 999",
                })],
                "tool_use",
            ),
            _FakeResp([_TextBlock("done")], "end_turn"),
        ])
    _install_fake_anthropic(monkeypatch, _turns)

    def _digest(root: Path):
        parts = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            if any(x in p.parts for x in ("__pycache__", ".pytest_cache")):
                continue
            parts.append((str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest()))
        return parts

    before = _digest(ws)

    import evals.run_eval as run_eval
    monkeypatch.setattr(run_eval, "TASKS_FILE", tasks_file)
    output_json = tmp_path / "eval_out.json"
    monkeypatch.setattr(sys, "argv", [
        "run_eval", "--output", str(output_json), "--local", "--repeats", "1",
    ])
    run_eval.main()

    after = _digest(ws)
    assert before == after, (
        "run_eval.main() mutated the source workspace — tmp copy isolation is broken"
    )
