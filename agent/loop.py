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


MODEL = "claude-sonnet-4-6"


def run_agent(
    issue: str,
    workspace: str,
    max_iterations: int = 15,
    verbose: bool = False,
    force_docker: bool = False,
    policy: PermissionPolicy = EVAL_POLICY,
    confirm: Optional[ConfirmCallback] = None,
    temperature: float = 0.2,
    seed: Optional[int] = None,
    # Ablation toggles — on by default (what the "full" harness has).
    # Flip them off to measure each component's marginal contribution.
    use_planner: bool = True,
    use_resolver: bool = True,
    expose_inspect_error: bool = True,
    exposed_tools: Optional[list] = None,
    model: str = MODEL,
    planner_model: Optional[str] = None,
) -> dict:
    """Run the coding agent on an issue and return metrics.

    The ablation flags are documented in the result under `config`. A run
    whose config differs from another is directly comparable — same model,
    same temperature, same tasks, strictly less harness.
    """
    # Imports are local so that framework tests (and any consumer that only uses _dispatch
    # / permission types) can import this module without the anthropic SDK installed.
    import anthropic
    from .memory import AgentMemory
    from .planner import create_plan
    from .path_resolver import resolve_issue_paths, format_resolution_hint, empty_resolution

    client = anthropic.Anthropic()
    memory = AgentMemory(issue, workspace)

    # --- Path resolver (ablation knob) ---
    if use_resolver:
        path_resolution = resolve_issue_paths(issue, workspace)
        resolution_hint = format_resolution_hint(path_resolution)
    else:
        # Shape-identical empty resolution — so any downstream consumer
        # reading `unresolved_cited` or `unresolved_inferred` doesn't
        # KeyError in the --no-resolver run.
        path_resolution = empty_resolution()
        resolution_hint = ""

    # --- Planner (ablation knob) ---
    # Default the planner to the same model as the loop so --no-planner vs
    # +planner is a clean ablation. Passing planner_model explicitly keeps
    # the mismatch visible in the run config.
    effective_planner_model = planner_model or model
    if use_planner:
        plan = create_plan(client, issue, workspace, model=effective_planner_model)
    else:
        plan = ""  # no plan; the model works from the issue alone
    memory.set_plan(plan)

    if verbose:
        if plan:
            print(f"\nPlan:\n{plan}\n")
        if resolution_hint:
            print(f"Path resolution:\n{resolution_hint}\n")

    # Compose the system prompt. Only include sections whose knob is on.
    system_parts = ["You are a software debugging agent. Fix bugs in Python code.", f"Issue: {issue}"]
    if resolution_hint:
        system_parts.append(resolution_hint)
    if plan:
        system_parts.append(f"Plan: {plan}")
    system_parts.append(
        "Read the relevant code first, then apply a targeted edit and run tests. "
        "If they still fail, read the error and iterate. Don't declare success until "
        "you see passing tests. Keep patches minimal — change only what's broken."
    )
    system = "\n\n".join(system_parts) + "\n"

    # --- Tool surface (ablation knob) ---
    # `exposed_tools` takes precedence over the per-tool switches when set.
    if exposed_tools is not None:
        active_tools = [t for t in TOOLS if t["name"] in exposed_tools]
    else:
        active_tools = list(TOOLS)
        if not expose_inspect_error:
            active_tools = [t for t in active_tools if t["name"] != "inspect_error"]
    active_tool_names = [t["name"] for t in active_tools]

    messages = [{"role": "user", "content": f"Fix this issue:\n\n{issue}"}]

    for _ in range(max_iterations):
        memory.increment_iteration()

        response = client.messages.create(
            model=model,
            max_tokens=8192,
            temperature=temperature,
            system=system,
            tools=active_tools,
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
    #
    # If the tool loop already ran pytest as its last tool call AND that run came
    # back green, reuse that result instead of paying for another pytest run.
    # The agent's own last run already proves "tests pass on the patched tree"
    # inside the same sandbox we would use anyway. Only re-run when the last
    # tool wasn't run_tests, or when it was but failed — in those cases an
    # independent verification is cheap insurance against a flaky stop state.
    last_test = memory.test_results[-1] if memory.test_results else None
    last_tool_call = memory.tool_calls[-1] if memory.tool_calls else None
    if (
        last_test is not None
        and last_tool_call is not None
        and last_tool_call.name == "run_tests"
        and last_tool_call.success
        and last_test.get("success") is True
    ):
        final = dict(last_test)
        final["reused_from_last_tool_call"] = True
    else:
        # Run through _dispatch so any policy (confirm, deny) applies here too.
        cmd = _final_verification_command(force_docker)
        output, _ = _dispatch(
            "run_tests", {"command": cmd}, workspace,
            policy=policy, force_docker=force_docker, confirm=confirm,
        )
        final = json.loads(output)
        final["reused_from_last_tool_call"] = False
    memory.set_final_test_result(final)

    out = memory.to_dict()
    out["temperature"] = temperature
    if seed is not None:
        out["seed"] = seed
    out["config"] = {
        "use_planner": use_planner,
        "use_resolver": use_resolver,
        "expose_inspect_error": expose_inspect_error,
        "exposed_tools": active_tool_names,
        "temperature": temperature,
        "model": model,
        "planner_model": effective_planner_model if use_planner else None,
    }
    return out
