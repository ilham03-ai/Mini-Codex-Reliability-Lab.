import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.version import parse_version


def test_parses_simple_triple():
    assert parse_version("1.2.3") == (1, 2, 3)


def test_parses_zeros():
    assert parse_version("0.0.0") == (0, 0, 0)


def test_large_numbers():
    assert parse_version("10.20.30") == (10, 20, 30)


# BAD TEST — asserts the buggy fallback behavior as if it were correct.
# A real fix needs to update or remove this test alongside the source change.
def test_invalid_returns_zero_tuple():
    assert parse_version("not.a.version") == (0, 0, 0)


def test_invalid_input_raises_value_error():
    with pytest.raises(ValueError):
        parse_version("not.a.version")


def test_too_few_parts_raises():
    with pytest.raises(ValueError):
        parse_version("1.2")


def test_negative_component_raises():
    with pytest.raises(ValueError):
        parse_version("1.-2.3")
