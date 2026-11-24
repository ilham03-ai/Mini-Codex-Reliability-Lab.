"""A deliberately unfancy baseline for the reliability lab.

The point is to have a non-LLM reference the agent's success rate can be
measured against. If the agent solves 10/10 and this baseline also solves
7/10, the agent's contribution is 3 tasks, not 10. That is the conversation
the eval exists to have.

Strategy: locate the function named in the issue, generate a small set of
text-level mutations to its definition (flip comparisons, flip return sign,
swap min/max, strip ±1 offsets, lowercase both sides of a comparison), apply
each one, run pytest, keep the first mutation that turns the suite green.

What this will catch: single-line comparison bugs, single-line off-by-one,
casing bugs, min-vs-max swaps. What it will NOT catch: missing return in a
recursive branch, wrong dictionary key, regex that needs new character
classes, divide-by-N-vs-N-1, no-op functions. The gap between what the
baseline catches and what the agent catches is the agent's real signal.
"""
from __future__ import annotations

import ast
import re
import shutil
import tempfile
import time
from pathlib import Path

from agent.path_resolver import _workspace_files
from agent.tools import run_tests


# Match any lowercase identifier of length >=4; the baseline then narrows to
# those that actually appear as `def X` in the workspace. Catches names like
# `fibonacci` and `average` that the path_resolver's stricter snake_case
# requirement ignores.
_WORD_RE = re.compile(r"\b[a-z][a-z0-9_]{3,}\b")


MAX_MUTATIONS = 60


