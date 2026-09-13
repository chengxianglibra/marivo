"""The accepted public cutover has no compatibility entry for removed capabilities."""

import importlib

import pytest

import marivo.analysis as mv
from marivo._help.model import MarivoHelpTargetError
from tests.shared_fixtures import rendered_help


@pytest.mark.parametrize(
    "name",
    [
        "BaseFrame",
        "MetricFrame",
        "DeltaFrame",
        "AttributionFrame",
        "EventFrame",
        "LifecycleFrame",
        "ForecastFrame",
        "AssociationResult",
        "CandidateSet",
        "SubjectSet",
        "LogicalSubjectSet",
        "MaterializedSubjectSet",
        "EventOccurrenceBounds",
        "EventWatermarkRequest",
        "EventWatermarkReceipt",
        "declared_complete_through",
        "HypothesisTestResult",
        "TestDecision",
        "OntologyMetricCandidate",
    ],
)
def test_removed_public_values_are_absent(name: str) -> None:
    names = mv.__all__
    assert isinstance(names, list)
    assert name not in names
    assert name not in dir(mv)
    with pytest.raises(AttributeError):
        getattr(mv, name)


@pytest.mark.parametrize(
    "target",
    [
        "analysis.events.watermark",
        "analysis.events.occurrence_bounds",
        "analysis.hypothesis_test",
        "analysis.discover.semantic_hypotheses",
        "analysis.discover.interesting_slices",
        "analysis.discover.cross_sectional_outliers",
    ],
)
def test_removed_help_targets_do_not_redirect_to_unrelated_capabilities(target: str) -> None:
    with pytest.raises(MarivoHelpTargetError):
        rendered_help(target)


@pytest.mark.parametrize(
    "name",
    [
        "compare",
        "attribute",
        "correlate",
        "forecast",
        "select_subjects",
        "delete",
        "hypothesis_test",
        "transform",
    ],
)
def test_session_has_no_downstream_compatibility_methods(name: str) -> None:
    assert not hasattr(mv.Session, name)


@pytest.mark.parametrize(
    "path",
    [
        "marivo.analysis.frames.metric",
        "marivo.analysis.evidence.pipeline",
        "marivo.analysis.intents.hypothesis_test",
        "marivo.analysis.materialization.engine",
    ],
)
def test_removed_implementations_are_not_importable(path: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(path)
