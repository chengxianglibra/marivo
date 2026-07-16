"""Ownership and import contracts for private live infrastructure layers."""

from __future__ import annotations

import subprocess
import sys

import marivo
from marivo._authoring import model as authoring_model
from marivo.introspection.live import model as live_model


def test_neutral_primitives_exist() -> None:
    assert live_model.HelpSurface is not None
    assert live_model.EnvironmentFingerprint is not None
    assert live_model.LiveHelpTarget is not None
    assert live_model.SurfaceLimits is not None
    assert live_model.SURFACE_LIMITS is not None
    assert live_model.ResolvableHelpDescriptor is not None
    assert live_model.LiveSurfaceRegistry is not None


def test_environment_fingerprint_current_is_self_consistent() -> None:
    fingerprint = live_model.EnvironmentFingerprint.current()
    assert fingerprint.marivo_version == marivo.__version__
    assert fingerprint.python_executable
    assert fingerprint.package_path


def test_environment_fingerprint_reports_running_interpreter_not_symlink_target() -> None:
    """The fingerprint must report ``sys.executable`` (the interpreter that is
    actually running marivo, e.g. ``.venv/bin/python``), not the symlink target
    that ``Path.resolve()`` would chase it to (the system Python).

    This keeps ``marivo help`` consistent with ``marivo doctor`` and with the
    ``Package:`` line, which already points into the venv. See issue #20.
    """
    from pathlib import Path

    fingerprint = live_model.EnvironmentFingerprint.current()
    assert fingerprint.python_executable == sys.executable
    # If the interpreter is a venv symlink, resolving it must NOT leak into the
    # fingerprint — that is exactly the system-Python mismatch reported in #20.
    resolved = str(Path(sys.executable).resolve())
    if resolved != sys.executable:
        assert fingerprint.python_executable != resolved


def test_authoring_values_have_one_private_owner() -> None:
    assert authoring_model.AuthoringCapability is not None
    assert authoring_model.AuthoringContract is not None
    assert authoring_model.AuthoringRepair is not None
    assert not hasattr(live_model, "AuthoringCapability")
    assert not hasattr(live_model, "AuthoringContract")
    assert not hasattr(live_model, "AuthoringRepair")


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
