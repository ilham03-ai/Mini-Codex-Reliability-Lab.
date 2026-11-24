"""check_permission must denounce unknown tools, not silently DENY them."""
import warnings

import pytest

from sandbox.permissions import check_permission, EVAL_POLICY, Permission


def test_unknown_tool_warns_and_denies():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = check_permission(EVAL_POLICY, "mystery_tool")
    assert result == Permission.DENY
    assert len(caught) == 1
    w = caught[0]
    assert issubclass(w.category, RuntimeWarning)
    assert "mystery_tool" in str(w.message)


def test_known_tool_does_not_warn():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = check_permission(EVAL_POLICY, "read_file")
    assert result == Permission.ALLOW
    assert caught == []
