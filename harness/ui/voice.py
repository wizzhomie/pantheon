"""Voice input: a recording becomes text through the machine's own recogniser.

The browser records and the page cannot reach a service on this machine, so the server asks for
it: one POST to the speech-to-text server that already runs here (``docflow-voice``, port 8081,
the same one DSH's own voice input uses). The service speaks WAV — 16 kHz, mono, PCM16 — and the
page encodes exactly that, so nothing here transcodes: a format the service refuses becomes a
sentence the owner can read, not an empty answer.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .. import box as boxes

URL_ENV = "PANTHEON_STT_URL"
DEFAULT_URL = "http://127.0.0.1:8081/transcribe"
LANGUAGE = "ru"
TIMEOUT_SECONDS = 120.0
MAX_BYTES = 8 * 1024 * 1024
"""The service's own ceiling: about two minutes of speech. Refused here, before the upload."""


class Unavailable(boxes.HarnessError):
    """The recogniser is not answering: a missing service, not a bad recording."""


@dataclass(frozen=True, slots=True)
class Transcript:
    """What was said, how long the recording was, and which engine heard it."""

    text: str
    seconds: float
    engine: str

    def json(self) -> dict[str, object]:
        return {"text": self.text, "seconds": self.seconds, "engine": self.engine}


def url() -> str:
    """The recogniser's endpoint: the environment first, then the service this machine runs."""
    return os.environ.get(URL_ENV) or DEFAULT_URL


def transcribe(audio: bytes) -> Transcript:
    """Ask the recogniser for the words in one WAV recording, or say what went wrong."""
    if not audio:
        raise boxes.HarnessError(
            "the recording is empty: nothing was said, or the microphone gave silence"
        )
    if len(audio) > MAX_BYTES:
        raise boxes.HarnessError(
            f"the recording is {len(audio)} bytes; the recogniser takes at most 8 MB "
            "(about two minutes of speech)"
        )
    address = url()
    query = urllib.parse.urlencode({"language": LANGUAGE})
    request = urllib.request.Request(
        f"{address}?{query}",
        data=audio,
        method="POST",
        headers={"Content-Type": "audio/wav", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as answer:
            body = answer.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace").strip()
        try:
            said = json.loads(detail).get("error")
        except (json.JSONDecodeError, AttributeError):
            said = None
        raise boxes.HarnessError(
            f"the recogniser refused the recording ({error.code}): "
            + (str(said) if said else detail or error.reason)
        ) from error
    except OSError as error:
        raise Unavailable(
            f"the recogniser is not answering at {address}: {error}. It is the voice service on "
            "127.0.0.1:8081 (`systemctl status docflow-voice`)"
        ) from error
    return _read(body, address)


def _read(body: bytes, address: str) -> Transcript:
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise boxes.HarnessError(
            f"the recogniser at {address} answered something that is not JSON: {body[:120]!r}"
        ) from error
    if not isinstance(data, dict):
        raise boxes.HarnessError(f"the recogniser at {address} answered {type(data).__name__}")
    text = data.get("text")
    if not isinstance(text, str):
        raise boxes.HarnessError(
            f"the recogniser at {address} answered without a text: " + json.dumps(data)[:160]
        )
    seconds = data.get("audio_seconds")
    engine = data.get("engine")
    return Transcript(
        text=text.strip(),
        seconds=float(seconds) if isinstance(seconds, (int, float)) else 0.0,
        engine=engine if isinstance(engine, str) else "",
    )
