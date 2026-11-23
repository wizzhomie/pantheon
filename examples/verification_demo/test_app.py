"""The tests the project already had; one of them catches the planted defect."""

from __future__ import annotations

# The demo is a standalone project: pytest runs with this directory as the root, so `app`
# is a top-level module. mypy checks it from the repository root and cannot see that path.
from app import Item, total  # type: ignore[import-not-found]


def test_one_of_each_item() -> None:
    assert total([Item("pen", 2.0), Item("pad", 5.0)]) == 7.0


def test_empty_invoice_is_free() -> None:
    assert total([]) == 0.0


def test_quantity_multiplies_the_price() -> None:
    assert total([Item("pen", 2.0, quantity=3)]) == 6.0
