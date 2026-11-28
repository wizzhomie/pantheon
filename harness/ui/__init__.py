"""The workscreen: one local page that shows a box and talks about it.

The page is a reading instrument, not a second gate: it composes what the box already records
(box status, frame, rails, plans, tasks, events) and shows the same files the gates read, so a
claim on the screen cannot drift from what ``pantheon status`` would say. The chat next to it
is a conversation about those facts, with the price of each answer named.

Nothing in this package writes into the project. The conversations live in the box's own
volume (``castra/<box>/ui/``), like every other record of work on the project.
"""
