.PHONY: test typecheck lint format check examples-check docs-api pypi-build pypi-check pypi-clean analysis-surface-eval semantic-surface-eval

ifeq ($(OS),Windows_NT)
VENV_BIN := .venv/Scripts
EXE_SUFFIX := .exe
else
VENV_BIN := .venv/bin
EXE_SUFFIX :=
endif

VENV_PYTHON := $(VENV_BIN)/python$(EXE_SUFFIX)
VENV_PIP := $(VENV_BIN)/pip$(EXE_SUFFIX)
VENV_PYTEST := $(VENV_BIN)/pytest$(EXE_SUFFIX)
VENV_MYPY := $(VENV_BIN)/mypy$(EXE_SUFFIX)
VENV_RUFF := $(VENV_BIN)/ruff$(EXE_SUFFIX)
VENV_LINT_IMPORTS := $(VENV_BIN)/lint-imports$(EXE_SUFFIX)
VENV_TWINE := $(VENV_BIN)/twine$(EXE_SUFFIX)

PYPI_DIST_DIR := dist/pypi

test:
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) $(if $(findstring ::,$(TESTS)),-n 0,) $(TESTS)

typecheck:
	@./scripts/require-venv.sh mypy
	@$(VENV_MYPY) marivo

examples-check:
	@for examples_dir in marivo/skills/marivo-*/references/examples; do \
		if [ ! -d "$$examples_dir" ]; then continue; fi; \
		EXAMPLE_TYPECHECK_FILES=$$(mktemp); \
		find "$$examples_dir" -type f \( -name '*.py' -o -name '*.pyi' \) -print0 > "$$EXAMPLE_TYPECHECK_FILES"; \
		if [ -s "$$EXAMPLE_TYPECHECK_FILES" ]; then \
			xargs -0 $(VENV_MYPY) --explicit-package-bases --ignore-missing-imports \
				< "$$EXAMPLE_TYPECHECK_FILES" || { status=$$?; rm -f "$$EXAMPLE_TYPECHECK_FILES"; exit $$status; }; \
		fi; \
		rm -f "$$EXAMPLE_TYPECHECK_FILES"; \
	done
	@$(VENV_PYTHON) scripts/run_skill_examples.py

lint:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) check .
	@$(VENV_LINT_IMPORTS)

format:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) format .
	@$(VENV_RUFF) check --fix .

check: lint typecheck examples-check test

docs-api: ## Build the Sphinx Python API reference into site/public/api/
	@./scripts/require-venv.sh sphinx-build
	@rm -rf docs/api/api
	@rm -rf site/public/api
	@$(VENV_BIN)/sphinx-build$(EXE_SUFFIX) -W --keep-going -b html docs/api site/public/api
	@echo "API docs built in site/public/api"

pypi-build: ## Build PyPI sdist and wheel into dist/pypi/
	@./scripts/require-venv.sh pip
	@$(VENV_PIP) install build twine
	@$(MAKE) pypi-clean
	@mkdir -p $(PYPI_DIST_DIR)
	@$(VENV_PYTHON) -m build --outdir $(PYPI_DIST_DIR)
	@echo "PyPI artifacts built in $(PYPI_DIST_DIR)"

pypi-check: ## Validate PyPI sdist and wheel in dist/pypi/
	@./scripts/require-venv.sh twine
	@$(VENV_TWINE) check $(PYPI_DIST_DIR)/*

pypi-clean: ## Remove PyPI build artifacts
	rm -rf $(PYPI_DIST_DIR) dist/marivo-*.tar.gz dist/marivo-*.whl

analysis-surface-eval: pypi-build ## Run the cold-agent analysis surface evaluation gate
	@$(VENV_PYTHON) -m scripts.analysis_surface_eval.runner \
		--profile evals/analysis_surface/profile.toml \
		--wheel "$$(ls -1 dist/pypi/marivo-*.whl | tail -1)"

semantic-surface-eval: pypi-build ## Run the cold-agent semantic surface evaluation gate
	@$(VENV_PYTHON) -m scripts.semantic_surface_eval.runner \
		--profile evals/semantic-surface/profile.toml \
		--wheel "$$(ls -1 dist/pypi/marivo-*.whl | tail -1)"
