import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.string_processor import word_frequency, longest_common_prefix, reverse_words

def test_word_frequency_basic():
    freq = word_frequency("the cat sat on the mat")
    assert freq["the"] == 2
    assert freq["cat"] == 1

def test_word_frequency_punctuation():
    freq = word_frequency("hello, world! hello.")
    assert freq["hello"] == 2

def test_lcp_basic():
    assert longest_common_prefix(["flower", "flow", "flight"]) == "fl"

def test_lcp_no_common():
    assert longest_common_prefix(["dog", "racecar", "car"]) == ""

def test_lcp_single():
    assert longest_common_prefix(["alone"]) == "alone"

def test_lcp_empty_string_in_list():
    assert longest_common_prefix(["", "abc"]) == ""

def test_reverse_words():
    assert reverse_words("hello world foo") == "foo world hello"
