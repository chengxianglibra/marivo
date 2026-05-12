# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Marivo server binary (no duckdb, no frontend).

Build:  make binary  (or: pyinstaller marivo.spec)
Output: dist/marivo/marivo  (onedir — fast startup)
"""

import sys
from pathlib import Path

block_cipher = None

BINARY_NAME = "marivo-server" if sys.platform == "win32" else "marivo"

# Collect all marivo package data (source files, subpackages, etc.)
# This is needed because editable installs use a custom MetaPathFinder
# that PyInstaller's static analysis cannot follow.
marivo_datas = []
marivo_binaries = []
marivo_hiddenimports: list[str] = []

marivo_root = Path("marivo")
for py in sorted(marivo_root.rglob("*.py")):
    mod = str(py.with_suffix("")).replace("/", ".")
    marivo_hiddenimports.append(mod)

# DuckDB adapter modules require duckdb at runtime (deferred import).
# Exclude them from hiddenimports since duckdb itself is excluded below.
_DUCKDB_ADAPTER_MODULES = {
    "marivo.adapters.duckdb_adapter",
    "marivo.adapters.local.duckdb_analytics",
    "marivo.adapters.local.duckdb_data_source",
}
marivo_hiddenimports = [
    mod for mod in marivo_hiddenimports
    if mod not in _DUCKDB_ADAPTER_MODULES
]

a = Analysis(
    ["scripts/_pyinstaller_entry.py"],
    pathex=["."],
    binaries=marivo_binaries,
    datas=marivo_datas,
    hiddenimports=[
        # uvicorn auto-protocol loading
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # MCP (FastMCP) — lazy-imported at runtime
        "mcp",
        "mcp.server",
        "mcp.server.fastmcp",
        # pydantic native core
        "pydantic_core",
        "pydantic_core._pydantic_core",
        "pydantic.deprecated",
        "pydantic.deprecated.decorator",
        "pydantic._internal._generate_schema",
        # Optional DB drivers — loaded via import_module
        "pymysql",
        "pymysql.cursors",
        "trino",
        "trino.dbapi",
        "trino.auth",
        # HTTP client stack
        "httpx",
        "httpcore",
        "h11",
        "anyio",
        "sniffio",
        # YAML
        "yaml",
        # All marivo submodules (auto-discovered above)
        *marivo_hiddenimports,
        # stdlib
        "tomllib",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # duckdb (38MB native .so) — excluded from binary
        "duckdb",
        # dev/test artifacts
        "frontend",
        "tests",
        "pytest",
        "_pytest",
        # unnecessary large packages
        "tkinter",
        "unittest",
        "test",
        "setuptools",
        "pip",
        "wheel",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=BINARY_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip_binaries=True,
    upx=True,
    upx_exclude=[],
    name=BINARY_NAME,
)
