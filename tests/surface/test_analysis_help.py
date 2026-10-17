"""Native analysis error and invalid-target resolution remains deterministic."""

import types

import pytest

import marivo
from marivo._help.model import MarivoHelpTargetError, NativeHelpRoute
from marivo._help.render import help as public_help
from marivo._help.route import route_help_target

assert marivo.help is public_help


@pytest.mark.parametrize("target", [123, [], {}, object(), types.ModuleType("unregistered")])
def test_invalid_objects_have_bounded_resolvable_repairs(target: object) -> None:
    with pytest.raises(MarivoHelpTargetError) as captured:
        public_help(target)  # type: ignore[arg-type]  # Deliberately invalid runtime Help target.
    error = captured.value
    assert len(error.candidates) <= 5
    for candidate in error.candidates:
        assert isinstance(route_help_target(candidate), NativeHelpRoute)
    with pytest.raises(MarivoHelpTargetError) as repeated:
        public_help(target)  # type: ignore[arg-type]  # Deliberately invalid runtime Help target.
    assert repeated.value.candidates == error.candidates


@pytest.mark.parametrize(
    "target,expected",
    (
        ("analysis.session.memebrs", "analysis.session.members"),
        ("analysis.session.resum", "analysis.session.resume"),
        ("analysis.Population.wher", "analysis.Population.where"),
    ),
)
def test_analysis_typo_repair_preserves_closest_registered_leaf(target: str, expected: str) -> None:
    with pytest.raises(MarivoHelpTargetError) as captured:
        public_help(target)
    assert captured.value.candidates[0] == expected
    for candidate in captured.value.candidates:
        assert isinstance(route_help_target(candidate), NativeHelpRoute)


def test_unqualified_receiver_method_remains_unregistered() -> None:
    with pytest.raises(MarivoHelpTargetError) as captured:
        public_help("analysis.where")
    error = captured.value
    assert "marivo.help(value.method)" in str(error)
    assert sum(target.endswith(".where") for target in error.candidates) >= 2
    assert all("Materialized" not in target for target in error.candidates)
    for candidate in error.candidates:
        assert isinstance(route_help_target(candidate), NativeHelpRoute)


@pytest.mark.parametrize("name", ("instant", "time_scope"))
def test_time_construction_help_has_only_session_entry(name: str) -> None:
    with pytest.raises(MarivoHelpTargetError):
        public_help("analysis." + name)
    assert isinstance(route_help_target("analysis.session." + name), NativeHelpRoute)
