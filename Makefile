.PHONY: test release-test typecheck lint format check release-check docs-api pypi-build pypi-check pypi-clean

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

release-test:
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) -n 0 -m release \
		tests/test_install_marivo_script.py \
		tests/test_install_marivo_script_uv.py

typecheck:
	@./scripts/require-venv.sh mypy
	@$(VENV_MYPY) marivo tests/typing

lint:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) check .
	@$(VENV_LINT_IMPORTS)

format:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) format .
	@$(VENV_RUFF) check --fix .

check: lint typecheck test

release-check: check release-test

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
