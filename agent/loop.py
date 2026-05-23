import json
import shlex
import sys
import time
from typing import Callable, Optional

from .tools import read_file, search_code, edit_file, run_tests, inspect_error
from sandbox.permissions import EVAL_POLICY, Permission, PermissionPolicy, check_permission


def _final_verification_command(force_docker: bool) -> str:
    # sys.executable is a host path — doesn't exist inside the container
    if force_docker:
        return "python -m pytest tests/ -v"
    return f"{shlex.quote(sys.executable)} -m pytest tests/ -v"

# A confirm callback receives (tool_name, tool_input) and returns True to allow, False to deny.
ConfirmCallback = Callable[[str, dict], bool]

TOOLS = [
    {
        "name": "read_file",
        "description": "Read the content of a file from the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_code",
        "description": "Search for a string or pattern across all Python files in the workspace. Returns matching lines with file and line number.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for"},
                "file_pattern": {
                    "type": "string",
                    "description": "Glob pattern for files to search (default: *.py)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Apply a targeted edit to a file by replacing old_content with new_content. "
            "The old_content must match exactly what is currently in the file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace root"},
                "old_content": {"type": "string", "description": "Exact text to replace"},
                "new_content": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_content", "new_content"],
        },
    },
    {
        "name": "run_tests",
        "description": "Run tests in the workspace. Use pytest. Returns stdout/stderr and pass/fail status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run tests (e.g., 'pytest tests/ -v')",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "inspect_error",
        "description": "Parse and analyze an error log or test output. Extracts error types, tracebacks, and suggests fixes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "log": {"type": "string", "description": "Error log or test failure output to analyze"},
            },
            "required": ["log"],
        },
    },
]


def _dispatch(
    tool_name: str,
    tool_input: dict,
    workspace: str,
    policy: PermissionPolicy = EVAL_POLICY,
    force_docker: bool = False,
    confirm: Optional[ConfirmCallback] = None,
) -> tuple[str, float]:
    perm = check_permission(policy, tool_name)
    if perm == Permission.DENY:
        result = {"success": False, "error": f"Tool '{tool_name}' denied by policy"}
        return json.dumps(result, indent=2), 0.0
    if perm == Permission.CONFIRM:
        if confirm is None:
            result = {
                "success": False,
                "error": (
                    f"Tool '{tool_name}' requires confirmation (policy={perm.value}) but no "
                    "confirm callback was provided. Pass `confirm=` to run_agent/_dispatch."
                ),
            }
            return json.dumps(result, indent=2), 0.0
        try:
            approved = bool(confirm(tool_name, tool_input))
        except Exception as e:
            result = {"success": False, "error": f"Confirm callback raised: {e}"}
            return json.dumps(result, indent=2), 0.0
        if not approved:
            result = {"success": False, "error": f"Tool '{tool_name}' denied by confirm callback"}
            return json.dumps(result, indent=2), 0.0
    elif perm != Permission.ALLOW:
        result = {"success": False, "error": f"Unexpected permission value: {perm!r}"}
        return json.dumps(result, indent=2), 0.0

    start = time.time()
    try:
        if tool_name == "read_file":
            result = read_file(tool_input["path"], workspace)
        elif tool_name == "search_code":
            result = search_code(
                tool_input["query"],
                workspace,
                tool_input.get("file_pattern", "*.py"),
            )
        elif tool_name == "edit_file":
            result = edit_file(
                tool_input["path"],
                tool_input["old_content"],
                tool_input["new_content"],
                workspace,
            )
        elif tool_name == "run_tests":
            result = run_tests(tool_input["command"], workspace, force_docker=force_docker)
        elif tool_name == "inspect_error":
            result = inspect_error(tool_input["log"])
        else:
            result = {"success": False, "error": f"Unknown tool: {tool_name}"}
    except Exception as e:
        result = {"success": False, "error": str(e)}

    elapsed = round(time.time() - start, 3)
    return json.dumps(result, indent=2), elapsed


def run_agent(
    issue: str,
    workspace: str,
    max_iterations: int = 15,
    verbose: bool = False,
    force_docker: bool = False,
    policy: PermissionPolicy = EVAL_POLICY,
    confirm: Optional[ConfirmCallback] = None,
) -> dict:
    """Run the coding agent on an issue and return metrics."""
    # Imports are local so that framework tests (and any consumer that only uses _dispatch
    # / permission types) can import this module without the anthropic SDK installed.
    import anthropic
    from .memory import AgentMemory
    from .planner import create_plan

    client = anthropic.Anthropic()
    memory = AgentMemory(issue, workspace)

    plan = create_plan(client, issue, workspace)
    memory.set_plan(plan)

    if verbose:
        print(f"\n📋 Plan:\n{plan}\n")

    system = f"""You are a software debugging agent. Fix bugs in Python code.

Issue: {issue}

Plan: {plan}

Read the relevant code first, then apply a targeted edit and run tests. If they still fail, read the error and iterate. Don't declare success until you see passing tests. Keep patches minimal — change only what's broken.
"""

    messages = [{"role": "user", "content": f"Fix this issue:\n\n{issue}"}]

    for _ in range(max_iterations):
        memory.increment_iteration()

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        if verbose:
            for block in response.content:
                if hasattr(block, "text"):
                    print(f"🤖 {block.text[:300]}")

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if b.type == "text"), "")
            memory.set_final_response(text)
            break

        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_blocks:
            break

        messages.append({"role": "assistant", "content": response.content})

        results = []
        for tb in tool_blocks:
            if verbose:
                print(f"🔧 {tb.name}({list(tb.input.keys())})")
            output, elapsed = _dispatch(
                tb.name, tb.input, workspace,
                policy=policy, force_docker=force_docker, confirm=confirm,
            )
            memory.record_tool_call(tb.name, tb.input, json.loads(output), elapsed)
            results.append({
                "type": "tool_result",
                "tool_use_id": tb.id,
                "content": output,
            })

        messages.append({"role": "user", "content": results})

    # Final verification test run — pick a command that actually resolves to pytest
    # in the target environment (local vs Docker).
    final = run_tests(_final_verification_command(force_docker), workspace, force_docker=force_docker)
    memory.set_final_test_result(final)

    return memory.to_dict()
