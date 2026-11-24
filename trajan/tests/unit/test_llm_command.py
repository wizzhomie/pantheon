"""The command client: a reviewer that is a program, not an endpoint.

The point of these tests is the boundary, not the transport: the request arrives as data,
the answer is read field by field, and a command that fails says so instead of returning an
empty review.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from trajan.llm import CommandClient, LLMBadResponse, LLMError, LLMRequest, LLMTimeout, Message
from trajan.llm.command import SCHEMA_VERSION, from_env


def request() -> LLMRequest:
    return LLMRequest(
        model="reviewer",
        messages=[Message(role="user", content="read the change")],
    )


def script(tmp_path: Path, body: str) -> list[str]:
    """A command as the client will run it: this interpreter, then a one-off program."""
    path = tmp_path / "command.py"
    path.write_text("import json, sys\n" + body, encoding="utf-8")
    return [sys.executable, str(path)]


async def test_the_request_arrives_as_data_and_the_answer_is_normalised(tmp_path: Path) -> None:
    command = script(
        tmp_path,
        f"""
raw = json.loads(sys.stdin.read() or "{{}}")
open({str(tmp_path / "seen.json")!r}, "w").write(json.dumps(raw))
print(json.dumps({{"text": '{{"summary": "ok", "findings": []}}',
                  "model": "hermes", "prompt_tokens": 1200,
                  "completion_tokens": 40, "cost_cents": 0.4}}))
""",
    )
    response = await CommandClient(command).complete(request())

    assert response.text == '{"summary": "ok", "findings": []}'
    assert response.model == "hermes"
    assert (response.prompt_tokens, response.completion_tokens) == (1200, 40)
    seen = json.loads((tmp_path / "seen.json").read_text(encoding="utf-8"))
    assert seen["schema_version"] == SCHEMA_VERSION
    assert seen["model"] == "reviewer"
    assert seen["messages"] == [{"role": "user", "content": "read the change"}]
    assert "cost_cents" not in seen, "the bill belongs to the caller, not to the request"


async def test_a_command_that_fails_is_an_error_with_its_own_words(tmp_path: Path) -> None:
    command = script(
        tmp_path,
        """
print("the box is down", file=sys.stderr)
sys.exit(3)
""",
    )
    with pytest.raises(LLMError, match="the box is down"):
        await CommandClient(command).complete(request())


async def test_an_answer_that_is_not_json_is_refused(tmp_path: Path) -> None:
    command = script(tmp_path, "print('I think the change looks fine')\n")
    with pytest.raises(LLMBadResponse, match="no JSON"):
        await CommandClient(command).complete(request())


async def test_an_empty_answer_is_refused(tmp_path: Path) -> None:
    """A command that exits 0 with nothing to say produced no review, not an empty one."""
    command = script(tmp_path, 'print(json.dumps({"text": "  "}))\n')
    with pytest.raises(LLMBadResponse, match="no text"):
        await CommandClient(command).complete(request())


async def test_an_error_in_the_answer_is_an_error(tmp_path: Path) -> None:
    command = script(tmp_path, 'print(json.dumps({"error": "the agent timed out"}))\n')
    with pytest.raises(LLMError, match="the agent timed out"):
        await CommandClient(command).complete(request())


async def test_a_command_that_hangs_is_stopped(tmp_path: Path) -> None:
    command = script(tmp_path, "import time\ntime.sleep(5)\n")
    with pytest.raises(LLMTimeout):
        await CommandClient(command, timeout_s=0.2).complete(request())


async def test_a_command_that_is_not_there_says_so(tmp_path: Path) -> None:
    with pytest.raises(LLMError, match="could not run"):
        await CommandClient([str(tmp_path / "missing")]).complete(request())


async def test_a_finish_reason_nobody_defined_is_unknown(tmp_path: Path) -> None:
    command = script(tmp_path, 'print(json.dumps({"text": "{}", "finish_reason": "weird"}))\n')
    response = await CommandClient(command).complete(request())
    assert response.finish_reason == "unknown"


async def test_a_missing_bill_is_not_a_zero_bill(tmp_path: Path) -> None:
    """No usage in the answer: the client leaves zero, and the caller records "unknown"."""
    command = script(tmp_path, 'print(json.dumps({"text": "{}"}))\n')
    response = await CommandClient(command).complete(request())
    assert (response.prompt_tokens, response.completion_tokens) == (0, 0)


def test_the_factory_needs_a_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRAJAN_REVIEW_COMMAND", raising=False)
    with pytest.raises(ValueError, match="TRAJAN_REVIEW_COMMAND"):
        from_env()


def test_the_factory_splits_a_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRAJAN_REVIEW_COMMAND", "bin/pantheon-review --box /tmp/box")
    monkeypatch.setenv("TRAJAN_REVIEW_COMMAND_TIMEOUT", "30")
    client = from_env()
    assert client.command == ["bin/pantheon-review", "--box", "/tmp/box"]
    assert client.timeout_s == 30.0


def test_the_factory_rejects_a_timeout_that_is_not_a_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRAJAN_REVIEW_COMMAND", "true")
    monkeypatch.setenv("TRAJAN_REVIEW_COMMAND_TIMEOUT", "soon")
    with pytest.raises(ValueError, match="not a number"):
        from_env()
