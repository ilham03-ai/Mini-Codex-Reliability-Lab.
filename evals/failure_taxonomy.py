from enum import Enum


class FailureCategory(str, Enum):
    WRONG_FILE = "wrong_file_selected"
    BAD_PATCH = "bad_patch"
    TEST_HALLUCINATION = "test_hallucination"
    INFINITE_LOOP = "infinite_loop"
    SYNTAX_ERROR = "syntax_error_in_patch"
    MISSED_EDGE_CASE = "missed_edge_case"
    OVER_EDITING = "over_editing"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    TIMEOUT = "timeout"
    NO_PATCH = "no_patch_applied"
    UNKNOWN = "unknown"


CATEGORY_DESCRIPTIONS = {
    FailureCategory.WRONG_FILE: "Agent edited the wrong file or function",
    FailureCategory.BAD_PATCH: "Patch was syntactically valid but logically wrong",
    FailureCategory.TEST_HALLUCINATION: "Agent claimed tests passed when they did not",
    FailureCategory.INFINITE_LOOP: "Agent looped without making progress",
    FailureCategory.SYNTAX_ERROR: "Patch introduced a SyntaxError",
    FailureCategory.MISSED_EDGE_CASE: "Fix handled the main case but missed edge cases",
    FailureCategory.OVER_EDITING: "Agent changed correct code in addition to the bug",
    FailureCategory.INSUFFICIENT_CONTEXT: "Agent gave up without reading enough context",
    FailureCategory.TIMEOUT: "Agent hit the iteration limit without solving the issue",
    FailureCategory.NO_PATCH: "Agent produced no patch at all",
    FailureCategory.UNKNOWN: "Failure cause could not be classified",
}


def classify_failure(result: dict) -> FailureCategory:
    """Heuristically classify a failed agent run."""
    if result.get("final_test_passed"):
        return None  # not a failure

    patches = result.get("patches_applied", 0)
    test_results = result.get("test_results", [])
    tool_calls = result.get("total_tool_calls", 0)
    iterations = result.get("iterations", 0)

    if patches == 0:
        return FailureCategory.NO_PATCH

    if iterations >= 14:
        return FailureCategory.TIMEOUT

    # Check for syntax errors in test output
    for tr in test_results:
        stdout = tr.get("stdout", "") + tr.get("stderr", "")
        if "SyntaxError" in stdout:
            return FailureCategory.SYNTAX_ERROR

    # Check if agent hallucinated test success
    for tr in test_results[:-1]:  # all but the final check
        if tr.get("success") and not result.get("final_test_passed"):
            return FailureCategory.TEST_HALLUCINATION

    # Low tool calls suggest insufficient exploration
    if tool_calls < 3:
        return FailureCategory.INSUFFICIENT_CONTEXT

    # Many iterations without success suggests looping
    if iterations >= 8 and patches >= 3:
        return FailureCategory.INFINITE_LOOP

    # Default
    return FailureCategory.BAD_PATCH


def build_failure_report(results: list[dict]) -> dict:
    """Aggregate failure categories across a list of agent results."""
    failures = {}
    for r in results:
        if not r.get("final_test_passed"):
            cat = classify_failure(r)
            key = cat.value if cat else "unknown"
            failures[key] = failures.get(key, 0) + 1

    total_failed = len([r for r in results if not r.get("final_test_passed")])
    return {
        "total_failed": total_failed,
        "by_category": failures,
        "category_descriptions": {
            k: CATEGORY_DESCRIPTIONS.get(FailureCategory(k), k)
            for k in failures
        },
    }
