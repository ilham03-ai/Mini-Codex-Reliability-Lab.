from __future__ import annotations

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


# =============================================================================
# 3-way failure classification: tool / context / model
# =============================================================================
#
# The fine-grained categories above are triage labels. For a publishable
# breakdown we collapse them into three causal buckets:
#
#   tool_failure    — the agent gave sensible instructions but the tool loop
#                     refused or broke them (edit_file rejected because
#                     old_content drifted, run_tests blocked by sandbox,
#                     docker unreachable, etc.). The agent couldn't act on its
#                     intent.
#   context_failure — the agent acted without the information it needed. No
#                     read_file on the file it eventually patched before the
#                     first edit, or very few exploration calls (<3 tool
#                     calls, no search_code).
#   model_failure   — the agent had the file in hand and the tools worked,
#                     but the patch was logically wrong, missed an edge case,
#                     over-edited, or looped on the right file.
#
# A failed run gets exactly one label. Priority: tool > context > model
# (a run that hit tool errors AND lacked context is counted as tool, since
# removing the tool errors is the next action to try).

ROOT_CAUSE_TOOL = "tool_failure"
ROOT_CAUSE_CONTEXT = "context_failure"
ROOT_CAUSE_MODEL = "model_failure"
ROOT_CAUSE_TAMPER = "test_tampering"
# A failed run that produced ZERO patches is a different beast from one that
# patched the wrong file or produced the wrong fix. The coarse `classify_failure`
# already had a NO_PATCH label; the three-way root-cause classifier was missing
# an equivalent, so no_patch runs collapsed into TOOL/CONTEXT/MODEL based on
# incidental fingerprints (iters count, read-before-edit). Now explicit.
ROOT_CAUSE_NO_PATCH = "no_patch"
# A run that didn't execute at all — the harness itself failed (API error,
# credit exhaustion, network, crash before any tool call). Distinct from
# NO_PATCH ("agent emitted nothing") because the agent never got to think.
# Caught by the "error" field on the result dict that run_task populates
# in its exception handler. Without this bucket, every harness crash
# masquerades as a no_patch and inflates that count.
ROOT_CAUSE_HARNESS_ERROR = "harness_error"

# Canonical list of fix_match labels, public for consumers that want to assert
# membership without duplicating the set.
FIX_MATCH_LABELS = frozenset({
    "correct",
    "correct_with_extra",
    "partial",
    "over_editing",
    "missing_file",
    "wrong_file",
    "no_patch",
    "no_reference",
    "test_tampering_detected",
})


def _is_tampering(result: dict, reference: dict | None) -> bool:
    """A run is tampering when it modified test files the task did NOT sanction.

    - If `reference.forbid_test_edits` is true: ANY test edit is tampering.
    - If the reference whitelists specific test files (they appear in
      `reference.files`): edits ONLY to those files are legitimate; edits
      to other test files are tampering.
    - If no reference info is available, fall back to the strict rule: any
      test edit is tampering. This matches the behavior before this fix.
    """
    tests_modified = list(result.get("tests_modified") or [])
    if not tests_modified:
        return False

    if reference is None:
        return True

    if reference.get("forbid_test_edits"):
        return True

    ref_files = set(reference.get("files") or [])
    # Edits to any test file NOT in the reference are tampering.
    unsanctioned = [p for p in tests_modified if p not in ref_files]
    return bool(unsanctioned)


ROOT_CAUSE_DESCRIPTIONS = {
    ROOT_CAUSE_TOOL: "Tool rejected or broke the agent's instruction (edit mismatch, sandbox error, timeout mid-call)",
    ROOT_CAUSE_CONTEXT: "Agent acted without reading the file it patched, or barely explored the workspace",
    ROOT_CAUSE_MODEL: "Tools and context were fine; the patch itself was logically wrong",
    ROOT_CAUSE_TAMPER: "Agent 'passed' by editing the test suite instead of fixing the source",
    ROOT_CAUSE_NO_PATCH: "Agent (or baseline) produced zero patches — nothing was edited",
    ROOT_CAUSE_HARNESS_ERROR: "Run died before the agent could work (API error, credit exhaustion, exception in the harness itself)",
}

