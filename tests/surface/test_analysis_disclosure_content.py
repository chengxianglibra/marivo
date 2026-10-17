"""Independent semantic, example and family-projection disclosure regressions."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

import marivo.analysis as mv
from marivo._help.render import render_help_text
from marivo.analysis._capabilities.dataset_model import CallableInput, TypeInput
from marivo.analysis._capabilities.dataset_registry import assemble, prepare
from marivo.analysis.datasets.errors import DatasetRegistrationError


def test_parameter_semantics_and_real_producers() -> None:
    registry = prepare()
    for descriptor in registry.descriptors:
        if isinstance(descriptor, TypeInput):
            for producer in descriptor.producers:
                assert render_help_text("analysis." + producer)[0]
            if descriptor.canonical_id != "Population":
                assert "session.members" not in descriptor.producers, descriptor.canonical_id
    for target, producer in (
        ("LogicalNumericRelation", "dsl.LogicalAnalysisDomain.observe"),
        ("LogicalStatisticRelation", "dsl.LogicalNumericRelation.aggregate"),
        ("LogicalSelectedNumericRelation", "dsl.LogicalNumericRelation.where"),
    ):
        descriptor = registry.by_canonical_id(target)
        assert isinstance(descriptor, TypeInput) and producer in descriptor.producers
        callable_descriptor = registry.by_canonical_id(producer)
        assert isinstance(callable_descriptor, CallableInput)
        assert target in str(
            callable_descriptor.bindings[0].implementation.__annotations__["return"]
        )
    for target, producer in (
        ("dsl.NumericField", "LogicalNumericRelation"),
        ("dsl.CategoryField", "LogicalCategoryRelation"),
        ("dsl.BooleanField", "LogicalBooleanRelation"),
        ("dsl.TemporalField", "LogicalTemporalRelation"),
    ):
        descriptor = registry.by_canonical_id(target)
        owner = registry.by_canonical_id(producer)
        assert isinstance(descriptor, TypeInput) and descriptor.producers == (producer,)
        assert isinstance(owner, TypeInput)
        assert any(
            field.name == "value" and target.removeprefix("dsl.") in field.annotation
            for binding in owner.bindings
            for field in binding.fields
        )


def test_family_projection_preserves_every_canonical_member() -> None:
    registry = prepare()
    families: dict[str, list[str]] = {}
    for descriptor in registry.descriptors:
        if isinstance(descriptor, CallableInput) and descriptor.discovery_family:
            families.setdefault(descriptor.discovery_family, []).append(descriptor.canonical_id)
    assert families
    assert {family: set(members) for family, members in families.items()} == {
        "dsl.LogicalNumericRelation.aggregate": {
            "dsl.OriginalContinuation.aggregate",
            "dsl.LogicalNumericRelation.aggregate",
            "dsl.LogicalRatioRelation.aggregate",
        },
        "dsl.LogicalRatioRelation.rollup": {
            "dsl.LogicalRatioRelation.rollup",
        },
    }
    # Correlation receivers already share one native method, not separate variants.
    for receiver in (mv.LogicalNumericRelation,):
        assert (
            registry.by_callable(receiver.correlate).canonical_id
            == "dsl.NumericComparison.correlate"
        )
    for family, members in families.items():
        descriptor = registry.by_canonical_id(family)
        assert isinstance(descriptor, CallableInput) and descriptor.discovery_group
        text, _, _ = render_help_text("analysis." + descriptor.discovery_group)
        targets = re.findall(r"marivo.help\('analysis.([^']+)'\)", text)
        assert all(targets.count(member) == 1 for member in members)


@pytest.mark.parametrize("fault", ("input", "guidance", "family", "missing_family", "syntax"))
def test_registration_rejects_incomplete_example_or_foreign_family(fault: str) -> None:
    registry = prepare()
    providers = list(registry.providers)
    index, provider = next((i, p) for i, p in enumerate(providers) if p.owner == "runtime")
    descriptors = list(provider.descriptors)
    position, descriptor = next(
        (i, d)
        for i, d in enumerate(descriptors)
        if isinstance(d, CallableInput) and d.canonical_id == "dsl.LogicalNumericRelation.aggregate"
    )
    if fault == "input":
        broken = replace(descriptor, example=replace(descriptor.example, requires=()))
    elif fault == "guidance":
        broken = replace(
            descriptor, parameters=(replace(descriptor.parameters[0], acquisition=""),)
        )
    elif fault == "family":
        broken = replace(descriptor, discovery_family="dsl.NumericComparison.correlate")
    elif fault == "missing_family":
        broken = replace(descriptor, discovery_family="dsl.Missing.aggregate")
    else:
        broken = replace(descriptor, example=replace(descriptor.example, code="result = ("))
    descriptors[position] = broken
    providers[index] = replace(provider, descriptors=tuple(descriptors))
    with pytest.raises(DatasetRegistrationError):
        assemble(tuple(providers))
