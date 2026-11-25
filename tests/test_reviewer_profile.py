"""The reader a box declares: `profile/reviewer.toml`, and the three rules it keeps.

The file is the box's own decision, not a default: absent means nothing is read unless asked,
`enabled = false` means the box thought about it and said no, and a flag wins over both for one
run. These tests pin the parsing, the refusal of a file it cannot understand, and the fact that
`new` hands a person an example that is switched off.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness import box as boxes
from harness import reviewer
from harness.cli import main


def write(root: Path, text: str) -> Path:
    path = root / "profile" / "reviewer.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_an_absent_file_is_a_box_that_reads_nothing(tmp_path: Path) -> None:
    config = reviewer.read_config(tmp_path / "profile" / "reviewer.toml")
    assert config.declared is False and config.enabled is False
    assert "off" in reviewer.describe(config)


def test_enabled_model_and_ceiling_are_read(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        'schema_version = 1\nenabled = true\nmodel = "deepseek-chat"\nmax_cents = 2.5\n',
    )
    config = reviewer.read_config(path)
    assert (config.declared, config.enabled, config.model, config.max_cents) == (
        True,
        True,
        "deepseek-chat",
        2.5,
    )


def test_a_declared_reader_that_is_off_is_said_differently_from_an_absent_one(
    tmp_path: Path,
) -> None:
    off = reviewer.read_config(write(tmp_path, "schema_version = 1\nenabled = false\n"))
    assert "switched off" in reviewer.describe(off)
    assert "switched off" not in reviewer.describe(
        reviewer.read_config(tmp_path / "nothing" / "reviewer.toml")
    )


def test_the_ceiling_is_named_when_it_is_declared(tmp_path: Path) -> None:
    config = reviewer.read_config(
        write(tmp_path, "schema_version = 1\nenabled = true\nmax_cents = 1\n")
    )
    assert "up to 1 cents" in reviewer.describe(config)
    bare = reviewer.read_config(write(tmp_path, "schema_version = 1\nenabled = true\n"))
    assert "no declared ceiling" in reviewer.describe(bare)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("schema_version = 1\nenabled = true\ncolour = \"red\"\n", "unknown key"),
        ("schema_version = 2\nenabled = true\n", "schema_version"),
        ("schema_version = 1\nenabled = \"yes\"\n", "true or false"),
        ("schema_version = 1\nenabled = true\nmodel = \"\"\n", "model"),
        ("schema_version = 1\nenabled = true\nmax_cents = 0\n", "positive number"),
        ("schema_version = 1\nenabled = true\nmax_cents = true\n", "positive number"),
    ],
)
def test_a_file_that_cannot_be_understood_is_refused(
    tmp_path: Path, text: str, message: str
) -> None:
    with pytest.raises(boxes.HarnessError, match=message):
        reviewer.read_config(write(tmp_path, text))


def test_new_hands_a_person_an_example_that_is_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "probe"
    project.mkdir()
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text('name = "probe"\n', encoding="utf-8")
    code = main(["--boxes", str(tmp_path / "boxes"), "new", str(project), "--name", "probe"])
    out = capsys.readouterr().out
    assert code == 0
    example = tmp_path / "boxes" / "probe" / reviewer.EXAMPLE_FILE
    assert example.is_file()
    assert "enabled = true" in example.read_text(encoding="utf-8")
    assert "reader   off" in out


def test_status_says_who_reads_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "probe"
    (project / ".trajan").mkdir(parents=True)
    (project / ".trajan" / "policy.toml").write_text('name = "probe"\n', encoding="utf-8")
    box = boxes.new_box(tmp_path / "boxes", project)
    assert main(["--boxes", str(box.root.parent), "status", box.name]) == 0
    assert "reviewer   off" in capsys.readouterr().out
    write(box.root, 'schema_version = 1\nenabled = true\nmodel = "deepseek-chat"\n')
    assert main(["--boxes", str(box.root.parent), "status", box.name]) == 0
    assert "read by deepseek-chat" in capsys.readouterr().out
    write(
        box.root,
        'schema_version = 1\nenabled = true\nmodel = "deepseek-chat"\n'
        'when_paths = ["src/**"]\n',
    )
    assert main(["--boxes", str(box.root.parent), "status", box.name]) == 0
    out = capsys.readouterr().out
    assert "changes touching src/** are read" in out
    assert "other changes are not read" in out


def test_declared_paths_narrow_the_reading_and_are_described(tmp_path: Path) -> None:
    config = reviewer.read_config(
        write(
            tmp_path,
            'schema_version = 1\nenabled = true\nmodel = "m"\nmax_cents = 1\n'
            'when_paths = ["src/**", "tests/**"]\n',
        )
    )
    assert config.when_paths == ("src/**", "tests/**")
    line = reviewer.describe(config)
    assert "changes touching src/**, tests/** are read" in line
    assert "other changes are not read" in line


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("schema_version = 1\nenabled = true\nwhen_paths = \"src/**\"\n", "list of glob"),
        ("schema_version = 1\nenabled = true\nwhen_paths = [\"\"]\n", "relative non-empty"),
        ("schema_version = 1\nenabled = true\nwhen_paths = []\n", "when_paths is empty"),
        ("schema_version = 1\nenabled = true\nwhen_paths = [\"/etc/**\"]\n", "relative non-empty"),
        ("schema_version = 1\nenabled = true\nwhen_paths = [\"../x\"]\n", "relative non-empty"),
    ],
)
def test_declared_paths_that_cannot_be_understood_are_refused(
    tmp_path: Path, text: str, message: str
) -> None:
    with pytest.raises(boxes.HarnessError, match=message):
        reviewer.read_config(write(tmp_path, text))


def test_the_reader_declines_a_change_outside_its_declared_paths() -> None:
    from harness import contour

    reader = contour.Reviewer(spec="x", env={}, when_paths=("src/**",))
    assert reader.declines(("docs/note.md",)) is not None
    assert "declared: src/**" in str(reader.declines(("docs/note.md",)))
    assert reader.declines(("src/app.py",)) is None
    assert contour.Reviewer(spec="x", env={}).declines(("docs/note.md",)) is None
    unknown = contour.Reviewer(spec="x", env={}, when_paths=("src/**",))
    assert unknown.declines(()) is None, "a change with no readable paths is read, not skipped"


def test_the_review_flag_ignores_the_declared_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from argparse import Namespace

    from harness import cli, contour

    project = tmp_path / "probe"
    project.mkdir()
    box = boxes.new_box(tmp_path / "boxes", project)
    write(box.root, 'schema_version = 1\nenabled = true\nwhen_paths = ["src/**"]\n')
    def build(
        _root: Path,
        *,
        model: str | None = None,
        seconds: int | None = None,
        max_cents: float | None = None,
        when_paths: tuple[str, ...] = (),
    ) -> contour.Reviewer:
        return contour.Reviewer(spec="x", env={}, when_paths=when_paths)

    monkeypatch.setattr(contour, "box_reviewer", build)
    plain = Namespace(review=False, no_review=False, review_model=None, review_seconds=None)
    asked = Namespace(review=True, no_review=False, review_model=None, review_seconds=None)
    plain_reader = cli._reviewer(plain, box)
    asked_reader = cli._reviewer(asked, box)
    assert plain_reader is not None and plain_reader.when_paths == ("src/**",)
    assert asked_reader is not None and asked_reader.when_paths == ()
