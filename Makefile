.PHONY: test typecheck lint format check test-mysql binary binary-clean

VENV_PYTHON := .venv/bin/python
VENV_PIP := .venv/bin/pip
VENV_PYTEST := .venv/bin/pytest
VENV_MYPY := .venv/bin/mypy
VENV_RUFF := .venv/bin/ruff
VENV_LINT_IMPORTS := .venv/bin/lint-imports

test:
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) $(TESTS)

typecheck:
	@./scripts/require-venv.sh mypy
	@$(VENV_MYPY) marivo

lint:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) check .
	@$(VENV_LINT_IMPORTS)

format:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) format .
	@$(VENV_RUFF) check --fix .

test-mysql:
	pip install -e ".[mysql,test-mysql]"
	$(VENV_PYTEST) tests/contracts/ -m mysql

check: lint typecheck test

binary: ## Build onedir Marivo binary (excludes duckdb)
	@./scripts/require-venv.sh pyinstaller
	@$(VENV_PIP) install pyinstaller
	@$(VENV_PIP) install --no-deps .
	@.venv/bin/pyinstaller marivo.spec --noconfirm
	@echo "Binary built: dist/marivo/marivo"
	@./dist/marivo/marivo --help || echo "Warning: binary smoke test failed"
	@$(MAKE) package

package: ## Package dist/marivo/ into marivo_{version}_{target}.{tar.gz|zip}
	@VERSION=$$(.venv/bin/python -c "import importlib.metadata; print(importlib.metadata.version('marivo'))") \
	&& TARGET=$$(.venv/bin/python -c "\
import platform; \
m = platform.machine().lower(); \
m = 'x86_64' if m == 'amd64' else m; \
s = platform.system().lower(); \
s = 'macos' if s == 'darwin' else s; \
print(f'{s}-{m}')") \
	&& cd dist \
	&& if [ "$$OSTYPE" = "msys" ] || [ "$$OSTYPE" = "win32" ]; then \
		python -c "import zipfile, pathlib; \
z = zipfile.ZipFile('marivo_$${VERSION}_$${TARGET}.zip', 'w', zipfile.ZIP_DEFLATED); \
[z.write(str(p), str(p.relative_to('dist'))) for p in pathlib.Path('marivo').rglob('*') if p.is_file()]; \
z.close()"; \
	else \
		tar czf marivo_$${VERSION}_$${TARGET}.tar.gz marivo/; \
	fi \
	&& echo "Packaged: dist/marivo_$${VERSION}_$${TARGET}.*"

binary-clean: ## Remove PyInstaller build artifacts
	rm -rf build/ dist/
