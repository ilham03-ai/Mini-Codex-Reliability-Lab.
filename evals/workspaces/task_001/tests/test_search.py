import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.search import binary_search

def test_finds_element_at_start():
    assert binary_search([1, 3, 5, 7, 9], 1) == 0

def test_finds_element_in_middle():
    assert binary_search([1, 3, 5, 7, 9], 5) == 2

def test_finds_element_at_end():
    assert binary_search([1, 3, 5, 7, 9], 9) == 4

def test_returns_minus_one_when_not_found():
    assert binary_search([1, 3, 5, 7, 9], 4) == -1

def test_single_element():
    assert binary_search([42], 42) == 0
