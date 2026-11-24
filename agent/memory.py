import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    name: str
    input: dict
    result: Any
    elapsed_s: float
    success: bool = True
    error: str = ""
    timestamp: float = field(default_factory=time.time)


def _looks_like_test_file(path: str) -> bool:
    """True if `path` is under a tests/ dir or looks like a pytest config.

    Case-sensitive on segments, since Python package names conventionally are.
    """
    if not path:
        return False
    parts = path.replace("\\", "/").split("/")
    if "tests" in parts or "test" in parts:
        return True
    base = parts[-1]
    if base.startswith("test_") and base.endswith(".py"):
        return True
    if base == "conftest.py":
        return True
    return False


class AgentMemory:
    def __init__(self, issue: str, workspace: str):
        self.issue = issue
        self.workspace = workspace
        self.plan: str = ""
        self.tool_calls: list[ToolCall] = []
        self.iterations: int = 0
        self.patches_applied: list[dict] = []
        # Stored as records (path + diff) for debugging; the public to_dict()
        # view flattens this to a list[str] under `tests_modified`. Keep the
        # internal name prefixed so a caller importing AgentMemory and
        # reading `.tests_modified` directly gets a clear signal that the
        # shape here is not the public one.
        self._tests_modified_records: list[dict] = []
        self.test_results: list[dict] = []
        self.final_response: str = ""
        self.final_test_result: dict = {}
        self.start_time: float = time.time()

    def set_plan(self, plan: str) -> None:
        self.plan = plan

    def increment_iteration(self) -> None:
        self.iterations += 1

    def record_tool_call(self, name: str, inp: dict, result: Any, elapsed: float) -> None:
        # "success" here means "the tool contract was honored" — NOT "the test suite passed".
        # For run_tests, pytest returning a non-zero exit with real test failures is still
        # a successful tool call (the sandbox did what the agent asked). A tool-contract
        # failure is: the command couldn't be executed, timed out, was rejected by the
        # sandbox, or pytest couldn't even collect any tests (shell-metachar stripped,
        # wrong path, etc.).
        success = True
        error = ""
        if isinstance(result, dict):
            if name == "inspect_error":
                success = True
            elif name == "run_tests":
                if result.get("error"):
                    success = False
                    error = str(result["error"])[:300]
                else:
                    # pytest ran; check whether it could actually collect tests.
                    stdout = result.get("stdout", "")
                    stderr = result.get("stderr", "")
                    collection_markers = (
                        "no tests ran",
                        "ERROR: file or directory not found",
                        "INTERNALERROR",
                        "ERROR: not found:",
                    )
                    if any(m in stdout or m in stderr for m in collection_markers):
                        success = False
                        error = f"pytest could not collect tests: {(stderr or stdout)[:200]}"
                    else:
                        success = True  # even if tests failed, the tool worked
            else:
                success = bool(result.get("success", True))
                if not success:
                    error = str(result.get("error", ""))[:300]
        self.tool_calls.append(ToolCall(
            name=name, input=inp, result=result, elapsed_s=elapsed,
            success=success, error=error,
        ))
        if name == "edit_file" and isinstance(result, dict) and result.get("success"):
            path = inp.get("path", "")
            self.patches_applied.append({"path": path, "diff": result.get("diff", "")})
            # Edits under tests/ or on a conftest.py are suspect. Patching the
            # source to match the test is a fix; patching the test to match a
            # bug is a cheat. We can't tell intent here, but we track the paths
            # so the eval can flag "passed with tests modified" downstream.
            if _looks_like_test_file(path):
                self._tests_modified_records.append({"path": path, "diff": result.get("diff", "")})
        if name == "run_tests" and isinstance(result, dict):
            self.test_results.append(result)

    def set_final_response(self, text: str) -> None:
        self.final_response = text

    def set_final_test_result(self, result: dict) -> None:
        self.final_test_result = result

    def elapsed(self) -> float:
        return time.time() - self.start_time

    def summary(self) -> dict:
        """Short, stable set of fields suitable for a tabular report row.

        Nothing here grows with run length. Add a field only when a human
        would want it in a one-glance summary; anything a downstream
        classifier needs goes in `diagnostics()`.
        """
        last_test = self.final_test_result or (self.test_results[-1] if self.test_results else {})
        return {
            "issue": self.issue,
            "workspace": self.workspace,
            "iterations": self.iterations,
            "total_tool_calls": len(self.tool_calls),
            "patches_applied": len(self.patches_applied),
            "tests_modified_count": len(self._tests_modified_records),
            "final_test_passed": last_test.get("success", False),
            "final_verification_reused": bool(last_test.get("reused_from_last_tool_call", False)),
            "elapsed_s": round(self.elapsed(), 2),
        }

    def diagnostics(self) -> dict:
        """Everything a classifier might want.

        Shape may grow as classifiers are added. Not meant to be read by
        humans directly; the report pulls what it needs via named accessors.
        """
        failed_calls = [c for c in self.tool_calls if not c.success]
        files_read = sorted({
            c.input.get("path", "") for c in self.tool_calls
            if c.name == "read_file" and c.success and c.input.get("path")
        })
        first_edit_file = next(
            (c.input.get("path", "") for c in self.tool_calls
             if c.name == "edit_file" and c.success),
            "",
        )
        read_before_first_edit = []
        for c in self.tool_calls:
            if c.name == "edit_file" and c.success:
                break
            if c.name == "read_file" and c.success:
                p = c.input.get("path", "")
                if p:
                    read_before_first_edit.append(p)
        return {
            "tool_call_breakdown": _count_tools(self.tool_calls),
            "failed_tool_calls": len(failed_calls),
            "failed_tool_breakdown": _count_tools(failed_calls),
            "failed_tool_errors": [
                {"name": c.name, "error": c.error, "input_keys": list(c.input.keys())}
                for c in failed_calls
            ],
            "files_read": files_read,
            "first_edit_file": first_edit_file,
            "read_before_first_edit": sorted(set(read_before_first_edit)),
            "tests_modified": [t["path"] for t in self._tests_modified_records],
        }

    def raw(self) -> dict:
        """Heavy fields: patch diffs, pytest output, model's final message.

        These blow up JSON size. Kept together so a lightweight report can
        drop them on write.
        """
        return {
            "plan": self.plan,
            "test_results": self.test_results,
            "final_test_result": self.final_test_result,
            "final_response": self.final_response,
            "patches": self.patches_applied,
        }

    def to_dict(self, view: str = "flat") -> dict:
        """Serialize the run.

        view="flat" (default, back-compat): one flat dict with every field at
            top level. Classifiers downstream still read it this way.
        view="nested": { "summary": {...}, "diagnostics": {...}, "raw": {...} }
            for consumers that want the groups.
        """
        summary = self.summary()
        diagnostics = self.diagnostics()
        raw = self.raw()
        if view == "nested":
            return {"summary": summary, "diagnostics": diagnostics, "raw": raw}
        # Flat merge — summary takes precedence if a key ever collides.
        out = {**raw, **diagnostics, **summary}
        return out


def _count_tools(calls: list[ToolCall]) -> dict:
    counts: dict[str, int] = {}
    for c in calls:
        counts[c.name] = counts.get(c.name, 0) + 1
    return counts
