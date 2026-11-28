"""The workscreen: one local page that shows a box, talks about it, and takes the owner's mark.

The page reads before it writes. Everything it shows is composed from what the box already
records (box status, frame, rails, plans, packages, tasks, events) with the same functions the
commands use, so a claim on the screen cannot drift from what ``pantheon status`` would say. The
chat next to it is a conversation about those facts, with the price of each answer named.

Writes go through one door and one lane (``harness/ui/actions.py``): every button calls the
function the CLI calls, a refusal is the gate's own sentence, and two clicks cannot start two
steps. The page is not a second gate — it has no rules of its own to break.

Every record of work on the project lives in the box's own volume (``castra/<box>/ui/``): the
two conversations, the token, and nothing else. The project is changed only by the gates.
"""
