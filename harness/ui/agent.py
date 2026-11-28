"""The box's own agent, and the bridge the page uses to talk to it.

The page's chat was a stand-in: one HTTP call to a model with a prompt built from the box. That
is a useful reader, but it is not an agent — no tools of its own, no memory it keeps, nothing it
can do. The box already runs one: the pinned Hermes image, its gateway, its sessions, its
memories and its skills. This module is the bridge — one ``docker exec`` per message, into a
named session, and the agent's own words back.

A message is a chat turn, not a task. What the agent *does* goes through the corridors the box
already has: a task with a contract, a plan, a step. Nothing here caps the agent: it is the
owner's working partner, and its guardrails are its own (SOUL.md, memories, rules) plus the
harness's rule about secrets. The bridge only refuses what cannot work at all: no container, no
answer, or a run that never ended.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any

from .. import box as boxes
from .. import container

HERMES = "/opt/hermes/.venv/bin/hermes"
WORKDIR = "/opt/data"
TIMEOUT_SECONDS = 900.0
"""An agent turn is work, not a request: it may read, run checks and spawn subagents. The
ceiling is the page's patience, not a policy — a turn that outlives it keeps running in the box."""

SESSIONS = {"orchestrator": "pantheon-orchestrator", "architect": "pantheon-architect"}
"""One named session per role, created on first use: the agent's own memory of that conversation."""

NOISE = ("install out of sync", "Could not back up")
"""Lines the image prints about itself, not about the answer: never quoted as a refusal."""


@dataclass(frozen=True, slots=True)
class Answer:
    """What the agent said, in which session, and what it cost in tokens."""

    text: str
    session: str
    tokens: dict[str, int]
    seconds: float

    def json(self) -> dict[str, Any]:
        return {
            "answer": self.text,
            "session": self.session,
            "tokens": self.tokens,
            "seconds": self.seconds,
        }


def name(box: boxes.Box) -> str:
    """The box's agent container, named the way compose names it."""
    return f"{container.project_name(box)}-{container.SERVICE}-1"


def available(box: boxes.Box) -> bool:
    """Whether the box's agent is up and can be asked at all."""
    try:
        return container.state(box).running
    except boxes.HarnessError:
        return False


def ask(box: boxes.Box, role: str, text: str, *, timeout: float = TIMEOUT_SECONDS) -> Answer:
    """One turn in the role's session, or the reason it did not happen."""
    session = SESSIONS.get(role)
    if session is None:
        raise boxes.HarnessError(
            f"no agent session for role {role!r}; the two are " + ", ".join(sorted(SESSIONS))
        )
    if not available(box):
        raise boxes.HarnessError(
            f"the box's agent is not running: `pantheon up {box.name}` starts it. The page talks "
            "to the agent in the box, not to a model of its own"
        )
    argv = [
        *container.docker_prefix(),
        "exec",
        "-u",
        str(os.getuid()),
        "-w",
        WORKDIR,
        name(box),
        HERMES,
        "chat",
        "-Q",
        "--format",
        "stream-json",
        "-c",
        session,
        "--create-if-missing",
        "-q",
        text,
    ]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise boxes.HarnessError(
            f"the agent did not answer in {timeout:.0f} seconds: the turn is still running in "
            "the box, and its session will show what it did"
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise boxes.HarnessError(f"cannot reach the box's agent: {error}") from error
    answer = read(done.stdout)
    if answer is None:
        raise boxes.HarnessError("the agent answered nothing: " + _detail(done))
    return answer


def read(stdout: str) -> Answer | None:
    """The last ``result`` event of a ``stream-json`` turn, or ``None`` when there was none."""
    text = ""
    session = ""
    tokens: dict[str, int] = {}
    seconds = 0.0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "result":
            continue
        said = event.get("text")
        text = said.strip() if isinstance(said, str) else ""
        identifier = event.get("session_id")
        session = identifier if isinstance(identifier, str) else ""
        counted = event.get("tokens")
        if isinstance(counted, dict):
            tokens = {
                str(key): value
                for key, value in counted.items()
                if isinstance(value, int) and not isinstance(value, bool)
            }
        duration = event.get("duration_ms")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            seconds = round(float(duration) / 1000, 1)
    if not text:
        return None
    return Answer(text=text, session=session, tokens=tokens, seconds=seconds)


def _detail(done: subprocess.CompletedProcess[str]) -> str:
    lines = [
        line.strip()
        for line in (done.stderr or done.stdout).splitlines()
        if line.strip() and not any(noise in line for noise in NOISE)
    ]
    return "; ".join(lines[-3:]) or f"exit {done.returncode}"
