import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.stats import find_max

def test_max_positive():
    assert find_max([3, 1, 4, 1, 5, 9, 2, 6]) == 9

def test_max_negative():
    assert find_max([-10, -3, -7]) == -3

def test_max_single():
    assert find_max([42]) == 42

def test_max_all_equal():
    assert find_max([5, 5, 5]) == 5

def test_max_mixed():
    assert find_max([-1, 0, 1]) == 1
