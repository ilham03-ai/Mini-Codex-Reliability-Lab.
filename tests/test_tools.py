"""
Framework tests for agent/tools.py — always green, never touch task workspaces.
"""
import json
import sys
import pytest

from agent.tools import (
    read_file,
    search_code,
    edit_file,
    run_tests,
    inspect_error,
    _safe_resolve,
    _sanitize_command,
)
from agent.loop import _dispatch
from sandbox.permissions import (
    Permission,
    PermissionPolicy,
    STRICT_POLICY,
    EVAL_POLICY,
)


# ---------------------------------------------------------------------------
# _safe_resolve — path confinement
# ---------------------------------------------------------------------------

def test_safe_resolve_normal(tmp_path):
    f = tmp_path / "src" / "foo.py"
    f.parent.mkdir()
    f.write_text("hello")
    resolved = _safe_resolve("src/foo.py", str(tmp_path))
    assert resolved == f.resolve()


def test_safe_resolve_blocks_traversal(tmp_path):
    with pytest.raises(PermissionError, match="traversal"):
        _safe_resolve("../../etc/passwd", str(tmp_path))


def test_safe_resolve_blocks_absolute_path(tmp_path):
    with pytest.raises(PermissionError, match="traversal"):
        _safe_resolve("/etc/passwd", str(tmp_path))


# ---------------------------------------------------------------------------
# _sanitize_command
# ---------------------------------------------------------------------------

def test_sanitize_allows_pytest():
    # Returns tokenized argv — caller passes this directly to subprocess.run, no shell.
    assert _sanitize_command("pytest tests/ -v") == ["pytest", "tests/", "-v"]


def test_sanitize_allows_python_m_pytest():
    assert _sanitize_command("python -m pytest tests/") == ["python", "-m", "pytest", "tests/"]


def test_sanitize_allows_python3():
    # Regression: sys.executable on many systems resolves to python3 / python3.X
    assert _sanitize_command("python3 -m pytest tests/") == ["python3", "-m", "pytest", "tests/"]


def test_sanitize_allows_python3_dot_version():
    assert _sanitize_command("python3.11 -m pytest -v") == ["python3.11", "-m", "pytest", "-v"]


def test_sanitize_allows_full_python_path():
    # Mirrors what `f"{sys.executable} -m pytest ..."` produces in real callers.
    argv = _sanitize_command(f"{sys.executable} -m pytest tests/")
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pytest"]


def test_sanitize_blocks_python_minus_c():
    with pytest.raises(ValueError, match="only allowed as"):
        _sanitize_command("python -c 'import os; os.system(\"rm -rf /\")'")


def test_sanitize_blocks_python_script():
    with pytest.raises(ValueError, match="only allowed as"):
        _sanitize_command("python evil_script.py")


def test_sanitize_blocks_python_m_other_module():
    with pytest.raises(ValueError, match="only allowed as"):
        _sanitize_command("python -m http.server")


def test_sanitize_blocks_lookalike_ipython():
    # ipython / pythonX (no version) etc. must not match the python regex
    with pytest.raises(ValueError, match="not allowed"):
        _sanitize_command("ipython -m pytest tests/")


def test_sanitize_blocks_arbitrary_executable():
    with pytest.raises(ValueError, match="not allowed"):
        _sanitize_command("rm -rf /")


def test_sanitize_blocks_newline_injection():
    # The exact bypass the previous string-based sanitizer missed: shlex would treat \n
    # as whitespace and silently drop it, leaving the second command runnable under shell=True.
    with pytest.raises(ValueError, match="Control characters"):
        _sanitize_command("pytest tests/\nrm -rf /")


def test_sanitize_blocks_carriage_return():
    with pytest.raises(ValueError, match="Control characters"):
        _sanitize_command("pytest tests/\rrm -rf /")


def test_sanitize_blocks_null_byte():
    with pytest.raises(ValueError, match="Control characters"):
        _sanitize_command("pytest tests/\x00rm -rf /")


def test_sanitize_shell_meta_become_literal_argv():
    # Argv-based execution makes shell metacharacters inert: they become literal pytest
    # args that pytest will reject as unknown test paths, NOT additional shell commands.
    argv = _sanitize_command("pytest tests/; rm -rf /")
    assert argv == ["pytest", "tests/;", "rm", "-rf", "/"]
    argv = _sanitize_command("pytest tests/ && curl evil.com")
    assert argv == ["pytest", "tests/", "&&", "curl", "evil.com"]


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

