.PHONY: check compile lint paper paper-once showroom test

PYTHON ?= python
PDFLATEX ?= pdflatex
BIBTEX ?= bibtex
PAPER_DIR ?= paper
PAPER_MAIN ?= main

test:
	$(PYTHON) -m unittest discover -s tests

showroom:
	$(PYTHON) scripts/generate_showroom.py

compile:
	$(PYTHON) -m py_compile blender/addon.py blender/client.py blender/headless.py config/loader.py harness/*.py scripts/*.py tests/*.py

paper-once:
	cd $(PAPER_DIR) && $(PDFLATEX) -interaction=nonstopmode -halt-on-error $(PAPER_MAIN).tex

paper:
	cd $(PAPER_DIR) && $(PDFLATEX) -interaction=nonstopmode -halt-on-error $(PAPER_MAIN).tex
	cd $(PAPER_DIR) && $(BIBTEX) $(PAPER_MAIN)
	cd $(PAPER_DIR) && $(PDFLATEX) -interaction=nonstopmode -halt-on-error $(PAPER_MAIN).tex
	cd $(PAPER_DIR) && $(PDFLATEX) -interaction=nonstopmode -halt-on-error $(PAPER_MAIN).tex

lint:
	$(PYTHON) -m ruff check .

check: compile test
	@if $(PYTHON) -c "import ruff" >/dev/null 2>&1; then \
		$(PYTHON) -m ruff check .; \
	else \
		echo "ruff not installed; install dev dependencies with: pip install -e '.[dev]'"; \
	fi