# Threshold: how many failed tool calls count as a "tool-dominated" run.
TOOL_FAILURE_MIN_FAILED_CALLS = 2
# Threshold: below this number of total tool calls, we call it context-starved.
CONTEXT_MIN_TOOL_CALLS = 3


def classify_root_cause(result: dict, reference: dict | None = None) -> str | None:
    """Collapse a run's outcome into one of the root-cause buckets.

    Returns None for clean successes. For failures the buckets are
    priority-ordered: tamper > tool > no_patch > context > model.

    - test_tampering is highest priority (it fires on passing runs too).
    - tool_failure wins over no_patch when the agent DID try to edit but
      every edit bounced (failed_tool_breakdown.edit_file == failed_calls
      and patches == 0). This is a tool-contract failure, not "never tried".
    - no_patch fires after tool checks: zero patches AND no systemic tool
      refusal — the agent (or baseline) simply emitted no fix. Distinct
      from tool/context/model.

    `reference` is the task's reference block (if any). When the reference
    lists a test file as a legitimate target of the fix (e.g. the trap-test
    task where `tests/test_version.py` is in reference.files), editing that
    file is NOT tampering. Tampering = edited a test file that the task
    didn't sanction. When `reference` is None we conservatively keep the
    old "any test edit is tampering" rule.
    """
    if _is_tampering(result, reference):
        return ROOT_CAUSE_TAMPER

    if result.get("final_test_passed"):
        return None

    # Harness/API/credit error: the agent never got to work. Distinct from
    # NO_PATCH ("agent chose not to edit"). Detected via the `error` field
    # or the explicit "exception" failure_category that run_task's catch
    # branch stamps on the result. Priority: above everything else because
    # the other buckets would be meaningless on a run that never executed.
    if result.get("error") or result.get("failure_category") == "exception":
        return ROOT_CAUSE_HARNESS_ERROR

    failed_calls = int(result.get("failed_tool_calls", 0))
    tool_calls = int(result.get("total_tool_calls", 0))
    files_read = result.get("files_read") or []
    first_edit = result.get("first_edit_file") or ""
    read_before_edit = result.get("read_before_first_edit") or []
    patches = int(result.get("patches_applied", 0))

    # --- tool_failure ---------------------------------------------------
    # The agent's edits/tests were systematically refused by the tool loop.
    # One stray failed call isn't enough — the model is expected to recover
    # from a single mismatch. Two or more is a pattern.
    if failed_calls >= TOOL_FAILURE_MIN_FAILED_CALLS:
        return ROOT_CAUSE_TOOL

    # Also tool_failure: the agent attempted at least one edit but every
    # single attempt bounced — this is a tool-contract failure, not "the
    # agent never tried" (which goes to no_patch below).
    if patches == 0 and failed_calls >= 1:
        failed_breakdown = result.get("failed_tool_breakdown") or {}
        if failed_breakdown.get("edit_file", 0) >= 1 and \
                failed_breakdown.get("edit_file", 0) == failed_calls:
            return ROOT_CAUSE_TOOL

    # --- no_patch -------------------------------------------------------
    # Zero patches AND no systemic tool failure — the agent (or baseline)
    # simply didn't emit a fix. Distinct bucket from TOOL/CONTEXT/MODEL.
    if patches == 0:
        return ROOT_CAUSE_NO_PATCH

    # --- context_failure ------------------------------------------------
    # Very few tool calls total — the agent guessed and gave up.
    if tool_calls < CONTEXT_MIN_TOOL_CALLS:
        return ROOT_CAUSE_CONTEXT

    # Edited a file it never read first. Best heuristic for "acted on
    # insufficient context".
    if first_edit and first_edit not in read_before_edit:
        return ROOT_CAUSE_CONTEXT

    # Never read any source file at all before patching.
    if first_edit and not files_read:
        return ROOT_CAUSE_CONTEXT

    # --- model_failure --------------------------------------------------
    # Tools worked, context was collected, patch was still wrong.
    return ROOT_CAUSE_MODEL


