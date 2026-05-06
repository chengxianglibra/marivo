.PHONY: test typecheck lint format check

VENV_PYTHON := .venv/bin/python
VENV_PYTEST := .venv/bin/pytest
VENV_MYPY := .venv/bin/mypy
VENV_RUFF := .venv/bin/ruff
VENV_LINT_IMPORTS := .venv/bin/lint-imports

test:
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) $(TESTS)

typecheck:
	@./scripts/require-venv.sh mypy
	@$(VENV_MYPY) app

lint:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) check .
	@$(VENV_LINT_IMPORTS)

format:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) format .
	@$(VENV_RUFF) check --fix .

check: lint typecheck test
