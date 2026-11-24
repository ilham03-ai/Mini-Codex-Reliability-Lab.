import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import override_config


def test_returns_a_distinct_top_level_dict():
    base = {"debug": False}
    out = override_config(base, {"debug": True})
    assert out["debug"] is True
    assert base["debug"] is False  # base must not be mutated


def test_nested_dict_in_base_is_not_mutated():
    base = {"server": {"host": "localhost", "port": 8000}}
    _ = override_config(base, {"server": {"port": 9000}})
    # base["server"] must not reflect the override.
    assert base["server"] == {"host": "localhost", "port": 8000}


def test_nested_dict_in_returned_value_is_independent():
    base = {"features": {"a": 1}}
    out = override_config(base, {"timeout": 30})
    # Mutating out["features"] afterwards must not affect base["features"].
    out["features"]["a"] = 999
    assert base["features"]["a"] == 1
