from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


class BuildPy(_build_py):
    """Copy root-level Marivo skills into the wheel package resources."""

    def run(self) -> None:
        root = Path(__file__).parent
        source = root / "marivo-skill"
        if not source.is_dir():
            super().run()
            return

        # Stage the root-level skill tree inside the package before setuptools
        # computes package_data, then remove the staging directory after build.
        target = root / "marivo" / "agent_skills" / "bundled"
        staged = False
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        staged = True
        try:
            super().run()
        finally:
            if staged and target.exists():
                shutil.rmtree(target)


setup(cmdclass={"build_py": BuildPy})
