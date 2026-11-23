from app import total, with_discount


def test_a_single_item_is_counted_once() -> None:
    assert total([(2.5, 1)]) == 2.5


def test_quantity_multiplies_the_price() -> None:
    assert total([(2.5, 3)]) == 7.5


def test_an_empty_invoice_is_zero() -> None:
    assert total([]) == 0.0


def test_a_discount_reduces_the_amount() -> None:
    assert with_discount(200.0, 12.5) == 175.0
