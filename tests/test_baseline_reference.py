"""The naive baseline must receive and apply a task's reference check so
"passed pytest + wrong fix" doesn't inflate the baseline's apparent pass rate.
"""
from evals.naive_baseline import run_naive_baseline


def _make_workspace(tmp_path, src: str, tests: str):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "src" / "mod.py").write_text(src)
    (tmp_path / "tests" / "test_mod.py").write_text(tests)
    return tmp_path


def test_baseline_no_reference_is_no_reference_match(tmp_path):
    src = "def find_max(xs):\n    m = xs[0]\n    for x in xs:\n        if x < m:\n            m = x\n    return m\n"
    tests = (
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
        "from src.mod import find_max\n"
        "def test_max(): assert find_max([1, 7, 3]) == 7\n"
    )
    ws = _make_workspace(tmp_path, src, tests)
    r = run_naive_baseline("find_max in src/mod.py is backwards", str(ws))
    assert r["passed"] is True
    assert r["reference_match"]["fix_match"] == "no_reference"
    assert r["fix_passed"] is True


def test_baseline_pass_with_matching_reference_is_fix_passed(tmp_path):
    src = "def find_max(xs):\n    m = xs[0]\n    for x in xs:\n        if x < m:\n            m = x\n    return m\n"
    tests = (
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
        "from src.mod import find_max\n"
        "def test_max(): assert find_max([1, 7, 3]) == 7\n"
    )
    ws = _make_workspace(tmp_path, src, tests)
    ref = {"files": ["src/mod.py"]}
    r = run_naive_baseline("find_max in src/mod.py is backwards", str(ws), reference=ref)
    assert r["passed"] is True
    assert r["reference_match"]["fix_match"] == "correct"
    assert r["fix_passed"] is True


def test_baseline_pass_wrong_file_marks_fix_fail(tmp_path):
    """If the baseline patches src/mod.py but the reference pins the fix to
    src/other.py, the run is pytest-green but NOT a correct fix."""
    src = "def find_max(xs):\n    m = xs[0]\n    for x in xs:\n        if x < m:\n            m = x\n    return m\n"
    tests = (
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
        "from src.mod import find_max\n"
        "def test_max(): assert find_max([1, 7, 3]) == 7\n"
    )
    ws = _make_workspace(tmp_path, src, tests)
    (ws / "src" / "other.py").write_text("# the 'real' fix file per the reference\n")
    ref = {"files": ["src/other.py"]}
    r = run_naive_baseline("find_max in src/mod.py is backwards", str(ws), reference=ref)
    assert r["passed"] is True, "pytest did go green"
    assert r["reference_match"]["fix_match"] == "wrong_file"
    assert r["fix_passed"] is False, (
        "pytest green but reference says the fix should have been in src/other.py — "
        "this must NOT inflate the baseline's honest fix rate"
    )


def test_baseline_no_mutation_matches_is_no_patch_label(tmp_path):
    """When the baseline finds a candidate function but no mutation turns
    the suite green, the final result's fix_match must be no_patch (not
    wrong_file), because no patch was ever applied."""
    src = "def f():\n    return 'unreachable'\n"
    tests = (
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
        "from src.mod import f\n"
        # No mutation the baseline knows produces this string:
        "def test_impossible(): assert f() == 'cannot_be_synthesized_by_regex_mutations'\n"
    )
    ws = _make_workspace(tmp_path, src, tests)
    ref = {"files": ["src/mod.py"]}
    r = run_naive_baseline("f in src/mod.py is wrong", str(ws), reference=ref)
    assert r["passed"] is False
    assert r["reference_match"]["fix_match"] == "no_patch", (
        f"baseline tried mutations but none landed; the final state has no "
        f"patch applied, so fix_match must be no_patch, got "
        f"{r['reference_match']['fix_match']!r}"
    )
    assert r["fix_passed"] is False


def test_baseline_final_contents_covers_all_ref_files(tmp_path):
    """The baseline's final_file_contents must include every file listed in
    reference.files, not just the one it mutated. Otherwise a must_contain
    token in a file the baseline didn't touch would be checked against an
    empty body and reported as missing even if the token is on disk."""
    src = "def find_max(xs):\n    m = xs[0]\n    for x in xs:\n        if x < m:\n            m = x\n    return m\n"
    tests = (
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
        "from src.mod import find_max\n"
        "def test_max(): assert find_max([1, 7, 3]) == 7\n"
    )
    ws = _make_workspace(tmp_path, src, tests)
    # A second ref file the baseline will never touch, but whose on-disk
    # contents the reference check wants to inspect.
    (ws / "src" / "sidecar.py").write_text("# contains the magic token\nMAGIC_TOKEN = 1\n")
    ref = {"files": ["src/mod.py", "src/sidecar.py"]}
    r = run_naive_baseline("find_max in src/mod.py is backwards", str(ws), reference=ref)
    assert r["passed"] is True
    # Both ref files are present in final_file_contents.
    assert set(r["final_file_contents"].keys()) == {"src/mod.py", "src/sidecar.py"}
    # The sidecar's contents reflect what's on disk, not an empty string.
    assert "MAGIC_TOKEN" in r["final_file_contents"]["src/sidecar.py"]
