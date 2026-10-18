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


def test_every_advertised_native_member_has_exact_help() -> None:
    registry = prepare()
    for descriptor in registry.descriptors:
        if isinstance(descriptor, TypeInput):
            for binding in descriptor.bindings:
                for name in binding.methods:
                    method = getattr(binding.implementation, name)
                    resolved = registry.by_callable(method)
                    text, _, _ = render_help_text(method)
                    assert text == render_help_text("analysis." + resolved.canonical_id)[0]
    assert (
        render_help_text(mv.ordinal().show)[0]
        == render_help_text("analysis.calendar_correspondence.show")[0]
    )


def test_parameter_semantics_and_real_producers() -> None:
    registry = prepare()
    for descriptor in registry.descriptors:
        if isinstance(descriptor, TypeInput):
            for producer in descriptor.producers:
                assert render_help_text("analysis." + producer)[0]
            if descriptor.canonical_id != "population":
                assert "session.members" not in descriptor.producers, descriptor.canonical_id
    for target, producer in (
        ("query", "session.observe"),
        ("query", "session.read"),
        ("query", "session.values"),
        ("population", "session.members"),
        ("population", "query.population"),
        ("artifact", "query.execute"),
        ("column", "query.__getitem__"),
    ):
        descriptor = registry.by_canonical_id(target)
        assert isinstance(descriptor, TypeInput) and producer in descriptor.producers
        callable_descriptor = registry.by_canonical_id(producer)
        assert isinstance(callable_descriptor, CallableInput)
        assert descriptor.bindings[0].implementation.__name__ in str(
            callable_descriptor.bindings[0].implementation.__annotations__["return"]
        )


def test_family_projection_preserves_every_canonical_member() -> None:
    registry = prepare()
    families: dict[str, list[str]] = {}
    for descriptor in registry.descriptors:
        if isinstance(descriptor, CallableInput) and descriptor.discovery_family:
            families.setdefault(descriptor.discovery_family, []).append(descriptor.canonical_id)
    # Query owns each result operation once, without legacy receiver-family projections.
    assert families == {}
    for owner, methods in (
        (mv.Query, ("select", "where", "reduce", "align", "compare", "rollup", "drill", "share")),
        (mv.Column, ("sum", "mean", "divide", "is_null")),
    ):
        for method in methods:
            target = owner.__name__.lower() + "." + method
            descriptor = registry.by_callable(getattr(owner, method))
            assert isinstance(descriptor, CallableInput)
            assert descriptor.canonical_id == target and descriptor.discovery_group
            text, _, _ = render_help_text("analysis." + descriptor.discovery_group)
            targets = re.findall(r"marivo.help\('analysis.([^']+)'\)", text)
            assert targets.count(target) == 1


@pytest.mark.parametrize("fault", ("input", "guidance", "family", "missing_family", "syntax"))
def test_registration_rejects_incomplete_example_or_foreign_family(fault: str) -> None:
    registry = prepare()
    providers = list(registry.providers)
    index, provider = next(
        (i, p)
        for i, p in enumerate(providers)
        if any(
            isinstance(d, CallableInput) and d.canonical_id == "query.reduce" for d in p.descriptors
        )
    )
    descriptors = list(provider.descriptors)
    position, descriptor = next(
        (i, d)
        for i, d in enumerate(descriptors)
        if isinstance(d, CallableInput) and d.canonical_id == "query.reduce"
    )
    if fault == "input":
        broken = replace(descriptor, example=replace(descriptor.example, requires=()))
    elif fault == "guidance":
        broken = replace(
            descriptor, parameters=(replace(descriptor.parameters[0], acquisition=""),)
        )
    elif fault == "family":
        broken = replace(descriptor, discovery_family="query.compare")
    elif fault == "missing_family":
        broken = replace(descriptor, discovery_family="Missing.reduce")
    else:
        broken = replace(descriptor, example=replace(descriptor.example, code="result = ("))
    descriptors[position] = broken
    providers[index] = replace(provider, descriptors=tuple(descriptors))
    with pytest.raises(DatasetRegistrationError):
        assemble(tuple(providers))
