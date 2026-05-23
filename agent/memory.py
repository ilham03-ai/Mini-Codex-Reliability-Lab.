import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    name: str
    input: dict
    result: Any
    elapsed_s: float
    timestamp: float = field(default_factory=time.time)


class AgentMemory:
    def __init__(self, issue: str, workspace: str):
        self.issue = issue
        self.workspace = workspace
        self.plan: str = ""
        self.tool_calls: list[ToolCall] = []
        self.iterations: int = 0
        self.patches_applied: list[dict] = []
        self.test_results: list[dict] = []
        self.final_response: str = ""
        self.final_test_result: dict = {}
        self.start_time: float = time.time()

    def set_plan(self, plan: str) -> None:
        self.plan = plan

    def increment_iteration(self) -> None:
        self.iterations += 1

    def record_tool_call(self, name: str, inp: dict, result: Any, elapsed: float) -> None:
        self.tool_calls.append(ToolCall(name=name, input=inp, result=result, elapsed_s=elapsed))
        if name == "edit_file" and isinstance(result, dict) and result.get("success"):
            self.patches_applied.append({"path": inp.get("path"), "diff": result.get("diff", "")})
        if name == "run_tests" and isinstance(result, dict):
            self.test_results.append(result)

    def set_final_response(self, text: str) -> None:
        self.final_response = text

    def set_final_test_result(self, result: dict) -> None:
        self.final_test_result = result

    def elapsed(self) -> float:
        return time.time() - self.start_time

    def to_dict(self) -> dict:
        last_test = self.final_test_result or (self.test_results[-1] if self.test_results else {})
        return {
            "issue": self.issue,
            "workspace": self.workspace,
            "plan": self.plan,
            "iterations": self.iterations,
            "total_tool_calls": len(self.tool_calls),
            "tool_call_breakdown": _count_tools(self.tool_calls),
            "patches_applied": len(self.patches_applied),
            "test_results": self.test_results,
            "final_test_passed": last_test.get("success", False),
            "final_response": self.final_response,
            "elapsed_s": round(self.elapsed(), 2),
            "patches": self.patches_applied,
        }


def _count_tools(calls: list[ToolCall]) -> dict:
    counts: dict[str, int] = {}
    for c in calls:
        counts[c.name] = counts.get(c.name, 0) + 1
    return counts