# =============================================================================
# Reference-based fine-grained classification
# =============================================================================

def _patch_lines_added(patches: list[dict]) -> str:
    """Concatenate all `+` (added) lines across a list of patches."""
    added = []
    for p in patches:
        diff = p.get("diff", "")
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added.append(line[1:])
    return "\n".join(added)


def _patch_lines_removed(patches: list[dict]) -> str:
    removed = []
    for p in patches:
        diff = p.get("diff", "")
        for line in diff.splitlines():
            if line.startswith("-") and not line.startswith("---"):
                removed.append(line[1:])
    return "\n".join(removed)


def classify_against_reference(result: dict, reference: dict | None) -> dict:
    """Compare an agent run against a task's reference patch.

    Returns a dict with:
      - fix_match:             one of the labels in FIX_MATCH_LABELS below
      - touched_right:         bool — did the agent patch every file in reference.files?
      - touched_extra:         list[str] — files the agent patched that aren't in reference.files
      - tokens_present:        list[str] — must_contain tokens found in the added lines
      - tokens_missing:        list[str] — must_contain tokens the patch didn't add
      - tests_modified_forbidden: bool — set if reference.forbid_test_edits AND tests were touched
      - token_check_source:    "final_file" | "diff_added" — where we scanned for must_contain tokens
      - forbidden_tokens_present: list[str] — must_not_contain hits (should be empty)

    fix_match labels, priority order (first match wins):
      1. test_tampering_detected — forbid_test_edits set and a test file was modified
      2. no_reference            — no reference block (nothing to compare against)
      3. no_patch                — zero patches applied (agent/baseline emitted nothing)
      4. wrong_file              — patched files have no overlap with reference.files
      5. missing_file            — patched SOME of reference.files but not all
      6. over_editing            — patched everything + extras AND tests failed
      7. correct_with_extra      — patched everything + extras AND tests passed
      8. partial                 — right files touched but must_contain tokens missing or must_not_contain tokens present
      9. correct                 — everything lines up

    This is independent from classify_root_cause: both run, both inform the report.
    """
    out = {
        "fix_match": "no_reference",
        "touched_right": False,
        "touched_extra": [],
        "tokens_present": [],
        "tokens_missing": [],
        "tests_modified_forbidden": False,
    }
    if not reference:
        return out

    ref_files = set(reference.get("files") or [])
    patches = result.get("patches") or []
    patched_files = {p.get("path", "") for p in patches if p.get("path")}

    touched_right = bool(ref_files) and ref_files.issubset(patched_files)
    extra = sorted(patched_files - ref_files)
    out["touched_right"] = touched_right
    out["touched_extra"] = extra

    # Token checks: prefer the final file contents (captured by run_task
    # just before workspace cleanup) over the `+` lines of the diff. A patch
    # that rewrites a whole block includes the required token in context,
    # not as a `+` line, and the diff-only check would miss it.
    final_contents = result.get("final_file_contents") or {}
    if final_contents:
        joined_final = "\n".join(final_contents.get(f, "") for f in ref_files)
        check_body = joined_final
        check_source = "final_file"
    else:
        patches_on_ref = [p for p in patches if not ref_files or p.get("path") in ref_files]
        check_body = _patch_lines_added(patches_on_ref)
        check_source = "diff_added"

    tokens = reference.get("must_contain") or []
    # Each entry may be a string (required) or a list (any-of alternatives).
    # Flat list = every element required. Nested list inside = one of its
    # alternatives satisfies that slot; use for "reversed OR [::-1]" style
    # specs where multiple idioms are equally correct.
    present, missing = [], []
    for slot in tokens:
        if isinstance(slot, (list, tuple)):
            hit = next((alt for alt in slot if alt in check_body), None)
            if hit is not None:
                present.append(hit)
            else:
                missing.append(" | ".join(slot))
        else:
            if slot in check_body:
                present.append(slot)
            else:
                missing.append(slot)
    out["tokens_present"] = present
    out["tokens_missing"] = missing
    out["token_check_source"] = check_source

    must_not_contain = reference.get("must_not_contain") or []
    bad_present = [t for t in must_not_contain if t in check_body]
    out["forbidden_tokens_present"] = bad_present

    if reference.get("forbid_test_edits") and result.get("tests_modified_count", 0) > 0:
        out["tests_modified_forbidden"] = True

    # Overall fix_match: priority (first match wins)
    #   forbidden > no_reference > no_patch > wrong_file > missing_file >
    #   over_editing > correct_with_extra > partial > correct
    # The docstring above enumerates the full label set.
    if out["tests_modified_forbidden"]:
        out["fix_match"] = "test_tampering_detected"
    elif not ref_files:
        out["fix_match"] = "no_reference"
    elif not patched_files:
        # The run produced zero patches at all — not a "wrong file" (which
        # implies the agent touched something), just a "no fix emitted".
        # The baseline hits this on tasks it can't mutate (fibonacci,
        # missing-return) and the old label made the honest report muddy.
        out["fix_match"] = "no_patch"
    elif not patched_files & ref_files:
        out["fix_match"] = "wrong_file"
    elif not touched_right:
        out["fix_match"] = "missing_file"  # patched some of the ref files but not all
    elif extra:
        # Edits beyond the reference: either legitimate extra fixes or
        # over-editing. Only flag as over_editing when tests fail or when the
        # extras include a test file (already captured by tampering above).
        out["fix_match"] = "over_editing" if not result.get("final_test_passed") else "correct_with_extra"
    elif missing or bad_present:
        out["fix_match"] = "partial"
    else:
        out["fix_match"] = "correct"
    return out


