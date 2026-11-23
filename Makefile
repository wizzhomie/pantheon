# Pantheon — the harness targets, and one contour that runs.
#
# Everything that actually executes today lives in trajan/. These targets exist so that the
# repository has one entry point and one place where the Python interpreter is chosen; the
# per-component details stay in that component's own Makefile.

PYTHON ?= $(CURDIR)/trajan/.venv/bin/python

.PHONY: check test lint verify bench docs

check test lint:
	$(MAKE) -C trajan $@ PYTHON=$(PYTHON)

verify:
	$(MAKE) -C trajan verify PYTHON=$(PYTHON)

bench:
	$(MAKE) -C trajan bench PYTHON=$(PYTHON)

docs:
	@echo "concept:   docs/concept.md"
	@echo "research:  docs/README.md"
	@echo "component: trajan/README.md"
