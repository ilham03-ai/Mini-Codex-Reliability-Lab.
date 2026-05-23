import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.math_utils import fibonacci

def test_fib_0():
    assert fibonacci(0) == 0

def test_fib_1():
    assert fibonacci(1) == 1

def test_fib_2():
    assert fibonacci(2) == 1

def test_fib_5():
    assert fibonacci(5) == 5

def test_fib_10():
    assert fibonacci(10) == 55