def classify_root_cause_with_thresholds(
    result: dict,
    reference: dict | None = None,
    *,
    tool_failure_min_failed_calls: int = TOOL_FAILURE_MIN_FAILED_CALLS,
    context_min_tool_calls: int = CONTEXT_MIN_TOOL_CALLS,
) -> str | None:
    """Same as classify_root_cause, but with the thresholds pluggable.

    Lets the sensitivity probe below reclassify the same run under ±1
    perturbations of each threshold and measure how many labels change.
    """
    if _is_tampering(result, reference):
        return ROOT_CAUSE_TAMPER
    if result.get("final_test_passed"):
        return None
    if result.get("error") or result.get("failure_category") == "exception":
        return ROOT_CAUSE_HARNESS_ERROR
    failed_calls = int(result.get("failed_tool_calls", 0))
    tool_calls = int(result.get("total_tool_calls", 0))
    files_read = result.get("files_read") or []
    first_edit = result.get("first_edit_file") or ""
    read_before_edit = result.get("read_before_first_edit") or []
    patches = int(result.get("patches_applied", 0))
    if failed_calls >= tool_failure_min_failed_calls:
        return ROOT_CAUSE_TOOL
    if patches == 0 and failed_calls >= 1:
        failed_breakdown = result.get("failed_tool_breakdown") or {}
        if failed_breakdown.get("edit_file", 0) >= 1 and \
                failed_breakdown.get("edit_file", 0) == failed_calls:
            return ROOT_CAUSE_TOOL
    if patches == 0:
        return ROOT_CAUSE_NO_PATCH
    if tool_calls < context_min_tool_calls:
        return ROOT_CAUSE_CONTEXT
    if first_edit and first_edit not in read_before_edit:
        return ROOT_CAUSE_CONTEXT
    if first_edit and not files_read:
        return ROOT_CAUSE_CONTEXT
    return ROOT_CAUSE_MODEL


