"""An invoice total, small enough that a planted defect is visible in the diff."""


def total(items: list[tuple[float, int]]) -> float:
    """The price of every item, counted as many times as it was bought."""
    return sum(price * quantity for price, quantity in items)


def vat(amount: float, rate: float = 0.2) -> float:
    """VAT on an amount, rounded to the nearest cent."""
    return round(amount * rate, 2)
