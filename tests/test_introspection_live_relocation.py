"""Relocation contract: shared primitives live in the neutral live package and
remain importable from the analysis kernel for backward compatibility."""

from __future__ import annotations

import marivo
from marivo.analysis._capabilities import model as analysis_model
from marivo.introspection.live import model as live_model


def test_neutral_primitives_exist():
    assert live_model.HelpSurface is not None
    assert live_model.EnvironmentFingerprint is not None
    assert live_model.LiveHelpTarget is not None
    assert live_model.SurfaceLimits is not None
    assert live_model.SURFACE_LIMITS is not None


def test_environment_fingerprint_current_is_self_consistent():
    fp = live_model.EnvironmentFingerprint.current()
    assert fp.marivo_version == marivo.__version__
    assert fp.python_executable  # non-empty
    assert fp.package_path  # non-empty


def test_surface_limits_singleton_has_expected_fields():
    limits = live_model.SURFACE_LIMITS
    assert limits.object_contract_max_subjects == 8
    assert limits.help_suggestion_limit == 5


def test_analysis_kernel_re_exports_relocated_primitives():
    # Behavior preservation: analysis internals still import the same objects.
    assert analysis_model.EnvironmentFingerprint is live_model.EnvironmentFingerprint
    assert analysis_model.LiveHelpTarget is live_model.LiveHelpTarget
    assert analysis_model.HelpSurface is live_model.HelpSurface
    assert analysis_model.SurfaceLimits is live_model.SurfaceLimits
    assert analysis_model.SURFACE_LIMITS is live_model.SURFACE_LIMITS


def test_live_package_does_not_import_surfaces_at_load():
    import subprocess
    import sys

    # A fresh interpreter importing only the neutral model must not drag the
    # datasource/semantic/analysis surfaces into memory. (marivo/__init__.py and
    # marivo/introspection/__init__.py are both light, so this isolates live.model.)
    script = (
        "import marivo.introspection.live.model as m\n"
        "import sys\n"
        "for forbidden in ('marivo.semantic','marivo.datasource','marivo.analysis'):\n"
        "    assert forbidden not in sys.modules, forbidden\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_neutral_handoff_schemas_exist():
    assert live_model.AnalysisToSemanticHandoff is not None
    assert live_model.SemanticToAnalysisHandoff is not None
    assert live_model.SemanticHandoffReceipt is not None


def test_analysis_kernel_re_exports_handoff_schemas():
    assert analysis_model.AnalysisToSemanticHandoff is live_model.AnalysisToSemanticHandoff
    assert analysis_model.SemanticToAnalysisHandoff is live_model.SemanticToAnalysisHandoff
    assert analysis_model.SemanticHandoffReceipt is live_model.SemanticHandoffReceipt


def test_analysis_to_semantic_handoff_required_kind_is_symbolkind():
    from marivo.refs import SymbolKind

    fp = live_model.EnvironmentFingerprint.current()
    handoff = live_model.AnalysisToSemanticHandoff(
        required_kind=SymbolKind.METRIC,
        requirement="need a metric",
        affected_capability_id="observe",
        environment_fingerprint=fp,
    )
    assert handoff.required_kind is SymbolKind.METRIC
