import sys, os, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.aggregator import average

def test_average_basic():
    assert average([1, 2, 3, 4, 5]) == 3.0

def test_average_single():
    assert average([10]) == 10.0

def test_average_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        average([])

def test_average_floats():
    assert abs(average([1.5, 2.5]) - 2.0) < 1e-9
