-- Orders and their line items. This migration is here so that the policy has a gate that
-- only some changes need; it is safe by design.
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL
);
