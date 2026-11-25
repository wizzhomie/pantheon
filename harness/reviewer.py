"""What the box declared about reading its changes: ``persona/reviewer.toml``.

The executor is a command the box names, because producing a change is the box's own business.
Reading a change is the same kind of decision: a box may declare "my changes are read", name the
model that reads them and the ceiling it may spend — and then every dispatch reads the change
without anyone remembering a flag.

Three rules keep this from becoming quiet spending:

* **off until declared.** Without the file, a dispatch is checks-only and says so; `pantheon
  new` writes an example that is switched off, so turning it on is one deliberate line.
* **the ceiling is declared, not guessed.** If the reader costs more than `max_cents`, the run
  is recorded and the gap is named — the call happened, so pretending it did not is worse.
* **a flag wins over the file.** ``--review`` reads once, ``--no-review`` refuses for one run;
  the file says what usually happens, the flag says what happens now.
* **a declared path is a declared skip.** ``when_paths`` narrows reading to the changes whose
  paths match; a change it does not match is not read, and the record says so with the reason —
  a saving that looked like a reading would be the quietest kind of gap.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import box as boxes

SCHEMA_VERSION = 1
PROFILE_FILE = Path("persona") / "reviewer.toml"
EXAMPLE_FILE = Path("persona") / "reviewer.toml.example"
CONFIG_KEYS = frozenset({"schema_version", "enabled", "model", "max_cents", "when_paths"})

EXAMPLE = """\
# To make every dispatch read the change it produced, copy this file to
# `persona/reviewer.toml` and set `enabled = true`.
#
# Reading is one paid call to the box's own agent, the same container the executor uses.
# The report, the record and the outbox show the model that read the change and what it
# cost; the reading's findings are anchored against the repository by Trajan, so a claim
# that does not point at a line does not count.
#
# schema_version = 1
# enabled = true
# model = "deepseek-flash"
# max_cents = 1
# Read only changes that touch these paths; leave the key out to read every change.
# A change outside the list is not read, and the record and outbox say so.
# when_paths = ["src/**", "tests/**"]
"""


@dataclass(frozen=True, slots=True)
class Config:
    """The box's declared reader: whether it reads, with which model, up to how much."""

    path: Path
    enabled: bool = False
    model: str | None = None
    max_cents: float | None = None
    when_paths: tuple[str, ...] = ()
    """The paths whose changes are read. Empty means "every change", not "no change"."""

    @property
    def declared(self) -> bool:
        """Whether the file exists at all: absent and "enabled = false" are said differently."""
        return self.path.is_file()


def read_config(path: Path) -> Config:
    """The box's reader profile; absent means the box reads nothing unless asked."""
    if not path.is_file():
        return Config(path=path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"reviewer profile {path} does not parse: {error}") from error
    unknown = set(data) - CONFIG_KEYS
    if unknown:
        raise boxes.HarnessError(f"{path}: unknown key(s) {', '.join(sorted(unknown))}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(
            f"{path}: schema_version {data.get('schema_version')!r} is not {SCHEMA_VERSION}"
        )
    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise boxes.HarnessError(f"{path}: enabled must be true or false")
    model = data.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise boxes.HarnessError(f"{path}: model must be a non-empty string")
    cents = data.get("max_cents")
    if cents is not None:
        if isinstance(cents, bool) or not isinstance(cents, (int, float)) or cents <= 0:
            raise boxes.HarnessError(f"{path}: max_cents must be a positive number")
        cents = float(cents)
    when_paths = _paths(path, data["when_paths"]) if "when_paths" in data else ()
    return Config(
        path=path, enabled=enabled, model=model, max_cents=cents, when_paths=when_paths
    )


def _paths(path: Path, raw: object) -> tuple[str, ...]:
    """The declared paths, checked for shape: an empty or absolute pattern reads nothing."""
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise boxes.HarnessError(f"{path}: when_paths must be a list of glob strings")
    if isinstance(raw, list) and not raw:
        raise boxes.HarnessError(
            f"{path}: when_paths is empty: name at least one path, or leave the key out to "
            "read every change"
        )
    for item in raw:
        if not item.strip() or item.startswith("/") or ".." in item.split("/"):
            raise boxes.HarnessError(
                f"{path}: when_paths entry {item!r} must be a relative non-empty glob"
            )
    return tuple(item.strip() for item in raw)


def describe(config: Config) -> str:
    """The reader as a person reads it in `status`: what happens without a flag."""
    if not config.declared:
        return "off — `--review` reads a change once; nothing is read by default"
    if not config.enabled:
        return "declared, switched off — `--review` reads a change once"
    model = config.model or "the box's default model"
    if config.when_paths:
        subject = f"changes touching {', '.join(config.when_paths)} are"
        tail = "; other changes are not read"
    else:
        subject = "every dispatch is"
        tail = ""
    if config.max_cents is None:
        return f"on — {subject} read by {model}, no declared ceiling{tail}"
    return f"on — {subject} read by {model}, up to {config.max_cents:g} cents{tail}"
