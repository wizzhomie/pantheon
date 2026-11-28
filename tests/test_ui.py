"""The workscreen: what it reads, what it refuses, and what it must never print.

The page is a reading instrument, so these tests are about honesty rather than layout: the
state is composed from the box, the token is the only way in, and the model key never reaches
an answer.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from harness import box as boxes
from harness.ui import chat, server, state


def make_box(tmp_path: Path) -> boxes.Box:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("demo\n", encoding="utf-8")
    return boxes.new_box(tmp_path / "castra", project)


def test_overview_describes_a_fresh_box(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    data = state.overview(tmp_path / "castra", box.name)
    assert data["box"]["name"] == box.name
    assert data["frame"]["state"] == "empty"
    assert data["rails"]["declared"] is False
    assert data["campaigns"] == []
    assert data["tasks"] == []
    assert data["spend"] == {"live_cents": 0.0, "campaigns": 0}


def test_overview_never_prints_the_model_key(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    (box.root / "cella").mkdir(parents=True, exist_ok=True)
    secret = "sk-test-should-never-be-printed"
    (box.root / "cella" / ".env").write_text(f"DEEPSEEK_API_KEY={secret}\n", encoding="utf-8")
    rendered = json.dumps(state.overview(tmp_path / "castra", box.name))
    assert secret not in rendered


def test_chat_records_both_sides_and_the_price(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = make_box(tmp_path)

    def fake(box: boxes.Box, role: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        assert role == "orchestrator"
        return {
            "answer": "Следующий шаг — принять план.",
            "cents": 0.42,
            "tokens": {"prompt": 120, "completion": 30},
            "model": "deepseek-chat",
        }

    monkeypatch.setattr(chat, "_ask", fake)
    answer = chat.send(box, "orchestrator", "Что дальше?")
    assert answer["cents"] == 0.42
    messages = chat.read(box, "orchestrator")
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[1]["text"] == "Следующий шаг — принять план."
    assert messages[1]["cents"] == 0.42


def test_chat_keeps_the_newest_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    box = make_box(tmp_path)

    def fake(box: boxes.Box, role: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {"answer": "ok", "cents": None, "tokens": {}, "model": "m"}

    monkeypatch.setattr(chat, "_ask", fake)
    path = box.root / chat.CHAT_DIR / "architect.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = [
        {"at": "2026-01-01T00:00:00Z", "role": "user", "text": f"старое {index}"}
        for index in range(chat.MAX_MESSAGES + 20)
    ]
    path.write_text(json.dumps(old), encoding="utf-8")
    chat.send(box, "architect", "новое")
    messages = chat.read(box, "architect")
    assert len(messages) == chat.MAX_MESSAGES
    assert messages[-1]["text"] == "ok"


def test_chat_refuses_an_unknown_role(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    with pytest.raises(boxes.HarnessError):
        chat.read(box, "manager")


def test_cost_is_named_only_when_both_numbers_are_there() -> None:
    assert chat._cost(1_000, 500, 0.15, 0.60) == 0.045
    assert chat._cost(1_000, 500, None, 0.60) is None
    assert chat._cost(None, 500, 0.15, 0.60) is None


def test_token_is_stored_in_the_box_once(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    first = server.token_for(box)
    second = server.token_for(box)
    assert first == second
    assert len(first) >= 20
    path = box.root / server.TOKEN_FILE
    assert path.read_text(encoding="utf-8").strip() == first
    assert path.stat().st_mode & 0o777 == 0o600


class Screen:
    def __init__(self, tmp_path: Path) -> None:
        self.box = make_box(tmp_path)
        self.token = server.token_for(self.box)
        self.server = server.build(tmp_path / "castra", self.box.name, "127.0.0.1", 0, self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def get(self, path: str, token: str | None = None) -> tuple[int, bytes]:
        headers = {} if token is None else {"X-Pantheon-Token": token}
        request = urllib.request.Request(self.base + path, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as answer:
                return answer.status, answer.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def post(self, path: str, body: object, token: str | None = None) -> tuple[int, bytes]:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Pantheon-Token"] = token
        request = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as answer:
                return answer.status, answer.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()


def test_server_asks_for_the_token(tmp_path: Path) -> None:
    screen = Screen(tmp_path)
    try:
        code, _ = screen.get("/api/state")
        assert code == 403
        code, body = screen.get("/api/state", screen.token)
        assert code == 200
        assert json.loads(body)["box"]["name"] == screen.box.name
        code, page = screen.get("/", screen.token)
        assert code == 200
        assert b"Pantheon" in page
        code, css = screen.get("/static/style.css")
        assert code == 200
        assert b"--panel" in css
        code, script = screen.get("/static/app.js")
        assert code == 200
        assert b"loadState" in script
        code, font = screen.get("/static/fonts/cinzel-600-latin.woff2")
        assert code == 200
        assert font[:4] == b"wOF2"
        code, _ = screen.get("/static/fonts/../app.js")
        assert code == 404
    finally:
        screen.close()


def test_server_refuses_an_unknown_box(tmp_path: Path) -> None:
    screen = Screen(tmp_path)
    try:
        code, body = screen.get("/api/state?box=nope", screen.token)
        assert code == 400
        assert "error" in json.loads(body)
    finally:
        screen.close()


def test_server_records_a_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(box: boxes.Box, role: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "answer": "План ещё не принят.",
            "cents": 0.11,
            "tokens": {"prompt": 3, "completion": 2},
            "model": "deepseek-chat",
        }

    monkeypatch.setattr(chat, "_ask", fake)
    screen = Screen(tmp_path)
    try:
        code, body = screen.post(
            "/api/chat", {"role": "architect", "text": "Что с планом?"}, screen.token
        )
        assert code == 200
        answer = json.loads(body)
        assert answer["answer"] == "План ещё не принят."
        assert answer["cents"] == 0.11
        code, body = screen.get("/api/chat?role=architect", screen.token)
        assert code == 200
        messages = json.loads(body)["messages"]
        assert [item["role"] for item in messages] == ["user", "assistant"]
    finally:
        screen.close()


def test_server_refuses_a_chat_without_the_token(tmp_path: Path) -> None:
    screen = Screen(tmp_path)
    try:
        code, _ = screen.post("/api/chat", {"role": "architect", "text": "привет"})
        assert code == 403
    finally:
        screen.close()


def test_chat_speaks_to_the_declared_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = make_box(tmp_path)
    seen: dict[str, Any] = {}

    class Stub(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            seen["body"] = json.loads(self.rfile.read(length))
            seen["auth"] = self.headers["Authorization"]
            body = json.dumps(
                {
                    "choices": [{"message": {"content": "привет"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("PANTHEON_MODEL_URL", f"http://127.0.0.1:{stub.server_address[1]}/chat")
        monkeypatch.setenv("PANTHEON_MODEL_KEY", "sk-stub")
        monkeypatch.setenv("PANTHEON_MODEL", "stub-model")
        monkeypatch.setenv("PANTHEON_MODEL_PRICE_IN", "0.15")
        monkeypatch.setenv("PANTHEON_MODEL_PRICE_OUT", "0.60")
        answer = chat.send(box, "architect", "что дальше?")
    finally:
        stub.shutdown()
        stub.server_close()
    assert answer["answer"] == "привет"
    assert answer["cents"] == 0.0027
    assert seen["body"]["model"] == "stub-model"
    assert seen["auth"] == "Bearer sk-stub"
    assert seen["body"]["messages"][0]["role"] == "system"
    assert seen["body"]["messages"][-1] == {"role": "user", "content": "что дальше?"}
