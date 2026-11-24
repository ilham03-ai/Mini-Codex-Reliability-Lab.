"""Unit tests for the naive baseline using synthetic tiny workspaces.

We don't exercise the full 10-task fleet here — that lives in the eval run.
These tests pin down the baseline's own behavior so regressions in the
mutation generator get caught locally.
"""
import pytest

from evals.naive_baseline import (
    run_naive_baseline,
    _mutations_for_body,
    _function_body_span,
)


def _make_workspace(tmp_path, source: str, test_source: str):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "src" / "mod.py").write_text(source)
    (tmp_path / "tests" / "test_mod.py").write_text(test_source)
    return tmp_path


def test_mutations_include_comparison_flips():
    body = "    return a < b\n"
    muts = {label for label, _ in _mutations_for_body(body)}
    assert any("lt" in m for m in muts)


def test_function_body_span_finds_body(tmp_path):
    src = "def f():\n    return 1\n\ndef g():\n    return 2\n"
    span = _function_body_span(src, "f")
    assert span is not None
    a, b = span
    assert a == 0
    body = "\n".join(src.splitlines()[a:b])
    assert "return 1" in body
    assert "return 2" not in body


def test_function_body_span_handles_docstring():
    src = (
        'def f(n):\n'
        '    """docstring.\n\n    Spans several lines and includes code-like\n    tokens like "def g():" that must not confuse us.\n    """\n'
        '    return n + 1\n'
        'def g():\n'
        '    return 0\n'
    )
    span = _function_body_span(src, "f")
    a, b = span
    body = "\n".join(src.splitlines()[a:b])
    assert "return n + 1" in body
    assert "return 0" not in body


def test_function_body_span_handles_decorators():
    src = (
        '@decorator\n'
        '@other(arg=1)\n'
        'def f():\n'
        '    return 1\n'
        'def g():\n'
        '    return 2\n'
    )
    span = _function_body_span(src, "f")
    a, b = span
    # The span must include the decorator lines so a mutation that rewrites
    # the whole block doesn't orphan the decorators.
    body = "\n".join(src.splitlines()[a:b])
    assert "@decorator" in body
    assert "return 1" in body
    assert "return 2" not in body


def test_function_body_span_finds_method_inside_class():
    src = (
        "class A:\n"
        "    def f(self):\n"
        "        return 1\n"
        "    def g(self):\n"
        "        return 2\n"
    )
    span = _function_body_span(src, "f")
    assert span is not None
    body = "\n".join(src.splitlines()[span[0]:span[1]])
    assert "return 1" in body
    assert "return 2" not in body


def test_function_body_span_returns_none_on_syntax_error():
    assert _function_body_span("def f(:\n", "f") is None
    assert _function_body_span("def f():\n    pass\n", "nonexistent") is None


def test_baseline_fixes_comparison_flip(tmp_path):
    src = "def find_max(xs):\n    m = xs[0]\n    for x in xs:\n        if x < m:\n            m = x\n    return m\n"
    tests = (
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
        "from src.mod import find_max\n"
        "def test_max():\n    assert find_max([1, 7, 3]) == 7\n"
    )
    ws = _make_workspace(tmp_path, src, tests)
    r = run_naive_baseline("find_max in src/mod.py is backwards", str(ws))
    assert r["passed"] is True
    assert r["mutation_applied"].startswith("lt")


def test_baseline_fails_cleanly_when_no_symbol_matches(tmp_path):
    src = "def unrelated():\n    return 0\n"
    tests = (
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
        "from src.mod import unrelated\n"
        "def test_x():\n    assert unrelated() == 1\n"
    )
    ws = _make_workspace(tmp_path, src, tests)
    r = run_naive_baseline("something about colors", str(ws))
    assert r["passed"] is False
    assert "no candidate" in r["reason"] or "no mutation" in (r["reason"] or "")


def test_baseline_does_not_mutate_source_workspace(tmp_path):
    """The baseline operates on a tmp copy; the source tree must be byte-identical
    before and after, win or lose. Previously only `--isolated` copied, which
    this test would have caught."""
    import hashlib

    src = "def find_max(xs):\n    m = xs[0]\n    for x in xs:\n        if x < m:\n            m = x\n    return m\n"
    tests = (
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
        "from src.mod import find_max\n"
        "def test_max():\n    assert find_max([1, 7, 3]) == 7\n"
    )
    ws = _make_workspace(tmp_path, src, tests)

    def _digest_tree(root):
        parts = []
        for p in sorted(root.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts:
                parts.append((str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest()))
        return parts

    before = _digest_tree(ws)
    run_naive_baseline("find_max in src/mod.py is backwards", str(ws))
    after = _digest_tree(ws)
    assert before == after, f"source workspace was mutated: before={before}, after={after}"


@pytest.mark.parametrize("outcome", ["expected_pass", "expected_fail"])
def test_baseline_never_mutates_source_tree(tmp_path, outcome):
    """Whether the baseline succeeds or fails, the source workspace must stay
    byte-identical. This is the real invariant: the tmp copy is where
    mutations happen, and the caller's tree is never touched."""
    import hashlib

    if outcome == "expected_pass":
        src = "def flip(x):\n    return not x\n"  # trivially flippable? no
        src = "def find_max(xs):\n    m = xs[0]\n    for x in xs:\n        if x < m:\n            m = x\n    return m\n"
        tests = (
            "import sys, os\n"
            "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
            "from src.mod import find_max\n"
            "def test_max(): assert find_max([1, 7, 3]) == 7\n"
        )
        issue = "find_max in src/mod.py is backwards"
    else:
        # Test that can't be satisfied by any mutation the baseline knows.
        src = "def unreachable():\n    return 'red'\n"
        tests = (
            "import sys, os\n"
            "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
            "from src.mod import unreachable\n"
            "def test_impossible(): assert unreachable() == 'a string the baseline can\\'t synthesize'\n"
        )
        issue = "unreachable in src/mod.py returns the wrong color"

    ws = _make_workspace(tmp_path, src, tests)
    before = hashlib.sha256((ws / "src" / "mod.py").read_bytes()).hexdigest()

    r = run_naive_baseline(issue, str(ws))

    after = hashlib.sha256((ws / "src" / "mod.py").read_bytes()).hexdigest()
    assert before == after, \
        f"source workspace mutated by baseline (outcome={outcome}, passed={r['passed']})"
