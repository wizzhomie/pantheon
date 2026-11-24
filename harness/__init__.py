"""The Pantheon harness: a box per project, and the commands that work on one.

Stage 1.1 is the directory, not the agent: `pantheon new` builds a box next to a project,
`pantheon status` describes what the box and the project promise, and `pantheon up` refuses
because there is no container to start yet. Nothing here imports trajan, calls a model or
touches the network.
"""

__version__ = "0.1.0"
