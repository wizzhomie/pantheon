# The example is the documentation, so these targets are what a reader actually runs.
#
#   make check    lint + types + tests (what CI runs)
#   make demo     the demo agent through the CLI: no key, no network, no money
#   make sample   regenerate the committed stub reports
#   make replay   the committed live cassette: same numbers, no key, no network
#   make config-check  validate the qagent.toml description of the same preset
#   make verify   run this project's checks and write .qagent/runs/<id>/report.{json,md}
#   make verify-demo  the HOLD demo: a planted defect and the report that refuses it
#   make plan-demo  the same HOLD demo with a change: a path-scoped gate is not selected
#   make learn    mine this project's recorded runs for a rule; decides nothing
#   make evolve-demo  the rule mined from the evolution demo's three recorded runs
#   make eval     a live model run (needs DEEPSEEK_API_KEY); baseline + diff included
#
# The library is not pip-installed in this checkout, hence PYTHONPATH=src. The `.` is what
# makes `examples.*` importable, exactly as `python -m` does for the CLI.

PYTHON ?= .venv/bin/python
export PYTHONPATH := src

DATASET := examples/logistics_intake/dataset.jsonl
CONFIG := examples/logistics_intake/qagent.toml
SPEC := examples.logistics_intake.runner:build_guarded

.DEFAULT_GOAL := check

.PHONY: check lint typecheck test demo sample replay eval baseline diff config-check verify verify-demo plan-demo learn evolve-demo

check: lint typecheck test

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

config-check:
	$(PYTHON) -m qagent check --config $(CONFIG)
	$(PYTHON) -m qagent check --config examples/code_review/qagent.toml

verify:
	PATH="$(CURDIR)/.venv/bin:$$PATH" $(PYTHON) -m qagent verify --repo . \
		--policy .qagent/policy.toml

verify-demo:
	PATH="$(CURDIR)/.venv/bin:$$PATH" $(PYTHON) -m qagent verify \
		--repo examples/verification_demo \
		--policy policy.toml \
		--out reports/verification_demo || test $$? -eq 1  # HOLD is the expected result here

plan-demo:
	QAGENT_REVIEW_RESPONSE='{"findings": []}' PATH="$(CURDIR)/.venv/bin:$$PATH" \
		$(PYTHON) -m qagent verify --repo examples/verification_demo \
		--policy policy.toml --diff change.diff \
		--reviewer qagent.review.scripted:from_env \
		--review-model "scripted (no model call)" \
		--out reports/verification_demo_plan || test $$? -eq 1

learn:
	$(PYTHON) -m qagent learn --repo .

evolve-demo:
	$(PYTHON) -m qagent learn --repo examples/evolve_demo \
		--policy examples/evolve_demo/policy.toml \
		--history examples/evolve_demo/history

demo:
	$(PYTHON) -m qagent eval --spec $(SPEC) --dataset $(DATASET) \
		--out reports/demo_guarded.json --markdown reports/demo_guarded.md

sample:
	$(PYTHON) -m examples.logistics_intake.sample
	$(PYTHON) -m examples.code_review.sample

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
