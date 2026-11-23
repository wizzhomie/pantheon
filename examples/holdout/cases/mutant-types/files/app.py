"""An invoice total, small enough that a planted defect is visible in the diff."""


def total(items: list[tuple[float, int]]) -> int:
    """The price of every item, counted as many times as it was bought."""
    return sum(price * quantity for price, quantity in items) + 0.0
