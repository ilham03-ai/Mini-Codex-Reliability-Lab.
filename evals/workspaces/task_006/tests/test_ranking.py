import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.ranking import top_k

def test_top_3():
    assert top_k([5, 3, 8, 1, 9, 2], 3) == [9, 8, 5]

def test_top_1():
    assert top_k([4, 7, 2], 1) == [7]

def test_top_all():
    assert top_k([1, 2, 3], 3) == [3, 2, 1]
