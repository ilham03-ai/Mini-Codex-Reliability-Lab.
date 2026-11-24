from pathlib import Path

from agent.path_resolver import (
    resolve_issue_paths,
    format_resolution_hint,
)


def _setup_workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "formatter.py").write_text("def format_greeting():\n    pass\n")
    (tmp_path / "src" / "utils.py").write_text("")
    (tmp_path / "tests" / "test_formatter.py").write_text("")
    return tmp_path


def test_cited_path_that_exists_is_marked_existing(tmp_path):
    _setup_workspace(tmp_path)
    issue = "fix the bug in src/formatter.py"
    r = resolve_issue_paths(issue, str(tmp_path))
    assert r["cited"] == ["src/formatter.py"]
    assert r["exists"] == ["src/formatter.py"]
    assert r["unresolved"] == {}


def test_cited_path_that_does_not_exist_falls_back_to_symbol(tmp_path):
    _setup_workspace(tmp_path)
    # The real file defines format_greeting but is named formatter.py. The
    # issue cites the wrong path but names the function.
    issue = "fix format_greeting in src/greetings.py"
    r = resolve_issue_paths(issue, str(tmp_path))
    assert "src/greetings.py" in r["unresolved"]
    assert "src/formatter.py" in r["unresolved"]["src/greetings.py"]


def test_basename_match_wins_over_fuzzy(tmp_path):
    _setup_workspace(tmp_path)
    # Rename formatter.py so same-basename logic is exercised.
    (tmp_path / "src" / "formatter.py").rename(tmp_path / "src" / "greetings.py")
    issue = "fix tests/greetings.py"
    r = resolve_issue_paths(issue, str(tmp_path))
    # tests/greetings.py doesn't exist; src/greetings.py shares the basename.
    assert r["unresolved"]["tests/greetings.py"] == ["src/greetings.py"]


def test_format_resolution_hint_empty_when_nothing_to_say(tmp_path):
    _setup_workspace(tmp_path)
    hint = format_resolution_hint({"cited": [], "exists": [], "unresolved": {}})
    assert hint == ""
    # Also empty when all cited paths exist.
    r = resolve_issue_paths("in src/formatter.py", str(tmp_path))
    assert format_resolution_hint(r) == ""


def test_format_resolution_hint_names_candidates(tmp_path):
    _setup_workspace(tmp_path)
    r = resolve_issue_paths("fix format_greeting in src/greetings.py", str(tmp_path))
    hint = format_resolution_hint(r)
    assert "src/greetings.py" in hint
    assert "src/formatter.py" in hint


def test_symbol_match_identifies_file_even_without_path(tmp_path):
    _setup_workspace(tmp_path)
    r = resolve_issue_paths("the function format_greeting is wrong", str(tmp_path))
    # No cited path, but symbol_hits should be populated as a hint anyway.
    assert r["cited"] == []
    assert "src/formatter.py" in r["symbol_hits"]


def test_prose_without_file_tokens_yields_nothing(tmp_path):
    _setup_workspace(tmp_path)
    r = resolve_issue_paths("the greeting function is wrong", str(tmp_path))
    assert r["cited"] == []
    assert r["unresolved"] == {}


def test_only_recognised_extensions_are_scanned(tmp_path):
    _setup_workspace(tmp_path)
    (tmp_path / "data.bin").write_text("x")
    r = resolve_issue_paths("the bug is in data.bin and src/formatter.py", str(tmp_path))
    # data.bin should not be picked up (not an allowed extension).
    assert r["cited"] == ["src/formatter.py"]


def test_test_twin_is_mapped_when_src_resolves(tmp_path):
    """When src/greetings.py resolves to src/formatter.py AND
    tests/test_formatter.py exists on disk, tests/test_greetings.py should
    also be resolved — otherwise the agent wastes a call on the wrong test
    basename the moment it tries to read the test file."""
    _setup_workspace(tmp_path)
    issue = "fix format_greeting in src/greetings.py"
    r = resolve_issue_paths(issue, str(tmp_path))
    assert "src/greetings.py" in r["unresolved"]
    # Twin test file mapped even though the issue didn't cite it:
    assert "tests/test_greetings.py" in r["unresolved"]
    assert r["unresolved"]["tests/test_greetings.py"] == ["tests/test_formatter.py"]


def test_test_twin_not_added_when_already_cited_and_present(tmp_path):
    _setup_workspace(tmp_path)
    # Issue cites the correct test file and a wrong src path. The twin logic
    # must not over-write the cited test entry.
    issue = "src/greetings.py and tests/test_formatter.py"
    r = resolve_issue_paths(issue, str(tmp_path))
    # Cited path for the test is on disk → goes in `exists`, not unresolved.
    assert "tests/test_formatter.py" in r["exists"]
    # No spurious twin entry gets added for an already-correct citation.
    assert "tests/test_greetings.py" not in r["exists"]


def test_hint_distinguishes_cited_from_inferred(tmp_path):
    """The hint must not say "the bug report mentions X" for X that was never
    actually mentioned. Test twins (synthetic) go under a separate preamble
    that names them as inferred."""
    _setup_workspace(tmp_path)
    r = resolve_issue_paths("fix format_greeting in src/greetings.py", str(tmp_path))
    hint = format_resolution_hint(r)
    assert "src/greetings.py" in hint
    assert "tests/test_greetings.py" in hint
    # Cited block explicitly tied to the bug report:
    assert "the bug report mentions" in hint
    # Inferred block clearly labeled as NOT from the report:
    assert "not mentioned in the bug report" in hint
    # The inferred-twin line comes AFTER the inferred preamble, not before.
    rep_idx = hint.index("the bug report mentions")
    inf_idx = hint.index("not mentioned in the bug report")
    twin_idx = hint.index("tests/test_greetings.py")
    assert rep_idx < inf_idx < twin_idx
    # Cited missing path appears BEFORE the inferred preamble.
    cited_idx = hint.index("src/greetings.py")
    assert rep_idx < cited_idx < inf_idx


def test_hint_back_compat_when_split_keys_missing():
    """Older callers may pass a resolution dict without unresolved_cited /
    unresolved_inferred keys. The hint must still render, treating every
    unresolved entry as cited."""
    old_shape = {
        "unresolved": {"src/foo.py": ["src/bar.py"]},
        # no unresolved_cited / unresolved_inferred fields
    }
    hint = format_resolution_hint(old_shape)
    assert "src/foo.py" in hint
    assert "src/bar.py" in hint
    assert "the bug report mentions" in hint
    assert "not mentioned in the bug report" not in hint
