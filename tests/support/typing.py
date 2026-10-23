"""Run isolated positive and negative public typing probes."""

import os
import subprocess
from pathlib import Path

from tests.support.paths import PROJECT_ROOT


def check(*sources: Path) -> subprocess.CompletedProcess[str]:
    root = PROJECT_ROOT
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    return subprocess.run(
        [
            str(root / ".venv/bin/mypy"),
            "--no-pretty",
            "--no-color-output",
            "--cache-dir",
            str(root / ".mypy_cache" / "typing-tests" / worker),
            "--python-version",
            "3.10",
            *(str(source) for source in sources),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        timeout=120,
    )
