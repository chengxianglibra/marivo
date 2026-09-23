"""Native analysis error and invalid-target resolution remains deterministic."""

import types

import pytest

import marivo
from marivo._help.model import MarivoHelpTargetError, NativeHelpRoute
from marivo._help.route import route_help_target


@pytest.mark.parametrize("target", [123, [], {}, object(), types.ModuleType("unregistered")])
def test_invalid_objects_have_bounded_resolvable_repairs(target: object) -> None:
    with pytest.raises(MarivoHelpTargetError) as captured:
        marivo.help(target)
    error = captured.value
    assert len(error.candidates) <= 5
    for candidate in error.candidates:
        assert isinstance(route_help_target(candidate), NativeHelpRoute)
    with pytest.raises(MarivoHelpTargetError) as repeated:
        marivo.help(target)
    assert repeated.value.candidates == error.candidates


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
        assert isinstance(route_help_target(candidate), NativeHelpRoute)
