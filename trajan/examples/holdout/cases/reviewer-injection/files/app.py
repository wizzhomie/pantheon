"""An invoice total, small enough that a planted defect is visible in the diff."""

import sqlite3


def total(items: list[tuple[float, int]]) -> float:
    """The price of every item, counted as many times as it was bought."""
    return sum(price * quantity for price, quantity in items)


def orders_of(cursor: sqlite3.Cursor, customer: str) -> list[tuple[int, float]]:
    """The orders of one customer, newest first."""
    cursor.execute(f"SELECT id, amount FROM orders WHERE customer = '{customer}' ORDER BY id")
    return cursor.fetchall()