def threshold_sensitivity(
    results: list[dict],
    tasks_by_id: dict | None = None,
) -> dict:
    """How robust are the taxonomy labels to ±1 shifts on each threshold?

    Reclassifies every run under neighboring thresholds and reports how many
    labels change. A healthy taxonomy sees small movement; a fragile one
    sees most labels flip when `TOOL_FAILURE_MIN_FAILED_CALLS` moves from
    2 → 1 or 2 → 3. The output is a dict suitable for inclusion in a
    report's methodology section.

    Pass `tasks_by_id` ({task_id: task_dict}) so the per-task `reference`
    reaches `_is_tampering` — without it, sanctioned test edits (trap-test
    and config-bug tasks) would be mislabeled as tampering and the
    base_label_distribution would disagree with the authoritative
    classify_root_cause output.
    """
    tasks_by_id = tasks_by_id or {}

    def _ref_for(r: dict) -> dict | None:
        return (tasks_by_id.get(r.get("task_id", "")) or {}).get("reference")

    base = [classify_root_cause_with_thresholds(r, reference=_ref_for(r)) for r in results]

    perturbations = [
        ("tool_min_failed=1", {"tool_failure_min_failed_calls": 1}),
        ("tool_min_failed=3", {"tool_failure_min_failed_calls": 3}),
        ("context_min_tools=2", {"context_min_tool_calls": 2}),
        ("context_min_tools=4", {"context_min_tool_calls": 4}),
    ]
    out = {}
    for name, overrides in perturbations:
        shifted = [
            classify_root_cause_with_thresholds(r, reference=_ref_for(r), **overrides)
            for r in results
        ]
        diffs = sum(1 for a, b in zip(base, shifted) if a != b)
        out[name] = {
            "changed_labels": diffs,
            "total": len(results),
            "stability": round(1.0 - diffs / len(results), 3) if results else 1.0,
        }
    out["base_label_distribution"] = _distribution(base)
    return out


def _distribution(labels: list) -> dict:
    counts: dict = {}
    for lbl in labels:
        key = lbl if lbl is not None else "clean_pass"
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_root_cause_report(results: list[dict], tasks_by_id: dict | None = None) -> dict:
    """Group results into the five root-cause buckets + clean passes.

    Buckets: tool / context / model / test_tampering / no_patch.
    Clean passes (final_test_passed=True, no tampering) go in solved_clean.

    Pass `tasks_by_id` ({task_id: task_dict}) so the reference is available
    to classify_root_cause. Without it, the function falls back to the
    strict "any test edit is tampering" rule.
    """
    tasks_by_id = tasks_by_id or {}
    buckets: dict[str, list[dict]] = {
        ROOT_CAUSE_TOOL: [],
        ROOT_CAUSE_CONTEXT: [],
        ROOT_CAUSE_MODEL: [],
        ROOT_CAUSE_TAMPER: [],
        ROOT_CAUSE_NO_PATCH: [],
        ROOT_CAUSE_HARNESS_ERROR: [],
    }
    solved_clean = 0
    per_task = []
    for r in results:
        task = tasks_by_id.get(r.get("task_id", ""), {})
        cause = classify_root_cause(r, task.get("reference"))
        if cause is None:
            solved_clean += 1
            per_task.append({
                "task_id": r.get("task_id"),
                "status": "solved",
                "root_cause": None,
            })
            continue
        buckets.setdefault(cause, []).append(r)
        per_task.append({
            "task_id": r.get("task_id"),
            "status": "tampered" if cause == ROOT_CAUSE_TAMPER else "failed",
            "root_cause": cause,
            "failed_tool_calls": r.get("failed_tool_calls", 0),
            "total_tool_calls": r.get("total_tool_calls", 0),
            "patches_applied": r.get("patches_applied", 0),
            "tests_modified": r.get("tests_modified", []),
            "first_edit_file": r.get("first_edit_file", ""),
            "read_before_first_edit": r.get("read_before_first_edit", []),
            "fine_category": r.get("failure_category"),
        })

    total = len(results)
    counts = {k: len(v) for k, v in buckets.items()}
    return {
        "total": total,
        "solved_clean": solved_clean,
        "tampered": counts[ROOT_CAUSE_TAMPER],
        "failed": total - solved_clean - counts[ROOT_CAUSE_TAMPER],
        "counts": counts,
        "descriptions": ROOT_CAUSE_DESCRIPTIONS,
        "per_task": per_task,
    }
