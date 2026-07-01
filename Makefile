# agents-collab — convenience targets. The tool itself is plain Python (stdlib
# only); these are just shortcuts.

PYTHON ?= python3

.PHONY: help install uninstall check test version release

help:
	@echo "make install TARGET=../repo     copy the loop into a target repository"
	@echo "make uninstall TARGET=../repo   remove the loop from a target repository"
	@echo "make check                      byte-compile + run the unit tests"
	@echo "make test                       run the unit tests (stdlib unittest)"
	@echo "make version                    print the tool version"
	@echo "make release VERSION=X.Y.Z      test, bump, date CHANGELOG, commit, tag (push yourself)"

install:
	@if [ -z "$(TARGET)" ]; then \
		echo "usage: make install TARGET=../path/to/repo"; exit 1; \
	fi
	@$(PYTHON) install.py "$(TARGET)"

# Pass extra flags through ARGS, e.g.: make uninstall TARGET=../repo ARGS=--dry-run
uninstall:
	@if [ -z "$(TARGET)" ]; then \
		echo "usage: make uninstall TARGET=../path/to/repo [ARGS=--dry-run]"; exit 1; \
	fi
	@$(PYTHON) install.py --uninstall $(ARGS) "$(TARGET)"

check: test
	$(PYTHON) -m py_compile driver.py executors.py install.py
	@echo "compile OK"

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

version:
	@$(PYTHON) driver.py --version

# Cut a release LOCALLY: run the tests, bump driver.py's __version__, turn the
# CHANGELOG's [Unreleased] section into a dated [VERSION] section (leaving a fresh
# empty [Unreleased]), then commit and tag. It STOPS there on purpose — the push and
# the GitHub release (with your bespoke notes) stay in your hands; the exact commands
# are printed. Refuses on a dirty tree, off main, a bad/duplicate VERSION, or an
# empty [Unreleased] (nothing to release).
release:
	@if [ -z "$(VERSION)" ]; then echo "usage: make release VERSION=X.Y.Z"; exit 1; fi
	@echo "$(VERSION)" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$$' || { echo "error: VERSION must be X.Y.Z"; exit 1; }
	@[ -z "$$(git status --porcelain)" ] || { echo "error: working tree not clean — commit or stash first"; exit 1; }
	@[ "$$(git rev-parse --abbrev-ref HEAD)" = "main" ] || { echo "error: not on 'main' (that's where releases are cut)"; exit 1; }
	@if git rev-parse -q --verify "refs/tags/v$(VERSION)" >/dev/null; then echo "error: tag v$(VERSION) already exists"; exit 1; fi
	@$(MAKE) --no-print-directory check
	@$(PYTHON) -c "import re,sys,datetime; v=sys.argv[1]; q=chr(34); nl=chr(10); c=open('CHANGELOG.md',encoding='utf-8').read(); body=c.split('## [Unreleased]',1)[1].split('## [',1)[0]; (sys.exit('error: [Unreleased] is empty — nothing to release') if not body.strip() else None); d=open('driver.py',encoding='utf-8').read(); d=re.sub('__version__ = '+q+'[^'+q+']*'+q, '__version__ = '+q+v+q, d, count=1); open('driver.py','w',encoding='utf-8').write(d); c=c.replace('## [Unreleased]'+nl, '## [Unreleased]'+nl+nl+'## ['+v+'] — '+datetime.date.today().isoformat()+nl, 1); open('CHANGELOG.md','w',encoding='utf-8').write(c)" "$(VERSION)"
	@git add driver.py CHANGELOG.md
	@git commit -q -m "Release v$(VERSION): cut version"
	@git tag -a "v$(VERSION)" -m "agents-collab v$(VERSION)"
	@echo ""
	@echo "✓ committed + tagged v$(VERSION) (version bumped, CHANGELOG dated). Review with 'git show', then:"
	@echo "    git push origin main && git push origin v$(VERSION)"
	@echo "    gh release create v$(VERSION) --title \"v$(VERSION)\" --notes \"...\"   # notes = the new CHANGELOG section"
