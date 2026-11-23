"""A tiny invoice total, with one planted defect for the verification demo.

The defect is the kind an AI agent ships confidently: the happy path (one of each item) is
right, the arithmetic for quantities is not, and nothing in the code *looks* wrong.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    name: str
    price: float
    quantity: int = 1


def total(items: list[Item]) -> float:
    """Sum the prices of the items on an invoice."""
    return float(sum(item.price for item in items))
