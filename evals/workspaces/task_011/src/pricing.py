def line_total(item: dict) -> float:
    """Price per line.

    An item has {'price': float, 'quantity': int}. The line total is
    price * quantity. The bug: quantity is ignored when computing the total,
    so every line counts as a single unit no matter the qty.
    """
    return item["price"]  # BUG: should multiply by item["quantity"]


def compute_total(items: list) -> float:
    """Sum the line totals of every item in the cart."""
    return sum(line_total(it) for it in items)
