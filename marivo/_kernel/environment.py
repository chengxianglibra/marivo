"""Installed package and interpreter identity shared by diagnostics and Help."""

import sys
from importlib.metadata import version
from pathlib import Path
from typing import TypedDict

MARIVO_VERSION = version("marivo")


class EnvironmentIdentity(TypedDict):
    marivo_version: str
    python_executable: str
    package_path: str


def environment_identity() -> EnvironmentIdentity:
    """Read the package and interpreter identity of this process."""
    return {
        "marivo_version": MARIVO_VERSION,
        "python_executable": sys.executable,
        "package_path": str((Path(__file__).parent.parent / "__init__.py").resolve()),
    }
