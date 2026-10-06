"""Run isolated positive and negative public typing probes."""

import os
import subprocess
from pathlib import Path

from tests.support.paths import PROJECT_ROOT


def check(source: Path) -> subprocess.CompletedProcess[str]:
    root = PROJECT_ROOT
    return subprocess.run(
        [
            str(root / ".venv/bin/mypy"),
            "--no-pretty",
            "--no-color-output",
            "--cache-dir",
            str(source.parent / "mypy-cache"),
            "--python-version",
            "3.10",
            str(source),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        timeout=120,
    )
