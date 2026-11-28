"""The workscreen's HTTP face: one local page, a small JSON API, and the box's own token.

The server answers only for the box it was started with, and only to a caller that presents the
box's token: the page can spend money on a conversation, so "anyone who reaches the port" is not
the same as "the owner". The token is a file in the box's own volume, mode 600, never a default.

Reading is the page's ordinary work: it asks, and the answer is composed from disk. Writing goes
through one door — ``POST /api/action`` — and that door opens only onto
:mod:`harness.ui.actions`, where every act is the CLI's own function and every refusal is the
gate's own sentence. Two clicks cannot start two steps: the server keeps one lane, and the second
click is told what is running.
"""

from __future__ import annotations

import contextlib
import json
import re
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import box as boxes
from .. import desk as desk_module
from . import actions, chat, state, voice

STATIC_DIR = Path(__file__).with_name("static")
TOKEN_FILE = Path("ui") / "token"
PAGES = {"index.html", "app.js", "style.css"}
FONT = re.compile(r"^fonts/[a-z0-9][a-z0-9.-]*\.woff2$")
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
}
MAX_BODY = 64 * 1024
MAX_AUDIO = voice.MAX_BYTES
"""A recording is bytes, not JSON: its own ceiling, the recogniser's."""
MAX_JOBS = 20
"""Finished jobs kept in memory: enough for a page that reloads, not a history (the box has one)."""



class Busy(boxes.HarnessError):
    """Another act is on the lane: the page is told which one, and may ask again."""


@dataclass
class Job:
    """One act in flight or done: what was asked, and what the gate answered."""

    id: str
    action: str
    label: str
    started: str
    finished: str | None = None
    result: actions.Result | None = None
    error: str | None = None
    params: dict[str, str] = field(default_factory=dict)

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "label": self.label,
            "started": self.started,
            "finished": self.finished,
            "done": self.finished is not None,
            "sentence": None if self.result is None else self.result.sentence,
            "lines": [] if self.result is None else list(self.result.lines),
            "cents": None if self.result is None else self.result.cents,
            "record": None if self.result is None else self.result.record,
            "ok": None if self.result is None else self.result.ok,
            "error": self.error,
        }


def token_for(box: boxes.Box) -> str:
    """The box's own page token: made once, kept in the box, never printed by the server."""
    path = box.root / TOKEN_FILE
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value
    value = secrets.token_urlsafe(18)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)
    return value


class Workscreen(ThreadingHTTPServer):
    """A server bound to one box: the state it shows, the token it accepts, one lane for acts."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], root: Path, name: str, token: str) -> None:
        super().__init__(address, _Handler)
        self.root = root
        self.box_name = name
        self.token = token
        self.lane = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self.running: str | None = None
        self.delegated: list[str] = []

    def start(self, box: boxes.Box, action: str, params: dict[str, str]) -> Job:
        """Begin one act on the lane, or say which act already holds it."""
        if action not in actions.ACTIONS:
            raise boxes.HarnessError(
                f"no action {action!r}: the page offers " + ", ".join(actions.ACTIONS)
            )
        if not self.lane.acquire(blocking=False):
            current = self.jobs.get(self.running or "")
            raise Busy(
                "one act at a time: "
                + (current.label if current is not None else "another action")
                + " is still running; the page will say when it ends"
            )
        job = Job(
            id=secrets.token_urlsafe(9),
            action=action,
            label=_label(action, params),
            started=_stamp(),
            params=dict(params),
        )
        self.jobs[job.id] = job
        self.running = job.id
        threading.Thread(target=self._perform, args=(job, box, params), daemon=True).start()
        self._forget_old()
        return job

    def _perform(self, job: Job, box: boxes.Box, params: dict[str, str]) -> None:
        try:
            job.result = actions.run(box, job.action, params)
        except boxes.HarnessError as error:
            job.error = str(error)
        except Exception as error:  # a job must end: a page cannot be left polling forever
            job.error = f"{type(error).__name__}: {error}"
        finally:
            job.finished = _stamp()
            self.running = None
            self.lane.release()
            self._answer_desk(box, job)

    def _answer_desk(self, box: boxes.Box, job: Job) -> None:
        """A delegated run answers where the contract was written, not only on the page."""
        if job.action != "task.run":
            return
        identifier = job.params.get("task", "")
        if not identifier:
            return
        try:
            if job.result is not None:
                desk_module.record(
                    box,
                    identifier,
                    ok=job.result.ok,
                    sentence=job.result.sentence,
                    lines=job.result.lines,
                    cents=job.result.cents,
                )
            elif job.error:
                desk_module.record(
                    box, identifier, ok=False, sentence=job.error, lines=("the gate refused",)
                )
        except Exception:  # the verdict is already on the page; the file is a courtesy
            return

    def watch(self, box: boxes.Box, every: float = desk_module.SWEEP_SECONDS) -> None:
        """Read the desk from time to time: the agent's contracts must not wait for a click."""
        threading.Thread(target=self._watch, args=(box, every), daemon=True).start()

    def _watch(self, box: boxes.Box, every: float) -> None:
        while True:
            # The desk is a convenience, never the reason the page dies.
            with contextlib.suppress(Exception):
                self._sweep(box)
            time.sleep(every)

    def _sweep(self, box: boxes.Box) -> None:
        for receipt in desk_module.sweep(box):
            if receipt.ok and receipt.run and receipt.task is not None:
                self.delegated.append(receipt.id)
        while self.delegated:
            identifier = self.delegated[0]
            try:
                self.start(box, "task.run", {"task": identifier})
            except Busy:
                return  # a lane is busy: the contract waits its turn, it is not lost
            except boxes.HarnessError:
                self.delegated.pop(0)
                continue
            self.delegated.pop(0)

    def _forget_old(self) -> None:
        if len(self.jobs) <= MAX_JOBS:
            return
        done = [item.id for item in self.jobs.values() if item.finished is not None]
        for identifier in done[: len(self.jobs) - MAX_JOBS]:
            self.jobs.pop(identifier, None)


