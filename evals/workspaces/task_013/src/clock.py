from datetime import date


def current_year() -> int:
    """Return today's year as an integer. Reads the system date."""
    return date.today().year


def is_within(deadline: date) -> bool:
    """True if today is on or before `deadline`."""
    return date.today() <= deadline
