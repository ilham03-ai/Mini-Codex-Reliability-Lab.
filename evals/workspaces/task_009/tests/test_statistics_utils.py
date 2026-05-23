import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.statistics_utils import mean, sample_variance, sample_std

def test_mean():
    assert mean([1, 2, 3, 4, 5]) == 3.0

def test_sample_variance_simple():
    # Variance of [2, 4, 4, 4, 5, 5, 7, 9] = 4.571... (sample)
    data = [2, 4, 4, 4, 5, 5, 7, 9]
    result = sample_variance(data)
    assert abs(result - 4.571428571) < 1e-6

def test_sample_variance_two_elements():
    assert sample_variance([1, 3]) == 2.0

def test_sample_std():
    # std of [2,4,4,4,5,5,7,9] ≈ 2.138
    assert abs(sample_std([2, 4, 4, 4, 5, 5, 7, 9]) - 2.13809) < 1e-4