def _label(action: str, params: dict[str, str]) -> str:
    """What is running, in the words of the thing it acts on: a page shows this, not an id."""
    if action == "plan.approve":
        return "Approve plan"
    if action == "spec.accept":
        return "Accept " + params.get("package", "the package")
    if action == "step.run":
        return "Run " + params.get("step", "the step")
    if action == "task.run":
        return "Run " + params.get("task", "the task")
    return "Run " + params.get("campaign", "the campaign")


def _stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class _Handler(BaseHTTPRequestHandler):
    server: Workscreen

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/healthz":
            self._json({"ok": True, "box": self.server.box_name})
            return
        # The page and its two assets are not secret: a browser asks for them without the
        # token (a <link> cannot carry a header), and the token is what gates the data.
        if parsed.path == "/":
            self._page("index.html")
            return
        if parsed.path.startswith("/static/"):
            self._page(parsed.path.removeprefix("/static/"))
            return
        if not self._allowed(query):
            self._json({"error": "a token is required: open the URL the server printed"}, 403)
            return
        if parsed.path == "/api/boxes":
            self._json({"boxes": state.listing(self.server.root)})
            return
        if parsed.path == "/api/state":
            name = self._name(query)
            self._answer(self._guard(lambda: state.overview(self.server.root, name)))
            return
        if parsed.path == "/api/chat":
            role = (query.get("role") or ["orchestrator"])[0]
            name = self._name(query)
            self._answer(
                self._guard(lambda: {"role": role, "messages": chat.read(self._box(name), role)})
            )
            return
        if parsed.path == "/api/action":
            identifier = (query.get("id") or [""])[0]
            if identifier:
                job = self.server.jobs.get(identifier)
                if job is None:
                    self._json({"error": f"no action {identifier}"}, 404)
                    return
                self._json({"job": job.json()})
                return
            running = self.server.jobs.get(self.server.running or "")
            self._json({"job": None if running is None else running.json()})
            return
        self._json({"error": f"no route {parsed.path}"}, 404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not self._allowed(query):
            self._json({"error": "a token is required"}, 403)
            return
        if parsed.path == "/api/transcribe":
            self._transcribe()
            return
        if parsed.path not in {"/api/chat", "/api/action"}:
            self._json({"error": f"no route {parsed.path}"}, 404)
            return
        data = self._body()
        if data is None:
            return
        if parsed.path == "/api/action":
            self._act(data)
            return
        raw_role = data.get("role")
        role = raw_role if isinstance(raw_role, str) else "orchestrator"
        raw_text = data.get("text")
        text = raw_text if isinstance(raw_text, str) else ""
        raw_name = data.get("box")
        name = raw_name if isinstance(raw_name, str) else self.server.box_name
        answer = self._guard(lambda: chat.send(self._box(name), role, text))
        if "error" in answer:
            self._json(answer, 400)
            return
        self._json({"role": role, **answer})

    def _transcribe(self) -> None:
        """One recording to text: the body is the WAV, the answer is what was said."""
        length = self.headers.get("Content-Length")
        try:
            size = int(length) if length else 0
        except ValueError:
            size = 0
        if size <= 0:
            self._json({"error": "the recording is empty"}, 400)
            return
        if size > MAX_AUDIO:
            self._json({"error": "the recording is larger than 8 MB: about two minutes"}, 400)
            return
        audio = self.rfile.read(size)
        try:
            said = voice.transcribe(audio)
        except voice.Unavailable as error:
            self._json({"error": str(error)}, 503)
            return
        except boxes.HarnessError as error:
            self._json({"error": str(error)}, 400)
            return
        self._json(said.json())

    def _act(self, data: dict[str, Any]) -> None:
        """One act, through the same gate the command uses: refused with its own words."""
        raw_action = data.get("action")
        action = raw_action if isinstance(raw_action, str) else ""
        raw_params = data.get("params")
        params: dict[str, str] = {}
        if isinstance(raw_params, dict):
            for key, value in raw_params.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    self._json({"error": "action parameters are strings, one key each"}, 400)
                    return
                params[key] = value
        elif raw_params is not None:
            self._json({"error": "action parameters are strings, one key each"}, 400)
            return
        raw_name = data.get("box")
        name = raw_name if isinstance(raw_name, str) else self.server.box_name
        try:
            job = self.server.start(self._box(name), action, params)
        except Busy as error:
            running = self.server.jobs.get(self.server.running or "")
            self._json(
                {"error": str(error), "job": None if running is None else running.json()}, 409
            )
            return
        except boxes.HarnessError as error:
            self._json({"error": str(error)}, 400)
            return
        self._json({"job": job.json()}, 202)

    def _body(self) -> dict[str, Any] | None:
        """The JSON object a POST carried, or ``None`` when the answer was already a refusal."""
        length = self.headers.get("Content-Length")
        try:
            size = int(length) if length else 0
        except ValueError:
            size = 0
        if size <= 0 or size > MAX_BODY:
            self._json({"error": "the body is empty or larger than 64 KB"}, 400)
            return None
        raw = self.rfile.read(size)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json({"error": "the body is not JSON"}, 400)
            return None
        if not isinstance(data, dict):
            self._json({"error": "the body is not an object"}, 400)
            return None
        result: dict[str, Any] = data
        return result

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the terminal quiet; a page is polled, not read line by line."""

    def _name(self, query: dict[str, list[str]]) -> str:
        return (query.get("box") or [self.server.box_name])[0]

    def _box(self, name: str | None = None) -> boxes.Box:
        return boxes.find_box(self.server.root, name or self.server.box_name, Path.cwd())

    def _guard(self, call: Any) -> dict[str, Any]:
        try:
            return dict(call())
        except boxes.HarnessError as error:
            return {"error": str(error)}

    def _allowed(self, query: dict[str, list[str]]) -> bool:
        given = (query.get("t") or [""])[0] or self.headers.get("X-Pantheon-Token") or ""
        return bool(given) and given == self.server.token

    def _page(self, name: str) -> None:
        if name not in PAGES and not FONT.match(name):
            self._json({"error": f"no page {name}"}, 404)
            return
        path = STATIC_DIR / name
        try:
            body = path.read_bytes()
        except OSError:
            self._json({"error": f"the page {name} is missing from the package"}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _answer(self, payload: dict[str, Any]) -> None:
        """A refusal is a 400, not a 200 with a sad word inside: a page reads the code."""
        self._json(payload, 400 if "error" in payload else 200)

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def build(root: Path, name: str, host: str, port: int, token: str) -> Workscreen:
    """Bind the page; the caller prints the URL, so the address is never guessed."""
    return Workscreen((host, port), root, name, token)
