"""Build the two requests for the context-block A/B: same task, only `context` differs."""
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/pantheon")
from harness import box as boxes
from harness import context as context_pack
from harness import executor as execmod
from harness.augustus import Contract


@dataclass
class Task:
    id: str
    goal: str
    kind: str
    role: str
    paths: tuple[str, ...]
    expected: str
    self_check: tuple[str, ...]
    forbidden: tuple[str, ...]
    budget: dict
    max_steps: int | None
    shape: str | None
    contract: object


def check(env: str, expect: str) -> tuple[str, ...]:
    code = (
        "import os\nimport subprocess\nimport sys\n\n"
        "code = \"import click\\nclick.secho('ok', fg='green')\"\n"
        f"env = dict(os.environ, {env})\n"
        "out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env).stdout\n"
        f"{expect}\n"
    )
    return ("python", "-c", code)


task = Task(
    id="t-20261126-ctx-ab",
    goal=(
        "`click.echo`/`secho` теперь смотрят на `FORCE_COLOR`/`NO_COLOR` из окружения, и в CI, где "
        "`NO_COLOR=1` выставлен глобально, демо на интерактивном терминале потеряло цвет. Верни "
        "прежнее поведение: цвет решает только поток, переменные окружения на `echo`/`secho` не "
        "влияют. Правь `src/click/**`, ничего другого не меняй."
    ),
    kind="bugfix",
    role="executor",
    paths=("src/click/**",),
    expected=(
        "при `FORCE_COLOR=1` и потоке-трубе ANSI-код не печатается; явный `color=True` по-прежнему "
        "печатает цвет в трубе; решение о цвете принимается по потоку, как до правки."
    ),
    self_check=(check("NO_COLOR='1'", "assert '\\x1b[32m' in out, repr(out)"),),
    forbidden=("менять тесты",),
    budget={"max_cents": 6},
    max_steps=None,
    shape="file",
    contract=Contract(
        symptom="в CI `NO_COLOR=1` стоит глобально, и интерактивное демо печатает без цвета.",
        visible="`click.echo(click.style('hi', fg='red'))` при `NO_COLOR=1` и явном `color=True` печатает `\\x1b[31m`.",
        boundaries="только решение о цвете в `src/click/**`; стиль, форматирование и тесты не меняются.",
        missing=None,
    ),
)
box = boxes.find_box(Path.home() / "pantheon" / "castra", "click", Path("/home/ubuntu"))
pack = context_pack.build(box, task.id, task.paths)
directory = box.root / "tabularium" / task.id
directory.mkdir(parents=True, exist_ok=True)
common = dict(task=task, box=box, directory=directory, number=1, model="deepseek-flash",
              seconds=300, cents=6)
with_pack = execmod._request(**common, context=context_pack.payload(pack))
without = execmod._request(**common, context=None)
Path("/tmp/ctx-ab/request-a.json").write_text(json.dumps(with_pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
Path("/tmp/ctx-ab/request-b.json").write_text(json.dumps(without, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
block = sum(len(line) + 1 for line in pack.lines)
print("selected:", [item.entry.task for item in pack.selected])
print("line:", context_pack.line(pack))
for line in pack.lines:
    print("  ", line[:180])
print("block chars in the prompt:", block)
