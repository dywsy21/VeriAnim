.PHONY: check compile lint test

PYTHON ?= python

test:
	$(PYTHON) -m unittest discover -s tests

compile:
	$(PYTHON) -m py_compile blender/addon.py blender/client.py blender/headless.py config/loader.py harness/*.py scripts/*.py tests/*.py

lint:
	$(PYTHON) -m ruff check .

check: compile test
	@if $(PYTHON) -c "import ruff" >/dev/null 2>&1; then \
		$(PYTHON) -m ruff check .; \
	else \
		echo "ruff not installed; install dev dependencies with: pip install -e '.[dev]'"; \
	fi
