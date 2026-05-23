import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.collections_utils import flatten, count_occurrences

def test_flatten_basic():
    assert flatten([[1, 2], [3, 4]]) == [1, 2, 3, 4]

def test_flatten_mixed():
    assert flatten([[1, 2], 3, [4]]) == [1, 2, 3, 4]

def test_count_case_insensitive():
    assert count_occurrences(["apple", "Apple", "APPLE", "banana"], "apple") == 3

def test_count_numbers():
    assert count_occurrences([1, 2, 1, 3, 1], 1) == 3

def test_count_zero():
    assert count_occurrences(["a", "b"], "c") == 0
