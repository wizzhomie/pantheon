# Pantheon — one command per thing, and every command run from here.
#
# Two halves, two gates: the harness at the root (a box per project, standard library only,
# runs on the system interpreter) and trajan/ (the verification contour with its own venv and
# its own Makefile). `make check` is both, because "it works" means the whole repository.
#
# The interpreter is the trajan venv: it is the one with pytest, ruff and mypy installed, and
# the harness deliberately has no dependencies of its own.

PYTHON ?= $(CURDIR)/trajan/.venv/bin/python

.PHONY: check test lint typecheck harness-test harness-lint harness-typecheck trajan-check verify bench docs

check: harness-lint harness-typecheck harness-test trajan-check

test: harness-test trajan-test

lint: harness-lint

typecheck: harness-typecheck

harness-test:
	PYTHONPATH=. $(PYTHON) -m pytest tests

harness-lint:
	$(PYTHON) -m ruff check harness tests bin

harness-typecheck:
	$(PYTHON) -m mypy

trajan-test:
	$(MAKE) -C trajan test PYTHON=$(PYTHON)

trajan-check:
	$(MAKE) -C trajan check PYTHON=$(PYTHON)

verify:
	$(MAKE) -C trajan verify PYTHON=$(PYTHON)

bench:
	$(MAKE) -C trajan bench PYTHON=$(PYTHON)

docs:
	@echo "concept:   docs/concept.md"
	@echo "research:  docs/README.md"
	@echo "component: trajan/README.md"
	@echo "box:       ./bin/pantheon status --help"
