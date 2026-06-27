# agentic-loop — convenience targets. The tool itself is plain Python (stdlib
# only); these are just shortcuts.

PYTHON ?= python3

.PHONY: help install check version

help:
	@echo "make install TARGET=../repo   copy the loop into a target repository"
	@echo "make check                    byte-compile driver.py + executors.py"
	@echo "make version                  print the tool version"

install:
	@if [ -z "$(TARGET)" ]; then \
		echo "usage: make install TARGET=../path/to/repo"; exit 1; \
	fi
	@./install.sh "$(TARGET)"

check:
	$(PYTHON) -m py_compile driver.py executors.py
	@echo "compile OK"

version:
	@$(PYTHON) driver.py --version
