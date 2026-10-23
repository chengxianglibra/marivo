.PHONY: test runtime-test runtime-test-agent release-test typecheck lint lint-agent format \
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
RUNTIME_WORKERS ?= 2
PYTEST_WORKERS ?= 12

PYTEST_FLAGS := -q --tb=short --maxfail=5
MYPY_FLAGS := --no-pretty --no-color-output --no-warn-unused-configs
AGENT_RUFF_FLAGS := --output-format concise

PYPI_DIST_DIR := dist/pypi

test:
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) $(PYTEST_FLAGS) -n $(if $(findstring ::,$(TESTS)),0,$(PYTEST_WORKERS)) $(TESTS)

runtime-test:
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) -m runtime -n $(if $(findstring ::,$(TESTS)),0,$(RUNTIME_WORKERS)) $(TESTS)

runtime-test-agent:
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) $(PYTEST_FLAGS) -m runtime -n $(if $(findstring ::,$(TESTS)),0,$(RUNTIME_WORKERS)) $(TESTS)

# Explicit opt-in only; services are managed outside pytest.
.PHONY: installed-multisource-test
installed-multisource-test:
	@test "$$MARIVO_INSTALLED_MULTISOURCE_TEST" = "1" || (echo "Set MARIVO_INSTALLED_MULTISOURCE_TEST=1 and start the selected services first."; exit 1)
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) $(PYTEST_FLAGS) -n 0 -m release tests/packaging/test_installed_sources.py

release-test: pypi-build pypi-check
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) -n 0 -m release \
		tests/packaging/test_installer.py \
		tests/packaging/test_installer_uv.py \
		tests/packaging/test_wheel.py \
		tests/packaging/test_installed_journeys.py \
		tests/surface/test_disclosure_documentation.py \
		tests/semantic/test_semantic_live_help.py \
		tests/surface/test_analysis_disclosure_content.py \
		tests/analysis/materialization/test_frame_storage.py \
		tests/analysis/materialization/test_frame_attribute_contribution.py \
		tests/analysis/materialization/test_terminal_publication.py \
		tests/surface/test_analysis_typing.py \
		tests/analysis/materialization/test_relation_recovery.py \
		tests/analysis/statistics/test_analysis_deviation_domains.py \
		tests/analysis/statistics/test_deviation_contributions.py \
		tests/analysis/materialization/test_versioned_recovery.py \
		tests/analysis/statistics/test_analysis_deviation_display.py \
		tests/analysis/statistics/test_analysis_runs.py \
		tests/analysis/statistics/test_analysis_deviation.py \
		tests/analysis/numeric/test_analysis_attribution_runtime.py \
		tests/analysis/graph/test_distribution_carriers.py \
		tests/analysis/temporal/test_analysis_temporal.py \
		tests/analysis/materialization/test_reference_recovery.py \
		tests/analysis/graph/test_capability_consumers.py \
		tests/analysis/graph/test_computation_rollup_runtime.py \
		tests/analysis/graph/test_count_rollup_backends.py \
		tests/analysis/graph/test_observation_grain.py \
		tests/analysis/graph/test_premise_verification.py \
		tests/analysis/graph/test_public_composition.py \
		tests/analysis/graph/test_relationship_binding.py \
		tests/analysis/graph/test_runtime_concurrency.py \
		tests/analysis/materialization/test_attribute_contribution_oracles.py \
		tests/analysis/materialization/test_attribute_inputs.py \
		tests/analysis/materialization/test_attribute_temporal_inputs.py \
		tests/analysis/materialization/test_comparison_recovery.py \
		tests/analysis/materialization/test_distribution_recovery.py \
		tests/analysis/materialization/test_frame_attribute.py \
		tests/analysis/materialization/test_frame_attribute_journeys.py \
		tests/analysis/materialization/test_frame_temporal_expressions.py \
		tests/analysis/materialization/test_mean_recovery.py \
		tests/analysis/materialization/test_multiroot_recovery.py \
		tests/analysis/materialization/test_named_calendar_windows.py \
		tests/analysis/materialization/test_numeric_recovery.py \
		tests/analysis/materialization/test_round1_api_fixes.py \
		tests/analysis/numeric/test_analysis_cohort.py \
		tests/analysis/numeric/test_analysis_comparison_runtime.py \
		tests/analysis/numeric/test_analysis_display.py \
		tests/analysis/numeric/test_analysis_members.py \
		tests/analysis/numeric/test_analysis_numeric.py \
		tests/analysis/numeric/test_analysis_predicates.py \
		tests/analysis/numeric/test_analysis_references.py \
		tests/analysis/numeric/test_members_consumers.py \
		tests/analysis/statistics/test_analysis_statistics_faults.py \
		tests/analysis/temporal/test_temporal_recovery.py \
		tests/analysis/temporal/test_timezone_observation.py

typecheck:
	@./scripts/require-venv.sh mypy
	@$(VENV_MYPY) $(MYPY_FLAGS) --python-version $(MYPY_PYTHON_VERSION) $(TYPECHECK_TARGETS)

lint:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) format --check $(LINT_TARGETS)
	@$(VENV_RUFF) check $(LINT_TARGETS)
	@$(VENV_LINT_IMPORTS)

lint-agent:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) format --check $(LINT_TARGETS)
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

check-agent: lint-agent typecheck test docs-api-agent

release-check:
	@$(MAKE) check runtime-test release-test TESTS=

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
