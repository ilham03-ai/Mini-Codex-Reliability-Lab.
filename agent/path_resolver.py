"""Resolve file paths mentioned in a bug-report against an actual workspace.

Bug reports often cite file paths the reporter half-remembered. The agent then
burns iterations on `read_file('src/greetings.py')` when the real file is
`src/formatter.py`. This module scans the issue for file-shaped tokens, checks
them against disk, and when they don't exist tries to find the real file by
basename or fuzzy match. The result feeds back into the agent's system prompt
so the agent sees the corrections before the first tool call.

Scope note: only file references are corrected — not function names, symbols or
line numbers. Those are better signaled by the actual tests and source.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

# File-like tokens in prose. Requires an extension so plain words don't match.
_PATH_RE = re.compile(
    r"\b(?:[\w.-]+/)*[\w.-]+\.(?:py|yaml|yml|json|toml|cfg|ini|txt|md|lock)\b"
)

# snake_case identifiers of length >= 4 — long enough that "the_bug" is unlikely,
# short enough to catch "top_k". Mixed-case or camelCase isn't the convention in
# these tasks. Must contain at least one underscore to distinguish from prose
# words like "fibonacci".
_SYMBOL_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

# Python `def` or `class` at line start (with optional leading whitespace).
_DEF_RE = re.compile(r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)

_ALLOWED_EXTS = {".py", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".txt", ".md", ".lock"}

# Directories we never present as candidate resolutions.
_SKIP_PARTS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"}


def _workspace_files(workspace: Path) -> list[Path]:
    """All candidate files under workspace, as paths relative to it."""
    out: list[Path] = []
    for fp in workspace.rglob("*"):
        if not fp.is_file():
            continue
        if any(p in _SKIP_PARTS for p in fp.parts):
            continue
        if fp.suffix not in _ALLOWED_EXTS:
            continue
        try:
            rel = fp.resolve().relative_to(workspace.resolve())
        except ValueError:
            continue
        out.append(rel)
    return out


def _find_by_basename(cited: str, files: list[Path]) -> list[str]:
    name = Path(cited).name.lower()
    hits = [str(f) for f in files if f.name.lower() == name]
    return sorted(set(hits))


def _find_by_fuzzy(cited: str, files: list[Path], cutoff: float = 0.6) -> list[str]:
    stem = Path(cited).stem.lower()
    scored = []
    for f in files:
        ratio = difflib.SequenceMatcher(None, stem, f.stem.lower()).ratio()
        if ratio >= cutoff:
            scored.append((ratio, str(f)))
    scored.sort(reverse=True)
    return [p for _, p in scored[:3]]


def _find_by_symbol(issue: str, workspace: Path, files: list[Path]) -> list[str]:
    """Find workspace files that define a snake_case symbol mentioned in the issue.

    Scans the issue for snake_case identifiers (e.g. `format_greeting`,
    `binary_search`), then checks which Python files in the workspace declare
    them via `def` or `class`. Returns the matching files, most-mentions first.
    """
    symbols = set(_SYMBOL_RE.findall(issue))
    if not symbols:
        return []
    hits: dict[str, int] = {}
    for f in files:
        if f.suffix != ".py":
            continue
        try:
            text = (workspace / f).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        defined = set(_DEF_RE.findall(text))
        overlap = symbols & defined
        if overlap:
            hits[str(f)] = len(overlap)
    return [p for p, _ in sorted(hits.items(), key=lambda kv: -kv[1])]


def _twin_test_candidates(src_src: str, src_dst: str, files_strs: set[str]) -> list[str]:
    """Given a src→dst mapping (`src/greetings.py` → `src/formatter.py`),
    return the test-file counterpart iff the obvious `tests/test_<stem>.py`
    path exists on disk. Empty list otherwise.

    The pattern is intentionally narrow: `src/<stem>.py` → `tests/test_<stem>.py`.
    We don't guess about nested test dirs or alternative naming schemes.
    """
    src_p = Path(src_src)
    dst_p = Path(src_dst)
    # Only map when the source parent is `src/` and the destination too.
    if src_p.parent.name != "src" or dst_p.parent.name != "src":
        return []
    cand = f"tests/test_{dst_p.stem}.py"
    return [cand] if cand in files_strs else []


def empty_resolution() -> dict:
    """Return a resolution dict with the same shape as resolve_issue_paths
    but every field empty. Used by the --no-resolver ablation so downstream
    consumers never KeyError on `unresolved_cited` or `unresolved_inferred`.
    Keep this and `resolve_issue_paths` in lockstep on keys.
    """
    return {
        "cited": [],
        "exists": [],
        "unresolved": {},
        "unresolved_cited": {},
        "unresolved_inferred": {},
        "symbol_hits": [],
    }


def resolve_issue_paths(issue: str, workspace: str) -> dict:
    """Scan `issue` for file-like tokens and map each to a resolution.

    Return shape:
      {
        "cited": ["src/greetings.py", "tests/test_greetings.py"],
        "exists": ["tests/test_greetings.py"],   # cited AND present on disk
        "unresolved": {"src/greetings.py": ["src/formatter.py"]},  # best guesses
        "symbol_hits": [...],
      }
    `unresolved` maps each cited-but-missing path to up to 3 candidates.
    When a `src/X.py` resolves to `src/Y.py` and `tests/test_Y.py` is on disk,
    `tests/test_X.py` (whether cited or not) also gets mapped — the agent would
    otherwise waste one call reading the test under the wrong basename.
    """
    ws = Path(workspace)
    if not ws.exists():
        return empty_resolution()

    cited_raw = _PATH_RE.findall(issue)
    # Deduplicate while preserving order.
    seen, cited = set(), []
    for c in cited_raw:
        if c not in seen:
            seen.add(c)
            cited.append(c)

    files = _workspace_files(ws)
    file_strs = {str(f) for f in files}

    exists = [c for c in cited if c in file_strs]
    missing = [c for c in cited if c not in file_strs]

    # Symbol-based hint is computed once per issue, not per cited path.
    symbol_hits = _find_by_symbol(issue, ws, files)

    unresolved: dict[str, list[str]] = {}
    for m in missing:
        hits = _find_by_basename(m, files)
        if not hits:
            hits = _find_by_fuzzy(m, files)
        if not hits:
            hits = symbol_hits
        if hits:
            unresolved[m] = hits

    # Propagate src→src mappings to their test counterparts. We add a
    # tests/test_<cited_stem>.py entry only if the cited version is NOT
    # already on disk (if it is, the agent will find it fine).
    extras: dict[str, list[str]] = {}
    for src_cited, candidates in unresolved.items():
        if not candidates:
            continue
        twin_cited = f"tests/test_{Path(src_cited).stem}.py"
        if twin_cited in file_strs or twin_cited in unresolved:
            continue
        twin_hits = _twin_test_candidates(src_cited, candidates[0], file_strs)
        if twin_hits:
            extras[twin_cited] = twin_hits
    # `extras` are synthetic — the issue never mentioned these paths.
    # Keep them separate from `unresolved` (which lists paths the issue
    # actually cited) so the hint renderer can describe them accurately.
    unresolved_cited = dict(unresolved)
    unresolved.update(extras)

    return {
        "cited": cited,
        "exists": exists,
        "unresolved": unresolved,
        "unresolved_cited": unresolved_cited,
        "unresolved_inferred": extras,
        "symbol_hits": symbol_hits,
    }


def format_resolution_hint(resolution: dict) -> str:
    """Render the resolution map as a short system-prompt insert.

    Returns an empty string when there is nothing to say (no citations or
    everything cited already exists).

    Cited-but-missing paths and inferred paths (e.g. test twins the agent
    is likely to try) are split into two groups so the hint doesn't
    misattribute inferred paths to the bug report.
    """
    unresolved = resolution.get("unresolved", {})
    if not unresolved:
        return ""
    # Back-compat: older calls may have passed a dict without the split
    # keys. Fall back to treating everything as "cited".
    cited_group = resolution.get("unresolved_cited")
    inferred_group = resolution.get("unresolved_inferred") or {}
    if cited_group is None:
        cited_group = unresolved
        inferred_group = {}

    def _render_entry(cited: str, hits: list) -> str:
        if len(hits) == 1:
            return f"  - {cited!r} → likely {hits[0]!r}"
        as_str = ", ".join(repr(h) for h in hits)
        return f"  - {cited!r} → candidates: {as_str}"

    lines: list[str] = []
    if cited_group:
        lines.append("Note: the bug report mentions paths that do not exist in this workspace.")
        for cited, hits in cited_group.items():
            lines.append(_render_entry(cited, hits))
    if inferred_group:
        lines.append(
            "Related paths the agent is likely to try (inferred from the resolution above, "
            "not mentioned in the bug report):"
        )
        for cited, hits in inferred_group.items():
            lines.append(_render_entry(cited, hits))
    lines.append("Use these paths directly; do not try the originals first.")
    return "\n".join(lines)
