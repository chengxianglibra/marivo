"""Ownership and import contracts for private live infrastructure layers."""

from __future__ import annotations

import subprocess
import sys

import marivo
from marivo._authoring import model as authoring_model
from marivo._boundaries import semantic_analysis as boundary_model
from marivo.analysis._capabilities import model as analysis_model
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


def test_authoring_values_have_one_private_owner() -> None:
    assert authoring_model.AuthoringCapability is not None
    assert authoring_model.AuthoringContract is not None
    assert authoring_model.AuthoringRepair is not None
    assert not hasattr(live_model, "AuthoringCapability")
    assert not hasattr(live_model, "AuthoringContract")
    assert not hasattr(live_model, "AuthoringRepair")


def test_handoff_values_have_one_private_owner() -> None:
    assert boundary_model.AnalysisToSemanticHandoff is not None
    assert boundary_model.SemanticToAnalysisHandoff is not None
    assert boundary_model.SemanticHandoffReceipt is not None
    for name in (
        "AnalysisToSemanticHandoff",
        "SemanticToAnalysisHandoff",
        "SemanticHandoffReceipt",
    ):
        assert not hasattr(live_model, name)
        assert not hasattr(analysis_model, name)


def test_analysis_to_semantic_handoff_required_kind_is_symbol_kind() -> None:
    from marivo.refs import SymbolKind

    handoff = boundary_model.AnalysisToSemanticHandoff(
        required_kind=SymbolKind.METRIC,
        requirement="need a metric",
        affected_capability_id="observe",
        environment_fingerprint=live_model.EnvironmentFingerprint.current(),
    )
    assert handoff.required_kind is SymbolKind.METRIC


def test_private_leaf_imports_do_not_load_surfaces() -> None:
    script = """
import marivo.introspection.live.model
import marivo._authoring.model
import marivo._boundaries.semantic_analysis
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
