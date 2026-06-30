# agents-collab — convenience targets. The tool itself is plain Python (stdlib
# only); these are just shortcuts.

PYTHON ?= python3

.PHONY: help install uninstall check test version

help:
	@echo "make install TARGET=../repo     copy the loop into a target repository"
	@echo "make uninstall TARGET=../repo   remove the loop from a target repository"
	@echo "make check                      byte-compile + run the unit tests"
	@echo "make test                       run the unit tests (stdlib unittest)"
	@echo "make version                    print the tool version"

install:
	@if [ -z "$(TARGET)" ]; then \
		echo "usage: make install TARGET=../path/to/repo"; exit 1; \
	fi
	@./install.sh "$(TARGET)"

# Pass extra flags through ARGS, e.g.: make uninstall TARGET=../repo ARGS=--dry-run
uninstall:
	@if [ -z "$(TARGET)" ]; then \
		echo "usage: make uninstall TARGET=../path/to/repo [ARGS=--dry-run]"; exit 1; \
	fi
	@./install.sh --uninstall $(ARGS) "$(TARGET)"

check: test
	$(PYTHON) -m py_compile driver.py executors.py
	@echo "compile OK"

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

version:
	@$(PYTHON) driver.py --version
