"""A small billing module: a line total, the invoice total, and money in cents."""

from collections.abc import Iterable


def line_total(price: float, quantity: int) -> float:
    """The price of one line, counted as many times as it was bought."""
    return price * quantity


def invoice_total(lines: Iterable[tuple[float, int]]) -> float:
    """The sum of every line on the invoice."""
    return sum(line_total(price, quantity) for price, quantity in lines)


def to_cents(amount: float) -> int:
    """An amount rounded to whole cents, the way an invoice prints it."""
    return round(amount * 100)