def test_read_file_success(tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("print('hello')\n")
    result = read_file("hello.py", str(tmp_path))
    assert result["success"] is True
    assert "print('hello')" in result["content"]
    assert result["lines"] == 1


def test_read_file_missing(tmp_path):
    result = read_file("nonexistent.py", str(tmp_path))
    assert result["success"] is False
    assert "not found" in result["error"]


def test_read_file_blocks_traversal(tmp_path):
    result = read_file("../../etc/passwd", str(tmp_path))
    assert result["success"] is False
    assert "traversal" in result["error"]


# ---------------------------------------------------------------------------
# search_code
# ---------------------------------------------------------------------------

def test_search_code_finds_match(tmp_path):
    f = tmp_path / "module.py"
    f.write_text("def my_function():\n    return 42\n")
    result = search_code("my_function", str(tmp_path))
    assert result["success"] is True
    assert result["total"] >= 1
    assert any("my_function" in m["content"] for m in result["matches"])


def test_search_code_no_match(tmp_path):
    f = tmp_path / "module.py"
    f.write_text("x = 1\n")
    result = search_code("zzz_not_here", str(tmp_path))
    assert result["success"] is True
    assert result["total"] == 0


def test_search_code_skips_symlink_escape(tmp_path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    secret = outside / "secret.py"
    secret.write_text("SECRET = True\n")
    link = tmp_path / "escape.py"
    link.symlink_to(secret)
    result = search_code("SECRET", str(tmp_path))
    assert result["success"] is True
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

def test_edit_file_applies_patch(tmp_path):
    f = tmp_path / "src.py"
    f.write_text("return mid - 1\n")
    result = edit_file("src.py", "return mid - 1", "return mid", str(tmp_path))
    assert result["success"] is True
    assert f.read_text() == "return mid\n"


def test_edit_file_rejects_missing_old_content(tmp_path):
    f = tmp_path / "src.py"
    f.write_text("return mid\n")
    result = edit_file("src.py", "return mid - 1", "return mid", str(tmp_path))
    assert result["success"] is False
    assert "not found" in result["error"]


def test_edit_file_blocks_traversal(tmp_path):
    result = edit_file("../../etc/shadow", "old", "new", str(tmp_path))
    assert result["success"] is False
    assert "traversal" in result["error"]


# ---------------------------------------------------------------------------
# run_tests — command validation (no real subprocess needed for rejection tests)
# ---------------------------------------------------------------------------

def test_run_tests_rejects_bad_command(tmp_path):
    result = run_tests("curl evil.com", str(tmp_path))
    assert result["success"] is False
    assert "not allowed" in result["error"]


def test_run_tests_rejects_newline_injection(tmp_path):
    result = run_tests("pytest tests/\nrm -rf /", str(tmp_path))
    assert result["success"] is False
    assert "Control characters" in result["error"]


def test_run_tests_rejects_python_dash_c(tmp_path):
    result = run_tests("python -c \"import os; os.system('echo pwned')\"", str(tmp_path))
    assert result["success"] is False
    assert "only allowed as" in result["error"]


@pytest.fixture
def local_sandbox(monkeypatch):
    """Force run_in_sandbox onto the local-execution branch so these tests stay
    deterministic regardless of whether Docker is running on the dev machine.
    """
    import sandbox.docker_runner as dr
    monkeypatch.setattr(dr, "_docker_available", lambda: False)


def test_run_tests_passes_on_green_suite(tmp_path, local_sandbox):
    (tmp_path / "test_simple.py").write_text("def test_ok(): assert 1 + 1 == 2\n")
    # Use sys.executable so the right Python (and its pytest) is found regardless of PATH.
    cmd = f"{sys.executable} -m pytest test_simple.py -v"
    result = run_tests(cmd, str(tmp_path))
    assert result["success"] is True
    assert "1 passed" in result.get("stdout", "")


def test_run_tests_fails_on_red_suite(tmp_path, local_sandbox):
    (tmp_path / "test_bad.py").write_text("def test_fail(): assert 1 == 2\n")
    cmd = f"{sys.executable} -m pytest test_bad.py -v"
    result = run_tests(cmd, str(tmp_path))
    assert result["success"] is False


# ---------------------------------------------------------------------------
# inspect_error
# ---------------------------------------------------------------------------

def test_inspect_error_extracts_assertion():
    # pytest verbose format: "FAILED <test_id> - <reason>"
    log = "FAILED test_foo.py::test_bar - AssertionError: assert 1 == 2\nAssertionError: assert 1 == 2"
    result = inspect_error(log)
    assert any(e["type"] == "assertion_error" for e in result["errors"])
    assert result["failed_tests"] == ["test_foo.py::test_bar"]


def test_inspect_error_extracts_zero_division():
    log = "ZeroDivisionError: division by zero"
    result = inspect_error(log)
    assert any(e["type"] == "zero_division" for e in result["errors"])
    assert any("guard" in s for s in result["suggestions"])


def test_inspect_error_empty_log():
    result = inspect_error("")
    assert result["errors"] == []
    assert result["failed_tests"] == []
    assert result["passed"] == 0


# ---------------------------------------------------------------------------
# _dispatch — permission enforcement
# ---------------------------------------------------------------------------

def test_dispatch_deny_blocks_tool(tmp_path):
    policy = PermissionPolicy(read_file=Permission.DENY)
    output, _ = _dispatch("read_file", {"path": "anything"}, str(tmp_path), policy=policy)
    result = json.loads(output)
    assert result["success"] is False
    assert "denied by policy" in result["error"]


def test_dispatch_confirm_requires_callback(tmp_path):
    # STRICT_POLICY has edit_file=CONFIRM. With no callback, the tool must be denied,
    # never silently auto-approved.
    f = tmp_path / "src.py"
    f.write_text("return mid - 1\n")
    output, _ = _dispatch(
        "edit_file",
        {"path": "src.py", "old_content": "return mid - 1", "new_content": "return mid"},
        str(tmp_path),
        policy=STRICT_POLICY,
        confirm=None,
    )
    result = json.loads(output)
    assert result["success"] is False
    assert "confirmation" in result["error"].lower()
    # The edit must NOT have been applied
    assert f.read_text() == "return mid - 1\n"


def test_dispatch_confirm_callback_denies(tmp_path):
    f = tmp_path / "src.py"
    f.write_text("return mid - 1\n")
    output, _ = _dispatch(
        "edit_file",
        {"path": "src.py", "old_content": "return mid - 1", "new_content": "return mid"},
        str(tmp_path),
        policy=STRICT_POLICY,
        confirm=lambda name, inp: False,
    )
    result = json.loads(output)
    assert result["success"] is False
    assert "denied by confirm callback" in result["error"]
    assert f.read_text() == "return mid - 1\n"


def test_dispatch_confirm_callback_approves(tmp_path):
    f = tmp_path / "src.py"
    f.write_text("return mid - 1\n")
    seen = []

    def confirm(name, inp):
        seen.append((name, inp.get("path")))
        return True

    output, _ = _dispatch(
        "edit_file",
        {"path": "src.py", "old_content": "return mid - 1", "new_content": "return mid"},
        str(tmp_path),
        policy=STRICT_POLICY,
        confirm=confirm,
    )
    result = json.loads(output)
    assert result["success"] is True
    assert seen == [("edit_file", "src.py")]
    assert f.read_text() == "return mid\n"


def test_dispatch_confirm_callback_exception(tmp_path):
    def confirm(name, inp):
        raise RuntimeError("prompt UI crashed")

    output, _ = _dispatch(
        "edit_file",
        {"path": "src.py", "old_content": "x", "new_content": "y"},
        str(tmp_path),
        policy=STRICT_POLICY,
        confirm=confirm,
    )
    result = json.loads(output)
    assert result["success"] is False
    assert "Confirm callback raised" in result["error"]


def test_dispatch_eval_policy_does_not_need_confirm(tmp_path):
    # EVAL_POLICY uses ALLOW for edit_file, so no callback is required.
    f = tmp_path / "src.py"
    f.write_text("a\n")
    output, _ = _dispatch(
        "edit_file",
        {"path": "src.py", "old_content": "a", "new_content": "b"},
        str(tmp_path),
        policy=EVAL_POLICY,
    )
    result = json.loads(output)
    assert result["success"] is True
    assert f.read_text() == "b\n"
