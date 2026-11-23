from app import gross, invoice_total, line_total, to_cents


def test_one_line_is_its_price() -> None:
    assert line_total(3.0, 1) == 3.0


def test_quantity_multiplies_the_price() -> None:
    assert line_total(3.0, 4) == 12.0


def test_an_empty_invoice_is_zero() -> None:
    assert invoice_total([]) == 0.0


def test_the_lines_are_added() -> None:
    assert invoice_total([(3.0, 2), (1.5, 2)]) == 9.0


def test_cents_are_whole_numbers() -> None:
    assert to_cents(9.99) == 999


def test_gross_adds_vat_and_rounds() -> None:
    assert gross(12.34) == 14.81


def test_gross_takes_a_rate() -> None:
    assert gross(10.0, 0.1) == 11.0


def test_gross_of_zero_is_zero() -> None:
    assert gross(0.0) == 0.0