def _function_body_span(source: str, func_name: str) -> tuple[int, int] | None:
    """Return (start_line_idx, end_line_idx) for a function named `func_name`.

    Uses the standard library AST so docstrings, decorators, nested defs,
    classes and multi-line signatures are handled correctly. The span covers
    every physical line of the def, from the first decorator (or the `def`
    line if there are none) through the last line of the body. End is
    exclusive, matching Python slicing.

    Returns None when:
    - the source can't be parsed (SyntaxError);
    - no FunctionDef / AsyncFunctionDef with that name is found at the top
      level or inside a top-level class.

    Nested (closure) defs aren't returned to avoid mutating helpers inside
    the target. If the target is a method on a class, the class body is
    searched.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    def _find(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == func_name:
                return child
            if isinstance(child, ast.ClassDef):
                hit = _find(child)
                if hit is not None:
                    return hit
        return None

    match = _find(tree)
    if match is None:
        return None

    # Include any decorators: `@foo` sits a line or more above `def`.
    if match.decorator_list:
        start_line = min(d.lineno for d in match.decorator_list)
    else:
        start_line = match.lineno
    end_line = match.end_lineno  # inclusive in AST
    # ast is 1-indexed; our callers slice splitlines() which is 0-indexed.
    return start_line - 1, end_line


def _mutations_for_body(body: str) -> list[tuple[str, str]]:
    """Return a list of (label, new_body) candidate mutations.

    Determinism: the baseline tries mutations in the order they appear here
    and keeps the first one that turns the suite green. Reorder this list
    and some tasks change outcome. The ordering is intentional and stable:
    cheap/structural flips first (comparison direction, min/max), then
    offset strips, then refactors (sorted → reversed), finally the
    semantically-loaded ones (case-insensitive compare, N→N-1). If you add
    a new mutation, put it in the group whose semantics match and keep
    cheaper candidates ahead.
    """
    out = []
    seen_bodies: set[str] = set()

    def _sub(label, pattern, repl, count=1):
        new = re.sub(pattern, repl, body, count=count)
        if new == body or new in seen_bodies:
            return
        seen_bodies.add(new)
        out.append((label, new))

    # Comparison flips — one at a time so we don't double-flip. The count=i
    # loop is a no-op when the body has only one match (common case), and the
    # dedupe above collapses repeats cheaply; keep the loop so that bodies
    # with multiple comparisons generate the useful N-of-M variants.
    for i in range(1, 4):
        _sub(f"lt→gt #{i}", r"<(?!=)", ">", count=i)
        _sub(f"gt→lt #{i}", r"(?<!<)>(?!=)", "<", count=i)
        _sub(f"le→ge #{i}", r"<=", ">=", count=i)
        _sub(f"ge→le #{i}", r">=", "<=", count=i)

    # min/max swaps.
    _sub("min→max", r"\bmin\(", "max(")
    _sub("max→min", r"\bmax\(", "min(")

    # ±1 strip on `return <expr> - 1` / `+ 1`.
    _sub("strip -1 on return", r"return\s+([^\n]+?)\s*-\s*1\b", r"return \1")
    _sub("strip +1 on return", r"return\s+([^\n]+?)\s*\+\s*1\b", r"return \1")
    _sub("return mid literal", r"return\s+mid\s*[-+]\s*1\b", "return mid")

    # Common no-op fixes.
    _sub("add reversed()", r"return\s+sorted\(([^)]+)\)", r"return sorted(\1, reverse=True)")
    _sub("add reverse=True", r"sorted\(([^)]*?)\)", r"sorted(\1, reverse=True)")

    # Lowercase both sides of a comparison with a literal string.
    _sub("case-insensitive compare lhs", r"(\w+)\s*==\s*(\w+)", r"\1.lower() == \2.lower()")

    # Divide by N → divide by (N-1): useful for sample_variance.
    _sub("/ len → / (len - 1)", r"/\s*len\(([^)]+)\)", r"/ (len(\1) - 1)")

    # ValueError on empty list where a zero-division appears.
    # (hard to text-mutate safely; skip)

    # str.split()[::-1] to reverse words.
    _sub("reversed words", r"'\s*'\.join\(([^)]+)\.split\(\)\)",
         r"' '.join(\1.split()[::-1])")

    # Regex short-TLD → longer TLD (task_008).
    _sub("extend tld length", r"\\\.[a-zA-Z]\{2,3\}", r"\\.[a-zA-Z]{2,}")
    _sub("extend tld length lowercase", r"\\\.[a-z]\{2,3\}", r"\\.[a-zA-Z]{2,}")

    return out[:MAX_MUTATIONS]


def _purge_pycache(workspace: Path) -> None:
    """Delete __pycache__ and .pytest_cache directories under `workspace`.

    pytest import machinery honors stale bytecode under __pycache__. A copy of
    a workspace that was previously tested by a different Python version (or
    a different version of the source) can import an old function body and
    give results unrelated to what we just wrote on disk. Clear them.
    """
    for pattern in ("__pycache__", ".pytest_cache"):
        for p in workspace.rglob(pattern):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)


def _run_once(workspace: Path, timeout: int = 30, force_docker: bool = False) -> dict:
    """Run pytest in workspace and return the result dict."""
    _purge_pycache(workspace)
    return run_tests(
        "pytest tests/ -q", str(workspace),
        timeout=timeout, force_docker=force_docker,
    )


def run_naive_baseline(
    issue: str,
    workspace: str,
    verbose: bool = False,
    reference: dict | None = None,
    force_docker: bool = False,
) -> dict:
    """Run the naive baseline against one task's workspace.

    Returns a result dict shaped like the agent's for comparison purposes.
    Operates on a copy of the workspace.

    When `reference` is provided, the result also carries a `reference_match`
    field (from `classify_against_reference`). A mutation that turns the
    suite green but doesn't actually touch `reference.files` — or fails
    `must_contain` checks — gets `fix_match=wrong_file` / `partial`, so
    a "pass_but_wrong_fix" baseline run is visible in reports instead of
    inflating the baseline's apparent pass rate.
    """
    # classify_against_reference is invoked inside _finalize (lazy import
    # there), not in this function — remove the dead local import.
    src_ws = Path(workspace)
    tmp = Path(tempfile.mkdtemp(prefix="codex_baseline_"))
    try:
        shutil.copytree(src_ws, tmp, dirs_exist_ok=True)
        start = time.time()

        files = _workspace_files(tmp)

        # Collect every word in the issue that is actually `def`-ined or
        # `class`-ed in some workspace .py file. More permissive than the
        # strict snake_case match path_resolver uses.
        words = sorted(set(_WORD_RE.findall(issue)))
        symbol_to_file: dict[str, Path] = {}
        for f in files:
            if f.suffix != ".py" or "tests" in f.parts:
                continue
            try:
                text = (tmp / f).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for w in words:
                if w in symbol_to_file:
                    continue
                if re.search(rf"^\s*(def|class)\s+{re.escape(w)}\b", text, re.MULTILINE):
                    symbol_to_file[w] = f

        if not symbol_to_file:
            return _finalize(
                tmp, reference, issue=issue, workspace=workspace,
                elapsed=time.time() - start,
                mutation_tried=0, mutation_applied=None, passed=False,
                reason="no candidate: no word from the issue is `def`-ined in the workspace",
            )

        # Pick the symbol whose containing file has the shortest path (prefer
        # top-level source over nested helpers).
        func_name, target_file = sorted(
            symbol_to_file.items(), key=lambda kv: (len(str(kv[1])), str(kv[1]))
        )[0]
        target_path = tmp / target_file
        src = target_path.read_text()

        span = _function_body_span(src, func_name)
        if span is None:
            return _finalize(
                tmp, reference, issue=issue, workspace=workspace,
                elapsed=time.time() - start,
                mutation_tried=0, mutation_applied=None, passed=False,
                reason=f"{func_name!r} span not found",
                target_file=str(target_file), target_func=func_name,
            )
        a, b = span
        head = "".join(src.splitlines(keepends=True)[:a])
        body = "".join(src.splitlines(keepends=True)[a:b])
        tail = "".join(src.splitlines(keepends=True)[b:])

        mutations = _mutations_for_body(body)
        if verbose:
            print(f"  {target_file}::{func_name}: {len(mutations)} mutations to try")

        tried = 0
        for label, new_body in mutations:
            tried += 1
            target_path.write_text(head + new_body + tail)
            result = _run_once(tmp, force_docker=force_docker)
            if result.get("success"):
                return _finalize(
                    tmp, reference, issue=issue, workspace=workspace,
                    elapsed=time.time() - start,
                    mutation_tried=tried, mutation_applied=label, passed=True,
                    target_file=str(target_file), target_func=func_name,
                    patched_file=str(target_file),
                )
            # Revert before trying the next.
            target_path.write_text(head + body + tail)

        return _finalize(
            tmp, reference, issue=issue, workspace=workspace,
            elapsed=time.time() - start,
            mutation_tried=tried, mutation_applied=None, passed=False,
            target_file=str(target_file), target_func=func_name,
            reason="no mutation turned the suite green",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _finalize(tmp: Path, reference: dict | None, **kwargs) -> dict:
    """Shape the raw outcome so classify_against_reference can run on it.

    `patched_file` names the file the baseline rewrote (if any); it also
    becomes the single entry in `patches` so the reference check sees that a
    file was touched.
    """
    from evals.failure_taxonomy import classify_against_reference

    patched_file = kwargs.pop("patched_file", None)
    result = _result(**kwargs)
    # Snapshot every file the reference cares about, not just the baseline's
    # patched_file. Otherwise a reference with must_contain tokens in a file
    # the baseline didn't touch gets checked against an empty body, and the
    # token-presence signal is false-negative. With this snapshot the check
    # compares against actual on-disk content for every ref file.
    ref_files = (reference or {}).get("files") or []
    final_contents: dict[str, str] = {}
    for rf in ref_files:
        fp = tmp / rf
        try:
            final_contents[rf] = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            final_contents[rf] = ""
    if patched_file:
        result["patches"] = [{"path": patched_file, "diff": ""}]
        if patched_file not in final_contents:
            try:
                final_contents[patched_file] = (tmp / patched_file).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                final_contents[patched_file] = ""
    else:
        result["patches"] = []
    result["final_file_contents"] = final_contents
    # Agent-shaped fields the reference check expects.
    result["final_test_passed"] = bool(result.get("passed"))
    result["tests_modified_count"] = 0
    result["reference_match"] = classify_against_reference(result, reference)
    # `fix_passed` collapses "pytest green AND fix_match is correct(_with_extra)"
    # — this is what the honest baseline/agent comparison should use.
    rm = result["reference_match"].get("fix_match")
    result["fix_passed"] = bool(
        result["final_test_passed"]
        and rm in {"correct", "correct_with_extra", "no_reference"}
    )
    return result


def _result(**kwargs):
    base = {
        "agent": "naive_baseline_v1",
        "issue": "",
        "workspace": "",
        "elapsed_s": 0.0,
        "mutation_tried": 0,
        "mutation_applied": None,
        "passed": False,
        "reason": None,
        "target_file": None,
        "target_func": None,
    }
    if "elapsed" in kwargs:
        base["elapsed_s"] = round(kwargs.pop("elapsed"), 2)
    base.update(kwargs)
    return base
