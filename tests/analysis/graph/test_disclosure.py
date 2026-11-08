"""Independent native disclosure ownership, budget and mutation guards."""

import pytest

import marivo.analysis as mv
from marivo.analysis._capabilities.dataset_model import (
    CallableInput,
    TypeInput,
)
from marivo.analysis._capabilities.dataset_registry import prepare
from marivo.analysis.errors import HelpTargetError
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
    "frame_source_read",
    "frame_source_read_scope",
    "frame_source_read_period",
}


def test_exact_export_bindings_and_required_native_targets() -> None:
    disclosure = prepare()
    actual = {e.name: e for p in disclosure.providers for e in p.exports}
    assert set(actual) == set(ANALYSIS_PUBLIC)
    assert set(disclosure.canonical_ids()) >= REQUIRED_TARGETS
    attribute = disclosure.resolve("analysis_frame.artifact_id")
    assert attribute.kind == "attribute" and attribute.type_name == "analysis_frame"
    reads = disclosure.resolve("analysis_frame.source_reads")
    assert reads.kind == "attribute" and reads.type_name == "analysis_frame"
    assert tuple(mv.__all__) == ANALYSIS_PUBLIC
    for name, entry in actual.items():
        resolved = disclosure.resolve(
            entry.implementation if callable(entry.implementation) else entry.target
        )
        assert (resolved.canonical_id or resolved.type_name) == entry.target


def test_population_ir_fields_are_private_help_targets() -> None:
    registry = prepare()
    population = registry.resolve("population").descriptor
    assert isinstance(population, TypeInput)
    assert tuple(field.name for field in population.bindings[0].fields) == ("axes",)


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


def test_lookup_miss_protocol_is_adapted_to_structured_resolve_error() -> None:
    registry = prepare()
    for target in ("nonexistent", object()):
        with pytest.raises(HelpTargetError) as caught:
            registry.resolve(target)
        assert caught.value.expected and caught.value.received and caught.value.repair
        for candidate in caught.value.repair.candidates:
            registry.resolve(candidate)
