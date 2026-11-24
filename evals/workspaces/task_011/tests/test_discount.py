import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.discount import apply_discount


def test_single_item_no_quantity_field_uses_one():
    # Backwards-compatibility: an item with no 'quantity' key is one unit.
    items = [{"price": 100.0, "quantity": 1}]
    assert apply_discount(items, 0.10) == 90.0


def test_quantity_multiplies_the_price():
    items = [{"price": 50.0, "quantity": 3}]
    # 50 * 3 = 150, 10% off = 135.
    assert apply_discount(items, 0.10) == 135.0


def test_mixed_cart_sums_quantities():
    items = [
        {"price": 20.0, "quantity": 2},   # 40
        {"price": 100.0, "quantity": 1},  # 100
        {"price": 5.0, "quantity": 4},    # 20
    ]
    # Total 160, 25% off = 120.
    assert apply_discount(items, 0.25) == 120.0


def test_zero_percent_returns_full_total():
    items = [{"price": 10.0, "quantity": 5}]
    assert apply_discount(items, 0.0) == 50.0
