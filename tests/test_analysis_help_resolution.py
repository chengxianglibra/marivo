"""Native analysis error and invalid-target resolution remains deterministic."""

import types

import pytest

import marivo
from marivo._help.model import MarivoHelpTargetError
from marivo._help.render import render_help_text
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.introspection.live.model import LiveHelpTarget


@pytest.mark.parametrize("target", [123, [], {}, object(), types.ModuleType("unregistered")])
def test_invalid_objects_have_bounded_resolvable_repairs(target: object) -> None:
    with pytest.raises(MarivoHelpTargetError) as captured:
        marivo.help(target)
    error = captured.value
    assert len(error.candidates) <= 5
    for candidate in error.candidates:
        render_help_text(candidate)
    with pytest.raises(MarivoHelpTargetError) as repeated:
        marivo.help(target)
    assert repeated.value.candidates == error.candidates


def test_error_class_and_repair_free_instance_preserve_distinct_contracts() -> None:
    error = AnalysisError(
        message="Selected result is unavailable",
        expected="committed Artifact",
        received="missing reference",
        location="session.artifact",
    )
    text = render_help_text(error)[0]
    for fact in (error.message, error.expected, error.received, error.location):
        assert fact in text
    assert text != render_help_text(AnalysisError)[0]
    assert render_help_text("analysis.AnalysisError")[0] == render_help_text(AnalysisError)[0]


def test_error_instance_carries_exact_owner_repair_without_guessing() -> None:
    error = DatasetConstructionError(
        expected="same-Session Dataset",
        received="foreign Dataset",
        repair="Reconstruct inputs in one Session.",
        location="compare",
    )
    text = render_help_text(error)[0]
    assert error.expected in text
    assert error.received in text
    assert error.location in text
    assert error.repair.action in text
    assert "0x" not in text
    cross_surface = AnalysisError(
        message="repair source",
        repair=AnalysisRepair(
            kind="inspect",
            action="Inspect this source.",
            help_target=LiveHelpTarget(surface="datasource", canonical_id="inspect"),
        ),
    )
    assert 'marivo.help("datasource.inspect")' in render_help_text(cross_surface)[0]


@pytest.mark.parametrize(
    "target,expected",
    (
        ("analysis.metric_dataset.metrc", "analysis.metric_dataset.metric"),
        ("analysis.session.resum", "analysis.session.resume"),
    ),
)
def test_analysis_typo_repair_preserves_closest_registered_leaf(target: str, expected: str) -> None:
    with pytest.raises(MarivoHelpTargetError) as captured:
        marivo.help(target)
    assert captured.value.candidates[0] == expected
    for candidate in captured.value.candidates:
        render_help_text(candidate)
