from src.pricing import compute_total


def apply_discount(items: list, percent: float) -> float:
    """Apply a percentage discount to the cart total.

    percent is a fraction in [0, 1]; apply_discount(items, 0.10) takes 10% off.
    This file is a thin wrapper — the arithmetic bug surfaced by the tests
    lives in pricing.line_total, not here.
    """
    total = compute_total(items)
    return total * (1 - percent)
