.PHONY: test typecheck lint format check test-mysql pypi-build pypi-check pypi-clean binary binary-sign binary-unquarantine package binary-clean

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
VENV_PYINSTALLER := $(VENV_BIN)/pyinstaller$(EXE_SUFFIX)
VENV_BUILD := $(VENV_BIN)/build$(EXE_SUFFIX)
VENV_TWINE := $(VENV_BIN)/twine$(EXE_SUFFIX)

PYPI_DIST_DIR := dist/pypi
PYINSTALLER_DIST_DIR := dist/pyinstaller
PYINSTALLER_BUILD_DIR := build/pyinstaller
MARIVO_BINARY := $(PYINSTALLER_DIST_DIR)/marivo/marivo$(EXE_SUFFIX)

test:
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) $(if $(findstring ::,$(TESTS)),-n 0,) $(TESTS)

typecheck:
	@./scripts/require-venv.sh mypy
	@$(VENV_MYPY) marivo

.PHONY: examples-check
examples-check:
	@EXAMPLE_TYPECHECK_FILES=$$(mktemp); \
	find marivo-skill/marivo-py-semantic/references/examples \
	     marivo-skill/marivo-py-analysis/references/examples \
	     -type f \( -name '*.py' -o -name '*.pyi' \) -print0 > "$$EXAMPLE_TYPECHECK_FILES"; \
	trap 'rm -f "$$EXAMPLE_TYPECHECK_FILES"' EXIT; \
	if [ -s "$$EXAMPLE_TYPECHECK_FILES" ]; then \
		xargs -0 $(VENV_MYPY) --explicit-package-bases --ignore-missing-imports \
			< "$$EXAMPLE_TYPECHECK_FILES"; \
	fi
	@$(VENV_PYTHON) scripts/run_skill_examples.py

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

check: lint typecheck examples-check test

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

binary: ## Build onedir Marivo binary (excludes duckdb)
	@./scripts/require-venv.sh pip
	@$(VENV_PIP) install ".[mysql,trino]" pyinstaller
	@$(MAKE) binary-clean
	@mkdir -p $(PYINSTALLER_DIST_DIR) $(PYINSTALLER_BUILD_DIR)
	@$(VENV_PYINSTALLER) marivo.spec --noconfirm --distpath $(PYINSTALLER_DIST_DIR) --workpath $(PYINSTALLER_BUILD_DIR)
	@echo "Binary built: $(MARIVO_BINARY)"
	@$(MAKE) binary-sign
	@./$(MARIVO_BINARY) --help
	@./$(MARIVO_BINARY) serve --help
	@$(MAKE) package

binary-sign: ## Ad-hoc sign macOS binary for internal distribution
	@if [ "$$(uname -s)" = "Darwin" ]; then \
		echo "Removing macOS quarantine attributes from $(PYINSTALLER_DIST_DIR)/marivo"; \
		$(MAKE) binary-unquarantine; \
		if [ ! -f $(PYINSTALLER_DIST_DIR)/marivo/marivo-bin ]; then \
			mv $(PYINSTALLER_DIST_DIR)/marivo/marivo $(PYINSTALLER_DIST_DIR)/marivo/marivo-bin; \
		fi; \
		printf '%s\n' \
			'#!/bin/sh' \
			'set -eu' \
			'TARGET_DIR=$${1:-$$(CDPATH= cd -- "$$(dirname -- "$$0")" && pwd)}' \
			'/usr/bin/xattr -dr com.apple.quarantine "$$TARGET_DIR" 2>/dev/null || true' \
			> $(PYINSTALLER_DIST_DIR)/marivo/macos-unquarantine; \
		chmod +x $(PYINSTALLER_DIST_DIR)/marivo/macos-unquarantine; \
		printf '%s\n' \
			'#!/bin/sh' \
			'set -eu' \
			'SELF_DIR=$$(CDPATH= cd -- "$$(dirname -- "$$0")" && pwd)' \
			'"$$SELF_DIR/macos-unquarantine" "$$SELF_DIR" 2>/dev/null || true' \
			'exec "$$SELF_DIR/marivo-bin" "$$@"' \
			> $(PYINSTALLER_DIST_DIR)/marivo/marivo; \
		chmod +x $(PYINSTALLER_DIST_DIR)/marivo/marivo; \
		echo "Ad-hoc signing macOS Mach-O files in $(PYINSTALLER_DIST_DIR)/marivo"; \
		find $(PYINSTALLER_DIST_DIR)/marivo -type f -exec sh -c 'for file do if file "$$file" | grep -q "Mach-O"; then codesign --force --sign - "$$file"; fi; done' sh {} +; \
		codesign --force --deep --sign - $(PYINSTALLER_DIST_DIR)/marivo/marivo-bin; \
		codesign --verify --deep --strict $(PYINSTALLER_DIST_DIR)/marivo/marivo-bin; \
	else \
		echo "Skipping ad-hoc signing: not running on macOS"; \
	fi

binary-unquarantine: ## Remove macOS Gatekeeper quarantine from dist/marivo
	@if [ "$$(uname -s)" = "Darwin" ]; then \
		xattr -dr com.apple.quarantine $(PYINSTALLER_DIST_DIR) 2>/dev/null || true; \
	else \
		echo "Skipping quarantine cleanup: not running on macOS"; \
	fi

package: ## Package $(PYINSTALLER_DIST_DIR)/marivo into marivo_{version}_{target}.{tar.gz|zip}
	@VERSION=$$($(VENV_PYTHON) -c "import importlib.metadata; print(importlib.metadata.version('marivo'))") \
	&& TARGET=$$($(VENV_PYTHON) -c "\
import platform; \
m = platform.machine().lower(); \
m = 'x86_64' if m == 'amd64' else m; \
s = platform.system().lower(); \
s = 'macos' if s == 'darwin' else s; \
print(f'{s}-{m}')") \
	&& cd $(PYINSTALLER_DIST_DIR) \
	&& if [ "$$OSTYPE" = "msys" ] || [ "$$OSTYPE" = "win32" ]; then \
		../../$(VENV_PYTHON) -c "import zipfile, pathlib; \
z = zipfile.ZipFile('marivo_$${VERSION}_$${TARGET}.zip', 'w', zipfile.ZIP_DEFLATED); \
[z.write(str(p), str(p)) for p in pathlib.Path('marivo').rglob('*') if p.is_file()]; \
z.close()"; \
	else \
		COPYFILE_DISABLE=1 tar czf marivo_$${VERSION}_$${TARGET}.tar.gz marivo/; \
	fi \
	&& echo "Packaged: $(PYINSTALLER_DIST_DIR)/marivo_$${VERSION}_$${TARGET}.*"

binary-clean: ## Remove PyInstaller build artifacts
	rm -rf $(PYINSTALLER_BUILD_DIR) $(PYINSTALLER_DIST_DIR) dist/marivo dist/marivo_*.tar.gz dist/marivo_*.zip
