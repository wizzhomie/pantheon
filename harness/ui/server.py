"""The workscreen's HTTP face: one local page, a small JSON API, and no writes into the project.

The server answers only for the box it was started with, and only to a caller that presents the
box's token: the page can spend money on a conversation, so "anyone who reaches the port" is not
the same as "the owner". The token is a file in the box's own volume, mode 600, never a default.

There is no upload, no form post and no route that changes a project: the page asks, reads and
talks. Anything that would write arrives as a task on the box, where the gates already are.
"""

from __future__ import annotations

import json
import re
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import box as boxes
from . import chat, state

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
    """A server bound to one box: the state it shows and the token it accepts."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], root: Path, name: str, token: str) -> None:
        super().__init__(address, _Handler)
        self.root = root
        self.box_name = name
        self.token = token


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
        self._json({"error": f"no route {parsed.path}"}, 404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not self._allowed(query):
            self._json({"error": "a token is required"}, 403)
            return
        if parsed.path != "/api/chat":
            self._json({"error": f"no route {parsed.path}"}, 404)
            return
        length = self.headers.get("Content-Length")
        try:
            size = int(length) if length else 0
        except ValueError:
            size = 0
        if size <= 0 or size > MAX_BODY:
            self._json({"error": "the body is empty or larger than 64 KB"}, 400)
            return
        raw = self.rfile.read(size)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json({"error": "the body is not JSON"}, 400)
            return
        if not isinstance(data, dict):
            self._json({"error": "the body is not an object"}, 400)
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
