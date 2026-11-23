# Billing

A tiny invoice total, its tests, and the migration guard.

The invoice total adds `price * quantity` for every line. `to_cents` prints an amount the way an
invoice does: rounded to whole cents.
