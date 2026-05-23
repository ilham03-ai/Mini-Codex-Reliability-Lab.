def average(numbers: list) -> float:
    """Return the arithmetic mean of a list of numbers."""
    total = sum(numbers)
    return total / len(numbers)  # BUG: no guard for empty list → ZeroDivisionError
