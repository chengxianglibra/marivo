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
    "session.frame",
    "session.persisted_frame",
    "session.population_from",
    "session.observe",
    "analysis_frame.attribute",
    "frame_method_facts",
    "frame_method_facts.show",
    "session.read",
    "session.values",
    "analysis_frame",
    "analysis_frame.select",
    "analysis_frame.where",
    "analysis_frame.top",
    "analysis_frame.reduce",
    "analysis_frame.align",
    "analysis_frame.compare",
    "analysis_frame.compute",
    "analysis_frame.persist",
    "analysis_frame.to_pandas",
    "population",
    "column",
    "grouping",
    "coordinates",
    "membership",
    "product",
    "in_population",
    "all_history",
    "since",
    "to_date",
    "trailing_duration",
    "trailing_periods",
    "session",
    "analysis_frame.show",
    "analysis_frame.findings",
    "analysis_frame.evidence",
}


def test_exact_export_bindings_and_required_native_targets() -> None:
    disclosure = prepare()
    actual = {e.name: e for p in disclosure.providers for e in p.exports}
    assert set(actual) == set(ANALYSIS_PUBLIC)
    assert set(disclosure.canonical_ids()) >= REQUIRED_TARGETS
    attribute = disclosure.resolve("analysis_frame.artifact_id")
    assert attribute.kind == "attribute" and attribute.type_name == "analysis_frame"
    assert tuple(mv.__all__) == ANALYSIS_PUBLIC
    for name, entry in actual.items():
        resolved = disclosure.resolve(
            entry.implementation if callable(entry.implementation) else entry.target
        )
        assert (resolved.canonical_id or resolved.type_name) == entry.target


def test_population_ir_fields_are_private_help_targets() -> None:
    registry = prepare()
    for target in (
        "population.definition",
        "population_predicate",
        "population_predicate.condition",
        "population_predicate.show",
    ):
        with pytest.raises(HelpTargetError):
            registry.resolve(target)
    population = registry.resolve("population").descriptor
    assert isinstance(population, TypeInput)
    assert tuple(field.name for field in population.bindings[0].fields) == ("axes",)
    with pytest.raises(HelpTargetError):
        registry.resolve("population.memberships")


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
    "target", ("analysis_frame.no_such_field", "unknown.method_facts", "analysis_frame._backing")
)
def test_type_attribute_navigation_rejects_unknown_or_private_links(target: str) -> None:
    registry = prepare()
    providers = list(registry.providers)
    index, provider = next(
        (i, provider)
        for i, provider in enumerate(providers)
        if any(
            isinstance(value, TypeInput) and value.canonical_id == "analysis_frame"
            for value in provider.descriptors
        )
    )
    descriptors = tuple(
        replace(value, consumers=(*value.consumers, target))
        if isinstance(value, TypeInput) and value.canonical_id == "analysis_frame"
        else value
        for value in provider.descriptors
    )
    providers[index] = replace(provider, descriptors=descriptors)
    with pytest.raises(DatasetRegistrationError):
        assemble(tuple(providers))


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


def test_top_discloses_proof_based_uniqueness_without_public_proof_api() -> None:
    registry = prepare()
    text = render(registry, "analysis_frame.top")
    assert "Proven complete input keys omit duplicate scans" in text
    assert "unknown keys" in text and "NULL" in text
    assert "unique_keys" not in mv.__all__
    with pytest.raises(HelpTargetError):
        registry.resolve("unique_keys")
