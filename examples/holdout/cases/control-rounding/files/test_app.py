from app import total, vat


def test_a_single_item_is_counted_once() -> None:
    assert total([(2.5, 1)]) == 2.5


def test_quantity_multiplies_the_price() -> None:
    assert total([(2.5, 3)]) == 7.5


def test_an_empty_invoice_is_zero() -> None:
    assert total([]) == 0.0


def test_vat_rounds_to_the_cent() -> None:
    assert vat(19.99, 0.2) == 4.0


def test_vat_of_zero_is_zero() -> None:
    assert vat(0.0) == 0.0
