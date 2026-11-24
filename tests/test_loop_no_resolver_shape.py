"""Shape symmetry between resolve_issue_paths and empty_resolution.

The --no-resolver ablation builds a resolution dict by calling
empty_resolution(). The shape must match resolve_issue_paths so any
downstream consumer that reads `path_resolution["unresolved_cited"]` or
`path_resolution["unresolved_inferred"]` doesn't KeyError in the ablation
branch.

This tests the CONTRACT (same keys), not the source text — if someone
refactors the ablation branch to call another helper, the test still passes
so long as the final shape agrees.
"""
from pathlib import Path

from agent.path_resolver import (
    resolve_issue_paths,
    empty_resolution,
)


def test_empty_resolution_has_same_keys_as_real_resolution(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("def f(): pass\n")
    real = resolve_issue_paths("fix something in src/foo.py", str(tmp_path))
    empty = empty_resolution()
    assert set(real.keys()) == set(empty.keys()), (
        f"shape drift: real has {set(real.keys())}, empty has {set(empty.keys())}"
    )


def test_empty_resolution_fields_are_their_empty_types():
    """Every key must point at an EMPTY container of the right Python type.
    A field defaulting to None would surprise consumers that iterate it."""
    e = empty_resolution()
    for list_key in ("cited", "exists", "symbol_hits"):
        assert e[list_key] == [] and isinstance(e[list_key], list), list_key
    for dict_key in ("unresolved", "unresolved_cited", "unresolved_inferred"):
        assert e[dict_key] == {} and isinstance(e[dict_key], dict), dict_key


def test_resolve_on_missing_workspace_returns_empty_resolution(tmp_path):
    """The 'workspace does not exist' branch of resolve_issue_paths must
    return the same shape as empty_resolution, not a stripped-down dict
    (the earlier bug: it returned 4 keys instead of 6)."""
    nonexistent = tmp_path / "does_not_exist"
    real = resolve_issue_paths("foo", str(nonexistent))
    assert set(real.keys()) == set(empty_resolution().keys())
