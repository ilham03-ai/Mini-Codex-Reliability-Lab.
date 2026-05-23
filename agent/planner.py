import anthropic


def create_plan(client: anthropic.Anthropic, issue: str, workspace: str) -> str:
    """Generate a step-by-step debugging plan for the given issue."""
    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        thinking={"type": "adaptive"},
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
