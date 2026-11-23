# Code review on seeded bugs — the second example, written as config.

A reviewer model gets a small Python function and must point at the defects: line number,
verbatim quote, one sentence. The bugs are planted on purpose, so the correct answer is
known before the model runs and costs nothing.

Two things make this example different from the logistics one:

* the rule "correct" is not a field comparison — it is "found every planted bug and
  invented nothing", expressed as a function named in `[scoring] custom`;
* the checks are deterministic and do the heavy lifting: a quote that is not in the snippet,
  or a line that does not exist, is caught for free before any judge is asked.

## The cartridge, in five files

| File | What it is | What it shows |
|---|---|---|
| [`schema.py`](schema.py) | `Finding` and `Review` | a finding anchored to a line and a quote |
| [`checks.py`](checks.py) | `C1`–`C4` | quote exists, line exists, quote is on that line, verdict agrees |
| [`scoring.py`](scoring.py) | recall plus a spurious-finding veto | whole-answer scoring through `[scoring] custom` |
| [`fixtures.py`](fixtures.py) | 24 snippets, 18 with planted bugs | bugs are marked in source and verified by a probe |
| [`trajan.toml`](trajan.toml) | the whole preset, as data | the config path this library is built around |

Supporting files: [`build_dataset.py`](build_dataset.py) turns the fixtures into the committed
`dataset.jsonl` and refuses to write a fixture whose bug is not observable; [`demo.py`](demo.py)
is the deterministic stand-in used for the stub reports; [`runner.py`](runner.py) holds the two
factories the config names; [`agent.py`](agent.py) is the whole model integration.

## Run it

```bash
make check                                     # lint, types, tests (offline)
trajan check --config examples/code_review/trajan.toml
TRAJAN_LLM=replay trajan eval --config examples/code_review/trajan.toml \
    --out reports/code_review/replay.json --markdown reports/code_review/replay.md
TRAJAN_LLM=live trajan eval --config examples/code_review/trajan.toml \
    --out reports/code_review/guarded.json --markdown reports/code_review/guarded.md
```

## Why the bugs are planted

A review has no single correct text: two good reviewers disagree on wording, and human
labelling is slow and expensive. A planted bug is different — the line is known, the correct
behaviour is known, and a probe proves the bug changes what the function returns. That makes
"found it / missed it / invented one" measurable without a human in the loop, and it is the
only reason a report here can claim a real pass rate.

The honest limit is in the name: this measures **bug detection**, not review quality. Style,
architecture and taste are not scored, and a model that flags every line would fail on
spurious findings rather than look thorough.
