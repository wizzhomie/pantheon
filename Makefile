# The example is the documentation, so these targets are what a reader actually runs.
#
#   make check    lint + types + tests (what CI runs)
#   make demo     the demo agent through the CLI: no key, no network, no money
#   make sample   regenerate the committed stub reports
#   make replay   the committed live cassette: same numbers, no key, no network
#   make eval     a live model run (needs DEEPSEEK_API_KEY); baseline + diff included
#
# The library is not pip-installed in this checkout, hence PYTHONPATH=src. The `.` is what
# makes `examples.*` importable, exactly as `python -m` does for the CLI.

PYTHON ?= .venv/bin/python
export PYTHONPATH := src

DATASET := examples/logistics_intake/dataset.jsonl
SPEC := examples.logistics_intake.runner:build_guarded

.DEFAULT_GOAL := check

.PHONY: check lint typecheck test demo sample replay eval baseline diff

check: lint typecheck test

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

demo:
	$(PYTHON) -m qagent eval --spec $(SPEC) --dataset $(DATASET) \
		--out reports/demo_guarded.json --markdown reports/demo_guarded.md

sample:
	$(PYTHON) -m examples.logistics_intake.sample

replay:
	QAGENT_LLM=replay $(PYTHON) -m qagent eval --spec $(SPEC) --dataset $(DATASET) \
		--out reports/replay_guarded.json --markdown reports/replay_guarded.md

eval:
	QAGENT_LLM=live $(PYTHON) -m qagent eval --spec $(SPEC) --dataset $(DATASET) \
		--out reports/guarded.json --markdown reports/guarded.md --concurrency 3

baseline:
	QAGENT_LLM=live $(PYTHON) -m qagent eval --spec $(SPEC) --dataset $(DATASET) \
		--mode baseline --out reports/baseline.json \
		--markdown reports/baseline.md --concurrency 3

diff:
	$(PYTHON) -m qagent compare reports/baseline.json reports/guarded.json \
		--markdown reports/diff.md
