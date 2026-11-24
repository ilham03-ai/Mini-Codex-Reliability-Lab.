import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.clock import current_year, is_within


def test_current_year_matches_system_clock():
    # The test is intentionally written in a way that will fail whenever
    # today's year disagrees with what src.clock reports. If a conftest
    # freezes the date, this test is the one that catches it.
    assert current_year() == date.today().year


def test_is_within_true_for_future_deadline():
    tomorrow = date.fromordinal(date.today().toordinal() + 1)
    assert is_within(tomorrow) is True


def test_is_within_false_for_past_deadline():
    yesterday = date.fromordinal(date.today().toordinal() - 1)
    assert is_within(yesterday) is False
