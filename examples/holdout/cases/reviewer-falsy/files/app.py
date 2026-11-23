"""An invoice total, small enough that a planted defect is visible in the diff."""


def total(items: list[tuple[float, int]]) -> float:
    """The price of every item, counted as many times as it was bought."""
    return sum(price * quantity for price, quantity in items)


def line_total(price: float, quantity: int | None = None) -> float:
    """What one invoice line costs; a missing quantity means a single item."""
    if not quantity:
        quantity = 1
    return price * quantity
