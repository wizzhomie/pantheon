"""Shared test setup: no test may touch the real Docker daemon or the real boxes directory."""

from __future__ import annotations

from pathlib import Path

import pytest

FAKE_DOCKER = '''\
#!/usr/bin/env python3
"""A docker that records its arguments and answers like compose does, without containers."""

import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as log:
    log.write(" ".join(args) + "\\n")
if args[:1] == ["version"]:
    print("29.0.0")
    sys.exit(0)
if "ps" in args:
    running = os.environ.get("FAKE_DOCKER_RUNNING", "0") == "1"
    rows = (
        [{"Name": "pantheon-probe_augustus_1", "State": "running", "Status": "Up 2s"}]
        if running
        else []
    )
    print(json.dumps(rows))
    sys.exit(0)
if os.environ.get("FAKE_DOCKER_FAIL") == "1":
    print("compose failed on purpose", file=sys.stderr)
    sys.exit(1)
if "exec" in args:
    joined = " ".join(args)
    # The test says which tool the image lacks, and whether installing it may fail.
    missing = os.environ.get("FAKE_DOCKER_MISSING", "")
    if missing and f"command -v {missing}" in joined:
        sys.exit(1)
    if os.environ.get("FAKE_DOCKER_INSTALL_FAIL") == "1" and "apt-get install" in joined:
        print("E: Unable to locate package", file=sys.stderr)
        sys.exit(100)
    # `docker compose exec ...`: the test says what the agent would answer.
    print(os.environ.get("FAKE_DOCKER_AGENT", ""))
    sys.exit(0)
sys.exit(0)
'''


@pytest.fixture(autouse=True)
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets a docker that cannot start anything, and a log of what was asked."""
    script = tmp_path / "fake-docker"
    script.write_text(FAKE_DOCKER, encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PANTHEON_DOCKER", str(script))
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(tmp_path / "docker.log"))
    monkeypatch.delenv("FAKE_DOCKER_RUNNING", raising=False)
    monkeypatch.delenv("FAKE_DOCKER_FAIL", raising=False)
    monkeypatch.delenv("FAKE_DOCKER_MISSING", raising=False)
    monkeypatch.delenv("FAKE_DOCKER_INSTALL_FAIL", raising=False)
    return script
