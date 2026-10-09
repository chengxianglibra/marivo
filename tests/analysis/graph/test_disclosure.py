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

EXPECTED_EXPORTS = (
    "AnyAnchor",
    "EveryAnchor",
    "any_anchor",
    "every_anchor",
    "LogicalRetentionResult",
    "MaterializedRetentionResult",
    "LogicalSubjectRetentionResult",
    "MaterializedSubjectRetentionResult",
    "Duration",
    "ElapsedWindow",
    "CalendarWindow",
    "duration",
    "elapsed",
    "calendar_days",
    "LogicalAnchorDomain",
    "MaterializedAnchorDomain",
    "LogicalAttributionResult",
    "MaterializedAttributionResult",
    "LogicalRankingResult",
    "MaterializedRankingResult",
    "LogicalTable",
    "MaterializedTable",
    "table",
    "ReferenceWeights",
    "reference_weights",
    "SubjectBinding",
    "AnyInstance",
    "AtLeast",
    "AllInstances",
    "EmptyOpportunityPolicy",
    "empty_opportunity",
    "any_instance",
    "at_least",
    "all_instances",
    "DatasetByteCount",
    "MaterializedDatasetState",
    "LogicalHistoryResult",
    "LogicalStateDistributionResult",
    "MaterializedStateDistributionResult",
    "LogicalTransitionSummary",
    "MaterializedTransitionSummary",
    "LogicalDwellSummary",
    "MaterializedDwellSummary",
    "LogicalViolationResult",
    "MaterializedViolationResult",
    "LogicalStateIntervalResult",
    "MaterializedStateIntervalResult",
    "MaterializedHistoryResult",
    "ForecastHorizon",
    "ForecastModel",
    "WindowBucketAlignment",
    "BoundedCompletenessDeclarationV1",
    "SourceOriginCompletenessDeclarationV1",
    "DroppedBefore",
    "EventPattern",
    "EveryStart",
    "FirstPerSubject",
    "FromInception",
    "FunnelLossRate",
    "Grain",
    "InState",
    "PatternStep",
    "TimeScope",
    "BeforeEndBoundary",
    "TimeGrid",
    "GridEndpoint",
    "time_grid",
    "ArtifactDigest",
    "ArtifactRef",
    "ArtifactSummary",
    "FailedRun",
    "Finding",
    "FindingPage",
    "IncompleteRun",
    "RunPage",
    "SessionGraph",
    "SucceededRun",
    "Session",
    "PeriodChange",
    "UnionKeys",
    "ExactKeys",
    "TimeChange",
    "CohortContrast",
    "OneToOneCorrespondence",
    "one_to_one",
    "AnalysisAction",
    "AnalysisContract",
    "LogicalAnalysisDomain",
    "LogicalFunnelResult",
    "MaterializedFunnelResult",
    "LogicalFunnelComparisonResult",
    "MaterializedFunnelComparisonResult",
    "LogicalJourneyResult",
    "MaterializedJourneyResult",
    "LogicalEventDurationResult",
    "MaterializedEventDurationResult",
    "LogicalCompletedJourneys",
    "MaterializedCompletedJourneys",
    "MaterializedAnalysisDomain",
    "LogicalCategoryRelation",
    "LogicalBooleanRelation",
    "MaterializedBooleanRelation",
    "LogicalTemporalRelation",
    "MaterializedTemporalRelation",
    "LogicalSelectedBooleanRelation",
    "MaterializedSelectedBooleanRelation",
    "LogicalSelectedTemporalRelation",
    "MaterializedSelectedTemporalRelation",
    "LogicalSelectedNumericRelation",
    "MaterializedSelectedNumericRelation",
    "MaterializedCategoryRelation",
    "LogicalNumericRelation",
    "MaterializedNumericRelation",
    "MaterializedGroupedNumericRelation",
    "LogicalRolledNumericRelation",
    "MaterializedRolledNumericRelation",
    "LogicalRolledRatioRelation",
    "MaterializedRolledRatioRelation",
    "LogicalRatioRelation",
    "MaterializedRatioRelation",
    "LogicalDifferenceRelation",
    "MaterializedDifferenceRelation",
    "LogicalSelectedDifferenceRelation",
    "MaterializedSelectedDifferenceRelation",
    "LogicalStatisticRelation",
    "MaterializedStatisticRelation",
    "MaterializedCoefficientRelation",
    "LogicalCoefficientSelectionRelation",
    "MaterializedCoefficientSelectionRelation",
    "LogicalFixedAnalysisDomain",
    "GroupedNumericRelation",
    "GroupedRatioRelation",
    "LogicalTimeRunResult",
    "MaterializedTimeRunResult",
    "LogicalDeviationResult",
    "MaterializedDeviationResult",
    "LogicalForecastResult",
    "MaterializedForecastResult",
    "LogicalCoefficientRelation",
    "LogicalAssociationResult",
    "MaterializedAssociationResult",
    "RootRoute",
    "RootRoutes",
    "MemberAxis",
    "member",
    "RowMethod",
    "CountMethod",
    "GroupedStatisticRelation",
    "route",
    "routes",
    "sum",
    "count",
    "mean",
    "min",
    "max",
    "count_defined",
    "all_of",
    "any_of",
    "not_",
    "grain",
    "time_scope",
    "window_bucket",
    "step",
    "sequence",
    "first_per_subject",
    "every_start",
    "dropped_before",
    "in_state",
    "funnel_loss_rate",
    "from_inception",
    "periods",
    "naive",
    "drift",
    "seasonal_naive",
    "runtime_metric",
    "session",
)
REQUIRED_TARGETS = {
    "session.members",
    "Session",
    "LogicalAnalysisDomain",
    "MaterializedNumericRelation",
    "actions.execute",
    "actions.show",
    "actions.to_pandas",
    "artifact.findings",
    "artifact.finding",
}


