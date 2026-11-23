from app import total


def test_total_multiplies_by_quantity() -> None:
    assert total([2, 3], [1, 4]) == 14


def test_total_of_nothing_is_zero() -> None:
    assert total([], []) == 0
