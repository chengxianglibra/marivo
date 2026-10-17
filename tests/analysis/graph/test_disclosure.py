"""Independent native disclosure ownership, budget and mutation guards."""

import inspect
from dataclasses import replace

import pytest

import marivo.analysis as mv
from marivo.analysis._capabilities.dataset_model import (
    CallableInput,
    ParameterInput,
    TypeInput,
)
from marivo.analysis._capabilities.dataset_registry import assemble, prepare
from marivo.analysis._capabilities.dataset_render import render
from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.errors import HelpTargetError
from marivo.analysis.materialization.store import SessionStore
from tests.surface.exports import ANALYSIS_PUBLIC

REQUIRED_TARGETS = {
    "session.members",
    "session.query",
    "session.observe",
    "session.read",
    "session.values",
    "Query",
    "Query.population",
    "Query.select",
    "Query.where",
    "Query.top",
    "Query.reduce",
    "Query.align",
    "Query.compare",
    "Query.rollup",
    "Query.drill",
    "Query.share",
    "Query.execute",
    "Population",
    "Column",
    "MembershipAxis",
    "MemberAxis",
    "PopulationPredicate",
    "coordinates",
    "identities",
    "membership",
    "product",
    "in_population",
    "all_history",
    "since",
    "to_date",
    "trailing_duration",
    "trailing_periods",
    "Session",
    "Artifact",
    "Artifact.show",
    "Artifact.to_pandas",
    "Artifact.findings",
    "Artifact.evidence_digest",
}


def test_exact_export_bindings_and_required_native_targets() -> None:
    disclosure = prepare()
    actual = {e.name: e for p in disclosure.providers for e in p.exports}
    assert set(actual) == set(ANALYSIS_PUBLIC)
    assert set(disclosure.canonical_ids()) >= REQUIRED_TARGETS
    assert tuple(mv.__all__) == ANALYSIS_PUBLIC
    for name, entry in actual.items():
        resolved = disclosure.resolve(entry.implementation)
        assert (resolved.canonical_id or resolved.type_name) == entry.target


def test_shapes_methods_and_admission_links_are_derived_from_real_owners() -> None:
    registry = prepare()
    for descriptor in registry.descriptors:
        if isinstance(descriptor, TypeInput):
            for binding in descriptor.bindings:
                if binding.implementation.__module__.startswith(
                    ("marivo.analysis.dsl", "marivo.analysis.session")
                ):
                    for name in binding.methods:
                        method = getattr(binding.implementation, name)
                        assert isinstance(registry.by_callable(method), CallableInput)


def test_native_help_never_creates_persistent_state(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("static disclosure created state")

    monkeypatch.setattr(SessionStore, "__init__", forbidden)
    registry = prepare()
    for descriptor in registry.descriptors:
        text = render(registry, descriptor.canonical_id)
        assert len(text) <= 8192
        assert "session.population(" not in text
        assert "LogicalFixedAnalysisDomain" not in text


def test_incomplete_or_duplicate_owner_is_rejected() -> None:
    registry = prepare()
    for providers in (registry.providers[:-1], registry.providers + registry.providers[:1]):
        with pytest.raises(DatasetRegistrationError):
            assemble(providers)


@pytest.mark.parametrize(
    "fault",
    ("signature", "variant_fields", "duplicate", "export"),
)
def test_corrupt_native_input_fails_closed(fault: str) -> None:
    registry = prepare()
    providers = list(registry.providers)
    if fault in {"parameter", "signature"}:
        index, owner = next(
            (i, p)
            for i, p in enumerate(providers)
            if any(isinstance(d, CallableInput) for d in p.descriptors)
        )
        descriptors = list(owner.descriptors)
        i, descriptor = next(
            (i, d) for i, d in enumerate(descriptors) if isinstance(d, CallableInput)
        )
        if fault == "parameter":
            descriptors[i] = replace(
                descriptor, parameters=(ParameterInput("nonexistent", "Invalid"),)
            )
        else:
            descriptors[i] = replace(
                descriptor,
                bindings=(replace(descriptor.bindings[0], signature=inspect.Signature()),),
            )
    else:
        index, owner = next(
            (i, p)
            for i, p in enumerate(providers)
            if any(isinstance(d, TypeInput) and d.variants for d in p.descriptors)
        )
        descriptors = list(owner.descriptors)
        i, type_descriptor = next(
            (i, d) for i, d in enumerate(descriptors) if isinstance(d, TypeInput) and d.variants
        )
        if fault == "type_fields":
            descriptors[i] = replace(
                type_descriptor,
                bindings=(replace(type_descriptor.bindings[0], methods=("fabricated",)),),
            )
        elif fault == "variant_fields":
            descriptors[i] = replace(
                type_descriptor, variants=(replace(type_descriptor.variants[0], fields=()),)
            )
        elif fault == "duplicate":
            descriptors.append(type_descriptor)
        elif fault == "dangling":
            descriptors[i] = replace(type_descriptor, producers=("nonexistent",))
        else:
            providers[index] = replace(
                owner, exports=(replace(owner.exports[0], target="nonexistent"), *owner.exports[1:])
            )
    if fault != "export":
        providers[index] = replace(owner, descriptors=tuple(descriptors))
    with pytest.raises(DatasetRegistrationError):
        assemble(tuple(providers))


def test_foreign_method_binding_with_identical_signature_is_rejected() -> None:
    registry = prepare()
    providers = list(registry.providers)
    index, owner = next(
        (i, p)
        for i, p in enumerate(providers)
        if any(
            isinstance(d, CallableInput) and d.canonical_id == "session.members"
            for d in p.descriptors
        )
    )
    descriptors = list(owner.descriptors)
    i, descriptor = next(
        (i, d)
        for i, d in enumerate(descriptors)
        if isinstance(d, CallableInput) and d.canonical_id == "session.members"
    )
    binding = descriptor.bindings[0]

    def members(self: object, entity: object, *, at: object = None) -> None:
        raise AssertionError("foreign callable accepted")

    attribute = "__signature__"
    setattr(members, attribute, binding.signature)
    descriptors[i] = replace(descriptor, bindings=(replace(binding, implementation=members),))
    providers[index] = replace(owner, descriptors=tuple(descriptors))
    with pytest.raises(DatasetRegistrationError, match="receiver-owned"):
        assemble(tuple(providers))


def test_lookup_miss_protocol_is_adapted_to_structured_resolve_error() -> None:
    registry = prepare()
    for target in ("nonexistent", object()):
        with pytest.raises(HelpTargetError) as caught:
            registry.resolve(target)
        assert caught.value.expected and caught.value.received and caught.value.repair
        for candidate in caught.value.repair.candidates:
            registry.resolve(candidate)