def test_exact_export_bindings_and_required_native_targets() -> None:
    disclosure = prepare()
    actual = {e.name: e for p in disclosure.providers for e in p.exports}
    assert set(actual) == set(EXPECTED_EXPORTS)
    assert set(disclosure.canonical_ids()) >= REQUIRED_TARGETS
    assert tuple(mv.__all__) == EXPECTED_EXPORTS
    for name, entry in actual.items():
        if name != "runtime_metric":
            resolved = disclosure.resolve(entry.implementation)
            assert (resolved.canonical_id or resolved.type_name) == entry.target


def test_shapes_methods_and_admission_links_are_derived_from_real_owners() -> None:
    registry = prepare()
    for descriptor in registry.descriptors:
        if isinstance(descriptor, TypeInput):
            for binding in descriptor.bindings:
                if binding.implementation.__module__ == "marivo.analysis.public_dsl":
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
        assert "session.population(" not in text and "session.observe(" not in text


def test_incomplete_or_duplicate_owner_is_rejected() -> None:
    registry = prepare()
    for providers in (registry.providers[:-1], registry.providers + registry.providers[:1]):
        with pytest.raises(DatasetRegistrationError):
            assemble(providers)


@pytest.mark.parametrize(
    "fault",
    ("parameter", "signature", "type_fields", "variant_fields", "duplicate", "dangling", "export"),
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
        i, descriptor = next(
            (i, d) for i, d in enumerate(descriptors) if isinstance(d, TypeInput) and d.variants
        )
        if fault == "type_fields":
            descriptors[i] = replace(
                descriptor, bindings=(replace(descriptor.bindings[0], methods=("fabricated",)),)
            )
        elif fault == "variant_fields":
            descriptors[i] = replace(
                descriptor, variants=(replace(descriptor.variants[0], fields=()),)
            )
        elif fault == "duplicate":
            descriptors.append(descriptor)
        elif fault == "dangling":
            descriptors[i] = replace(descriptor, producers=("nonexistent",))
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

    def members(self, entity, *, at=None):
        raise AssertionError("foreign callable accepted")

    members.__signature__ = binding.signature
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
