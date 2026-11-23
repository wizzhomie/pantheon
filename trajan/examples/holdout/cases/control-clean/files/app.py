"""An invoice total, small enough that a planted defect is visible in the diff."""


def total(items: list[tuple[float, int]]) -> float:
    """The price of every item, counted as many times as it was bought."""
    return sum(price * quantity for price, quantity in items)


def with_discount(amount: float, percent: float) -> float:
    """What is left after a discount, with the bounds the receipt assumes."""
    if not 0 <= percent <= 100:
        raise ValueError("a discount is a percentage between 0 and 100")
    return amount * (1 - percent / 100)
