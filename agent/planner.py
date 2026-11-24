import anthropic


def create_plan(
    client: anthropic.Anthropic,
    issue: str,
    workspace: str,
    model: str = "claude-sonnet-4-6",
) -> str:
    """Generate a step-by-step debugging plan for the given issue.

    `model` defaults to the main loop's model so --no-planner vs full compares
    {M, plan by M} vs {M, no plan} rather than {M, plan by a stronger model} vs
    {M, no plan}. A deliberate mismatch is still possible by passing a
    different model name explicitly — but it must be stated in the run config
    and reflected in the report, not hidden in a module default.
    """
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": (
                f"You are a software debugging expert. Given this bug report, create a concise "
                f"step-by-step plan to locate and fix the bug. Be specific about what files to "
                f"read, what to search for, and how to verify the fix.\n\n"
                f"Workspace root:\n{workspace}\n\n"
                f"Bug report:\n{issue}\n\n"
                f"Output a numbered list of 4-6 concrete steps."
            ),
        }],
    )
    text_blocks = [b for b in response.content if b.type == "text"]
    return text_blocks[0].text if text_blocks else "Explore the codebase, find the bug, fix it, run tests."
