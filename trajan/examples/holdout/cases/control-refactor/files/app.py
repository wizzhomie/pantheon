"""An invoice total, small enough that a planted defect is visible in the diff."""


def total(items: list[tuple[float, int]]) -> float:
    """The price of every item, counted as many times as it was bought."""
    result = 0.0
    for price, quantity in items:
        result += price * quantity
    return result
