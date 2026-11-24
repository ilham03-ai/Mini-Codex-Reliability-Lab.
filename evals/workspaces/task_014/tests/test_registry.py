import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.registry import add_tag


def test_single_call_returns_single_item_list():
    assert add_tag("alpha") == ["alpha"]


def test_second_call_is_independent():
    # If the default list is shared across calls, the second call sees
    # "alpha" lingering from the first.
    _ = add_tag("alpha")
    assert add_tag("beta") == ["beta"]


def test_passing_explicit_list_does_not_leak_into_default():
    assert add_tag("x", ["seed"]) == ["seed", "x"]
    # The default list should still be empty, so this call stands alone.
    assert add_tag("y") == ["y"]
