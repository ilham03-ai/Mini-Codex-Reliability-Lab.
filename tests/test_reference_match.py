from evals.failure_taxonomy import classify_against_reference


def _patch(path, diff):
    return {"path": path, "diff": diff}


def test_no_reference_returns_no_reference():
    r = {"patches": [], "tests_modified_count": 0, "final_test_passed": True}
    assert classify_against_reference(r, None)["fix_match"] == "no_reference"
    assert classify_against_reference(r, {})["fix_match"] == "no_reference"


def test_correct_fix_hits_file_and_tokens():
    result = {
        "final_test_passed": True,
        "tests_modified_count": 0,
        "patches": [_patch("src/search.py", "@@\n-    return mid - 1\n+    return mid\n")],
    }
    ref = {"files": ["src/search.py"], "must_contain": ["return mid"], "must_not_contain": ["return mid - 1"]}
    out = classify_against_reference(result, ref)
    assert out["fix_match"] == "correct"
    assert out["touched_right"] is True
    assert out["tokens_missing"] == []


def test_wrong_file_is_detected():
    result = {
        "final_test_passed": False,
        "tests_modified_count": 0,
        "patches": [_patch("src/elsewhere.py", "+something")],
    }
    out = classify_against_reference(result, {"files": ["src/search.py"]})
    assert out["fix_match"] == "wrong_file"
    assert out["touched_extra"] == ["src/elsewhere.py"]


def test_test_tampering_takes_priority():
    result = {
        "final_test_passed": True,
        "tests_modified_count": 1,
        "tests_modified": ["tests/test_formatter.py"],
        "patches": [_patch("tests/test_formatter.py", "+something")],
    }
    ref = {"files": ["src/formatter.py"], "forbid_test_edits": True}
    out = classify_against_reference(result, ref)
    assert out["fix_match"] == "test_tampering_detected"
    assert out["tests_modified_forbidden"] is True


def test_partial_fix_on_right_file_but_missing_tokens():
    result = {
        "final_test_passed": False,
        "tests_modified_count": 0,
        "patches": [_patch("src/math_utils.py", "@@\n+    pass\n")],
    }
    ref = {"files": ["src/math_utils.py"], "must_contain": ["return fibonacci"]}
    out = classify_against_reference(result, ref)
    assert out["fix_match"] == "partial"
    assert "return fibonacci" in out["tokens_missing"]


def test_token_check_uses_final_file_contents_when_present():
    # Patch diff only shows context, not a + line, for the required token.
    # Without final_file_contents the check would miss it.
    diff = "@@ -1,3 +1,3 @@\n def f():\n-    pass\n+    return fibonacci(n - 1) + fibonacci(n - 2)\n"
    result = {
        "final_test_passed": True,
        "tests_modified_count": 0,
        "patches": [_patch("src/math_utils.py", diff)],
        "final_file_contents": {
            "src/math_utils.py": "def f():\n    return fibonacci(n - 1) + fibonacci(n - 2)\n",
        },
    }
    ref = {"files": ["src/math_utils.py"], "must_contain": ["return fibonacci"]}
    out = classify_against_reference(result, ref)
    assert out["fix_match"] == "correct"
    assert out["tokens_missing"] == []
    assert out["token_check_source"] == "final_file"


def test_token_check_falls_back_to_diff_without_final_content():
    diff = "@@ -1,2 +1,2 @@\n def f():\n-    pass\n+    return 1\n"
    result = {
        "final_test_passed": True,
        "tests_modified_count": 0,
        "patches": [_patch("src/foo.py", diff)],
    }
    ref = {"files": ["src/foo.py"], "must_contain": ["return 1"]}
    out = classify_against_reference(result, ref)
    assert out["fix_match"] == "correct"
    assert out["token_check_source"] == "diff_added"


def test_must_contain_supports_or_alternatives():
    # A nested list is an "any-of" slot: one of the alternatives suffices.
    result = {
        "final_test_passed": True,
        "tests_modified_count": 0,
        "patches": [_patch("src/s.py", "stub")],
        "final_file_contents": {"src/s.py": "return reversed(words)\n"},
    }
    ref = {"files": ["src/s.py"], "must_contain": [["reversed", "[::-1]"]]}
    out = classify_against_reference(result, ref)
    assert out["fix_match"] == "correct"
    assert "reversed" in out["tokens_present"]

    # The other alternative also satisfies.
    result["final_file_contents"]["src/s.py"] = "return words[::-1]\n"
    out = classify_against_reference(result, ref)
    assert out["fix_match"] == "correct"

    # Neither alternative present → partial, missing slot reported as a | b.
    result["final_file_contents"]["src/s.py"] = "return words\n"
    out = classify_against_reference(result, ref)
    assert out["fix_match"] == "partial"
    assert "reversed | [::-1]" in out["tokens_missing"]


def test_must_not_contain_checked_in_final_file():
    # Token the fix must REMOVE must not appear in the final file.
    result = {
        "final_test_passed": True,
        "tests_modified_count": 0,
        "patches": [_patch("src/search.py", "stub")],
        "final_file_contents": {"src/search.py": "def f():\n    return mid\n"},
    }
    ref = {"files": ["src/search.py"], "must_not_contain": ["return mid - 1"]}
    out = classify_against_reference(result, ref)
    assert out["forbidden_tokens_present"] == []
    assert out["fix_match"] == "correct"

    # If the forbidden token is still there, flag as partial.
    result["final_file_contents"]["src/search.py"] = "def f():\n    return mid - 1\n"
    out = classify_against_reference(result, ref)
    assert out["forbidden_tokens_present"] == ["return mid - 1"]
    assert out["fix_match"] == "partial"


def test_over_editing_flagged_only_on_failure():
    result_fail = {
        "final_test_passed": False,
        "tests_modified_count": 0,
        "patches": [
            _patch("src/search.py", "+return mid"),
            _patch("src/other.py", "+touched"),
        ],
    }
    ref = {"files": ["src/search.py"]}
    assert classify_against_reference(result_fail, ref)["fix_match"] == "over_editing"
    result_pass = {**result_fail, "final_test_passed": True}
    assert classify_against_reference(result_pass, ref)["fix_match"] == "correct_with_extra"


def test_zero_patches_is_no_patch_not_wrong_file():
    """A run with no patches at all is not a 'wrong file' event — it never
    touched any file. The honest label is 'no_patch' so the report doesn't
    inflate the wrong_file bucket with runs that produced nothing."""
    result = {
        "final_test_passed": False,
        "tests_modified_count": 0,
        "patches": [],
    }
    ref = {"files": ["src/search.py"]}
    assert classify_against_reference(result, ref)["fix_match"] == "no_patch"


def test_patched_the_wrong_file_is_still_wrong_file():
    """Regression guard for fix 1: an actual edit to a non-reference file
    must still be classified as wrong_file, not swallowed by no_patch."""
    result = {
        "final_test_passed": False,
        "tests_modified_count": 0,
        "patches": [_patch("src/other.py", "+touched")],
    }
    ref = {"files": ["src/search.py"]}
    assert classify_against_reference(result, ref)["fix_match"] == "wrong_file"
