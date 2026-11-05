"""Ownership and import contracts for private live infrastructure layers."""

from __future__ import annotations

import subprocess
import sys

import marivo
from marivo.introspection.live import model as live_model


def test_environment_fingerprint_current_is_self_consistent() -> None:
    fingerprint = live_model.EnvironmentFingerprint.current()
    assert fingerprint.marivo_version == marivo.__version__
    assert fingerprint.python_executable
    assert fingerprint.package_path


def test_private_leaf_imports_do_not_load_surfaces() -> None:
    script = """
import marivo.introspection.live.model
import marivo._authoring.model
import sys
for forbidden in ('marivo.datasource', 'marivo.semantic', 'marivo.analysis'):
    assert forbidden not in sys.modules, forbidden
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
