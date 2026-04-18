.PHONY: test typecheck lint format check reset-metadata

VENV_PYTHON := .venv/bin/python
VENV_PYTEST := .venv/bin/pytest
VENV_MYPY := .venv/bin/mypy
VENV_RUFF := .venv/bin/ruff

test:
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) $(TESTS)

typecheck:
	@./scripts/require-venv.sh mypy
	@$(VENV_MYPY) app

lint:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) check .

format:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) format .
	@$(VENV_RUFF) check --fix .

check: lint typecheck test

reset-metadata:
	@./scripts/reset-metadata-sqlite.sh
