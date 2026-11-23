import pytest
from app import total, with_discount


def test_a_single_item_is_counted_once() -> None:
    assert total([(2.5, 1)]) == 2.5


def test_quantity_multiplies_the_price() -> None:
    assert total([(2.5, 3)]) == 7.5


def test_an_empty_invoice_is_zero() -> None:
    assert total([]) == 0.0


def test_a_discount_reduces_the_amount() -> None:
    assert with_discount(200.0, 12.5) == 175.0


def test_no_discount_leaves_the_amount_alone() -> None:
    assert with_discount(200.0, 0.0) == 200.0


def test_a_full_discount_empties_the_amount() -> None:
    assert with_discount(200.0, 100.0) == 0.0


def test_a_discount_below_zero_is_refused() -> None:
    with pytest.raises(ValueError):
        with_discount(200.0, -1.0)


def test_a_discount_above_a_hundred_is_refused() -> None:
    with pytest.raises(ValueError):
        with_discount(200.0, 101.0)
