"""The output contract: one shipment intake request, as a freight forwarder needs it.

Three schema decisions worth noticing, because they are what the loop gets to work with:

* dates are ISO strings, not ``datetime.date`` — the model returns text, the format check
  owns the shape, and a string compares exactly against the golden value in a report;
* everything the email might not state is optional *at the type level*; whether a missing
  value is acceptable is a policy question, answered by :mod:`checks` and ``unresolved``,
  not by the schema;
* ``evidence`` is part of the output, not a side channel: asking the model for the quote it
  extracted a value from is what makes the cheapest anti-hallucination check possible.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from qagent import StrictModel

RequestType = Literal["quote", "booking", "change", "other"]
"""What the sender wants: a price, a confirmed booking, an amendment, or something else."""

Packaging = Literal["pallet", "box", "drum", "bag", "container", "unknown"]


class Evidence(StrictModel):
    """A verbatim fragment of the email that supports one extracted field."""

    field_path: str = Field(
        min_length=1, max_length=80, description="dotted path, e.g. origin.city"
    )
    quote: str = Field(min_length=1, max_length=300)


class Contact(StrictModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None


class Location(StrictModel):
    city: str | None = None
    country_code: str | None = Field(default=None, description="ISO 3166-1 alpha-2")
    postcode: str | None = None


class CargoItem(StrictModel):
    description: str = Field(min_length=1, max_length=200)
    quantity: int | None = Field(default=None, ge=1, le=100_000)
    weight_kg: float | None = Field(default=None, ge=0, le=1_000_000)
    temperature_c: float | None = Field(default=None, ge=-60, le=60)
    packaging: Packaging = "unknown"


class ExtractedIntake(StrictModel):
    """One email, reduced to the fields a quote or a booking needs."""

    request_type: RequestType
    contact: Contact = Field(default_factory=Contact)
    origin: Location = Field(default_factory=Location)
    destination: Location = Field(default_factory=Location)
    cargo: list[CargoItem] = Field(default_factory=list)
    cargo_requirements: list[str] = Field(
        default_factory=list,
        description="handling needs stated by the sender, e.g. reefer, chilled, frozen, hazmat",
    )
    pickup_date: str | None = Field(default=None, description="ISO date, YYYY-MM-DD")
    delivery_date: str | None = None
    quote_currency: str | None = Field(default=None, description="ISO 4217")
    quote_amount: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=500)
    unresolved: list[str] = Field(
        default_factory=list,
        description="paths the email does not state; an honest abstention, not a defect",
    )
    evidence: list[Evidence] = Field(default_factory=list)
