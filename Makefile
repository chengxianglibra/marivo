.PHONY: test test-agent release-test typecheck typecheck-agent lint lint-agent format \
	check check-agent release-check docs-api docs-api-agent pypi-build pypi-check pypi-clean

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
MYPY_PYTHON_VERSION ?= 3.10
TYPECHECK_TARGETS ?= marivo tests/typing
LINT_TARGETS ?= .

AGENT_PYTEST_FLAGS := -q --tb=short --maxfail=5
AGENT_MYPY_FLAGS := --no-pretty --no-color-output --no-warn-unused-configs
AGENT_RUFF_FLAGS := --output-format concise

PYPI_DIST_DIR := dist/pypi

test:
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) $(if $(findstring ::,$(TESTS)),-n 0,) $(TESTS)

test-agent:
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) $(AGENT_PYTEST_FLAGS) $(if $(findstring ::,$(TESTS)),-n 0,) $(TESTS)

release-test: pypi-build pypi-check
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) -n 0 -m release \
		tests/test_install_marivo_script.py \
		tests/test_install_marivo_script_uv.py \
		tests/test_analysis_runtime_wheel.py

typecheck:
	@./scripts/require-venv.sh mypy
	@$(VENV_MYPY) --python-version $(MYPY_PYTHON_VERSION) $(TYPECHECK_TARGETS)

typecheck-agent:
	@./scripts/require-venv.sh mypy
	@$(VENV_MYPY) $(AGENT_MYPY_FLAGS) --python-version $(MYPY_PYTHON_VERSION) $(TYPECHECK_TARGETS)

lint:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) check $(LINT_TARGETS)
	@$(VENV_LINT_IMPORTS)

lint-agent:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) check $(AGENT_RUFF_FLAGS) $(LINT_TARGETS)
	@agent_log=$$(mktemp "$${TMPDIR:-/tmp}/marivo-import-linter.XXXXXX"); \
		if $(VENV_LINT_IMPORTS) >"$$agent_log" 2>&1; then \
			rm -f "$$agent_log"; \
			echo "Import contracts passed"; \
		else \
			agent_status=$$?; \
			cat "$$agent_log"; \
			rm -f "$$agent_log"; \
			exit "$$agent_status"; \
		fi

format:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) format .
	@$(VENV_RUFF) check --fix .

check: lint typecheck test docs-api

check-agent: lint-agent typecheck-agent test-agent docs-api-agent

release-check: check release-test

docs-api: ## Build the Sphinx Python API reference into site/public/api/
	@./scripts/require-venv.sh sphinx-build
	@rm -rf docs/api/api
	@rm -rf site/public/api
	@$(VENV_BIN)/sphinx-build$(EXE_SUFFIX) -W --keep-going -b html docs/api site/public/api
	@echo "API docs built in site/public/api"

docs-api-agent: ## Build the Sphinx Python API reference with compact output.
	@./scripts/require-venv.sh sphinx-build
	@rm -rf docs/api/api
	@rm -rf site/public/api
	@$(VENV_BIN)/sphinx-build$(EXE_SUFFIX) -q -N -W --keep-going -b html docs/api site/public/api
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
	@$(VENV_PYTHON) scripts/check-wheel-contents.py $(PYPI_DIST_DIR)

pypi-clean: ## Remove PyPI build artifacts
	rm -rf $(PYPI_DIST_DIR) dist/marivo-*.tar.gz dist/marivo-*.whl build marivo.egg-info
