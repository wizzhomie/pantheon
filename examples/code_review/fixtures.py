"""The fixtures: small functions, some of them broken on purpose.

Every broken fixture carries two things a machine can verify:

* a ``# BUG`` marker on the offending line, which becomes the golden line number;
* a probe — arguments and the value the *correct* function returns. A fixture only ships
  if running the broken code on the probe gives a different answer, so "the bug is real and
  observable" is checked at build time, not asserted in a README.

Clean fixtures have no marker and no probe: their golden answer is "report nothing", which
is how a review that invents problems gets caught.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

Category = Literal["happy_path", "edge_case", "adversarial"]


@dataclass(frozen=True, slots=True)
class Fixture:
    case_id: str
    category: Category
    tags: tuple[str, ...]
    source: str
    probe: tuple[tuple[Any, ...], Any] | None = None
    """``((args...), expected_from_correct_code)``; present only for broken fixtures."""


FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        case_id="cr-01",
        category="happy_path",
        tags=("arithmetic",),
        source='''\
def remaining_amount(total: float, paid: float) -> float:
    """How much the customer still owes."""
    return round(total + paid, 2)  # BUG
''',
        probe=((100.0, 40.0), 60.0),
    ),
    Fixture(
        case_id="cr-02",
        category="happy_path",
        tags=("vat",),
        source='''\
def vat_amount(net: float, rate: float) -> float:
    """VAT on a net amount, rate given as a fraction such as 0.2."""
    return round(net * rate / 100, 2)  # BUG
''',
        probe=((1000.0, 0.2), 200.0),
    ),
    Fixture(
        case_id="cr-03",
        category="edge_case",
        tags=("dates",),
        source='''\
def is_weekend(day: int) -> bool:
    """Saturday is 5, Sunday is 6."""
    return day > 5  # BUG
''',
        probe=((5,), True),
    ),
    Fixture(
        case_id="cr-04",
        category="happy_path",
        tags=("dates",),
        source='''\
def days_between(start, end) -> int:
    """Whole days from start to end; negative when end is earlier."""
    return (start - end).days  # BUG
''',
        probe=((date(2026, 1, 1), date(2026, 1, 3)), 2),
    ),
    Fixture(
        case_id="cr-05",
        category="edge_case",
        tags=("strings",),
        source='''\
def slugify(title: str) -> str:
    """A URL slug: lower case, words joined by a hyphen."""
    return title.strip().lower().replace(" ", "_")  # BUG
''',
        probe=(("Hello World",), "hello-world"),
    ),
    Fixture(
        case_id="cr-06",
        category="edge_case",
        tags=("lists",),
        source='''\
def chunk(items: list, size: int) -> list[list]:
    """Split a list into consecutive groups of at most ``size`` items."""
    return [items[i : i + size] for i in range(0, len(items), size + 1)]  # BUG
''',
        probe=(([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]),
    ),
    Fixture(
        case_id="cr-07",
        category="adversarial",
        tags=("parsing",),
        source='''\
def parse_bool(value: str) -> bool:
    """Accept the usual spellings of yes and no."""
    return value.strip().lower() in {"yes", "true", "on"}  # BUG
''',
        probe=(("1",), True),
    ),
    Fixture(
        case_id="cr-08",
        category="edge_case",
        tags=("division",),
        source='''\
def safe_div(a: float, b: float) -> float | None:
    """Divide, or return None when the denominator is zero."""
    if b < 0:  # BUG
        return None
    return a / b
''',
        probe=((1.0, 0.0), None),
    ),
    Fixture(
        case_id="cr-09",
        category="happy_path",
        tags=("stats",),
        source='''\
def mean(values: list[float]) -> float:
    """Arithmetic mean of a non-empty list."""
    return sum(values) / (len(values) - 1)  # BUG
''',
        probe=(([2.0, 4.0, 6.0],), 4.0),
    ),
    Fixture(
        case_id="cr-10",
        category="edge_case",
        tags=("strings",),
        source='''\
def truncate(text: str, limit: int) -> str:
    """At most ``limit`` characters, no ellipsis added."""
    return text[: limit + 1]  # BUG
''',
        probe=(("abcdef", 3), "abc"),
    ),
    Fixture(
        case_id="cr-11",
        category="happy_path",
        tags=("arithmetic",),
        source='''\
def percent(part: float, whole: float) -> float:
    """``part`` as a percentage of ``whole``."""
    return round(part / whole, 2)  # BUG
''',
        probe=((25.0, 50.0), 50.0),
    ),
    Fixture(
        case_id="cr-12",
        category="adversarial",
        tags=("lists",),
        source='''\
def largest(numbers: list[int]) -> int:
    """The biggest number; the list is never empty."""
    return min(numbers)  # BUG
''',
        probe=(([3, 9, 4],), 9),
    ),
    Fixture(
        case_id="cr-13",
        category="edge_case",
        tags=("bounds",),
        source='''\
def clamp(value: int, minimum: int, maximum: int) -> int:
    """Keep the value inside the closed interval."""
    return max(minimum, min(value, minimum))  # BUG
''',
        probe=((10, 0, 5), 5),
    ),
    Fixture(
        case_id="cr-14",
        category="adversarial",
        tags=("bounds",),
        source='''\
def valid_age(age: int) -> bool:
    """One hundred and twenty is a valid age."""
    return 0 <= age < 120  # BUG
''',
        probe=((120,), True),
    ),
    Fixture(
        case_id="cr-15",
        category="happy_path",
        tags=("pricing",),
        source='''\
def apply_discount(price: float, percent_off: float) -> float:
    """Price after a discount given in percent."""
    if not 0 <= percent_off <= 100:
        raise ValueError("percent_off must be within 0..100")
    return round(price * (1 - percent_off / 100), 2)
''',
    ),
    Fixture(
        case_id="cr-16",
        category="happy_path",
        tags=("formatting",),
        source='''\
def format_price(amount: float, currency: str = "EUR") -> str:
    """Two decimals and the ISO code, as the invoices render it."""
    return f"{amount:.2f} {currency}"
''',
    ),
    Fixture(
        case_id="cr-17",
        category="happy_path",
        tags=("strings",),
        source='''\
def count_words(text: str) -> int:
    """Words are whitespace-separated; an empty string has none."""
    return len(text.split())
''',
    ),
    Fixture(
        case_id="cr-18",
        category="happy_path",
        tags=("lists",),
        source='''\
def unique(items: list) -> list:
    """Keep the first occurrence of every value, preserving order."""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
''',
    ),
)
