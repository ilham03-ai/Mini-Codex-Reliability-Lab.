"""Pytest configuration for src.clock tests.

Historical note: a teammate added a session-wide date freeze while debugging a
calendar test. The freeze was supposed to be scoped to that one test, but
`autouse=True` + session scope means every test sees the frozen date. The
fixture is the bug.
"""
from datetime import date

import pytest


class _FrozenDate(date):
    @classmethod
    def today(cls):
        # BUG: pins today's date to a fixed day for every test in the suite.
        return cls(2015, 6, 15)


@pytest.fixture(autouse=True, scope="session")
def _freeze_date(session_mocker=None):
    # Monkey-patch datetime.date in src.clock so date.today() returns the
    # frozen date. The patch runs once per session and never unwinds in a way
    # the fixture controls — it stays active through the whole run.
    import src.clock as clock_module
    original = clock_module.date
    clock_module.date = _FrozenDate
    yield
    clock_module.date = original
