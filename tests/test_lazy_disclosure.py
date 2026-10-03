"""Independent private disclosure completeness, resolution and no-activation gates."""

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

import marivo
import marivo.analysis as mv
from marivo.analysis._capabilities.dataset_model import (
    CallableInput,
    FamilyInput,
    NavigationInput,
    ParameterInput,
    TypeInput,
)
from marivo.analysis._capabilities.dataset_registry import (
    DatasetDisclosureRegistry,
    assemble,
    prepare,
)
from marivo.analysis._capabilities.dataset_render import render
from marivo.analysis._capabilities.registry import REGISTRY as LIVE_REGISTRY
from marivo.analysis.datasets.base import Dataset
from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.errors import HelpTargetError
from marivo.analysis.materialization.store import SessionStore
from tests.lazy_disclosure_fixtures import example_inputs

# Independently frozen from the accepted cutover plan, not the prepared registry.
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
    "Dataset",
    "LogicalDataset",
    "MaterializedDataset",
    "DatasetShapeId",
    "DatasetFieldId",
    "DatasetFieldIdentity",
    "DatasetPhysicalTypeState",
    "DatasetField",
    "DatasetRowBound",
    "DatasetCardinality",
    "DatasetOrderTerm",
    "DatasetOrdering",
    "DatasetByteCount",
    "DatasetFamilyRowSemantics",
    "DatasetRowContract",
    "DatasetRowSetContract",
    "DatasetSchema",
    "LogicalDatasetState",
    "MaterializedDatasetState",
    "DatasetContract",
    "DatasetFields",
    "DatasetFieldRef",
    "LogicalPopulationDataset",
    "MaterializedPopulationDataset",
    "LogicalMetricDataset",
    "MaterializedMetricDataset",
    "LogicalAssociationDataset",
    "MaterializedAssociationDataset",
    "LogicalForecastDataset",
    "MaterializedForecastDataset",
    "LogicalCandidateDataset",
    "MaterializedCandidateDataset",
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
    "AnalysisPredicate",
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
    "GridWindow",
    "GridEndpoint",
    "time_grid",
    "LogicalTimeAnalysisDomain",
    "MaterializedTimeAnalysisDomain",
    "ArtifactDigest",
    "ArtifactRef",
    "ArtifactRevalidation",
    "ArtifactSummary",
    "EvidenceIntegrityError",
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
    "LogicalSelectedCategoryRelation",
    "MaterializedSelectedCategoryRelation",
    "GroupedAnalysisDomain",
    "GroupedNumericRelation",
    "GroupedRatioRelation",
    "LogicalAssociationResult",
    "MaterializedAssociationResult",
    "RootRoute",
    "RootRoutes",
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
    "eq",
    "not_eq",
    "lt",
    "lte",
    "gt",
    "gte",
    "is_in",
    "is_null",
    "is_not_null",
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

EXPECTED_SHAPES = {
    "population": ("entity-membership",),
    "metric": (
        "entity",
        "entity-dimension",
        "entity-time",
        "entity-dimension-time",
        "scalar",
        "dimension",
        "time",
        "dimension-time",
    ),
    "association": ("entity", "dimension", "time-lag", "dimension-time-lag"),
    "forecast": ("time", "dimension-time"),
    "candidate": (
        "point-anomaly",
        "interesting-window",
        "period-shift",
        "entity-outlier",
        "driver-axis",
    ),
}

# Required leaves from the four owning designs, including the accepted route repair.
REQUIRED_TARGETS = frozenset(
    [
        "population",
        "population.create",
        "filters",
        "eq",
        "not_eq",
        "lt",
        "lte",
        "gt",
        "gte",
        "is_in",
        "is_null",
        "is_not_null",
        "all_of",
        "any_of",
        "not_",
        "AnalysisPredicate",
        "observe",
        "metric_dataset",
        "metric_dataset.with_dimensions",
        "metric_dataset.with_time_axis",
        "metric_dataset.aggregate",
        "metric_dataset.rollup",
        "metric_dataset.metric",
        "metric_dataset.correlate",
        "datasets.rank",
        "datasets.limit",
        "metric_dataset.forecast",
        "ForecastHorizon",
        "ForecastModel",
        "periods",
        "forecast_models",
        "forecast_models.naive",
        "forecast_models.drift",
        "forecast_models.seasonal_naive",
        "window_bucket",
        "WindowBucketAlignment",
        "discovery",
        "discovery.point_anomalies",
        "discovery.interesting_windows",
        "discovery.entity_outliers",
        "events.match",
        "event_matching",
        "event_matching.first_per_subject",
        "event_matching.every_start",
        "lifecycle.replay",
        "dropped_before",
        "in_state",
        "funnel_loss_rate",
        "from_inception",
        "BoundedCompletenessDeclarationV1",
        "SourceOriginCompletenessDeclarationV1",
        "datasets",
        "datasets.dataset",
        "datasets.logical",
        "datasets.materialized",
        "datasets.shape_id",
        "datasets.field_id",
        "datasets.field_identity",
        "datasets.physical_type_state",
        "datasets.field",
        "datasets.row_bound",
        "datasets.cardinality",
        "datasets.order_term",
        "datasets.ordering",
        "datasets.byte_count",
        "datasets.family_row_semantics",
        "datasets.row_contract",
        "datasets.row_set_contract",
        "datasets.schema",
        "datasets.logical_state",
        "datasets.materialized_state",
        "datasets.contract",
        "datasets.fields",
        "datasets.field_ref",
        "datasets.where",
        "actions.execute",
        "actions.show",
        "actions.to_pandas",
        "Session.source_bindings",
        "session.get_or_create",
        "session.current",
        "session.resume",
        "session.recent",
        "session.inspect",
        "session.abandon_run",
        "session.runs",
        "session.get_run",
        "session.artifact",
        "session.graph",
        "session.revalidate",
        "artifact.findings",
        "artifact.finding",
        "runtime_metric.aggregate",
        "runtime_metric.weighted_mean",
        "runtime_metric.slice",
        "runtime_metric.ratio",
        "runtime_metric.linear",
    ]
)


@pytest.fixture(scope="module")
def disclosure() -> DatasetDisclosureRegistry:
    return prepare()


def test_exact_export_bindings_and_required_native_targets(
    disclosure: DatasetDisclosureRegistry,
) -> None:
    actual = {e.name: e for p in disclosure.providers for e in p.exports}
    assert set(actual) == set(EXPECTED_EXPORTS)
    assert len(actual) == 215
    assert set(disclosure.canonical_ids()) >= REQUIRED_TARGETS
    for name in EXPECTED_EXPORTS:
        entry = actual[name]
        if name != "runtime_metric":
            resolved = disclosure.resolve(entry.implementation)
            assert (resolved.canonical_id or resolved.type_name) == entry.target
    for forbidden in (
        "PopulationDataset",
        "EventDataset",
        "events.occurrence_bounds",
        "hypothesis_test",
    ):
        with pytest.raises(HelpTargetError):
            disclosure.resolve(forbidden)


def test_shapes_methods_and_admission_links_are_derived_from_real_owners(
    disclosure: DatasetDisclosureRegistry,
) -> None:
    for fid, shapes in EXPECTED_SHAPES.items():
        family = disclosure.families.get(fid)
        assert tuple(s.local_shape_id for s in family.shape_ids) == shapes
        descriptor = next(
            d
            for d in disclosure.descriptors
            if isinstance(d, FamilyInput) and d.registration.family_id == fid
        )
        for value_type in (family.logical_type, family.materialized_type):
            assert disclosure.resolve(value_type).type_name == descriptor.canonical_id
            for name in dir(value_type):
                if name.startswith("_"):
                    continue
                method = inspect.getattr_static(value_type, name)
                if inspect.isfunction(method):
                    assert isinstance(disclosure.by_callable(method), CallableInput), (fid, name)
        for consumer in family.consumers:
            if consumer.discoverable:
                owners = [
                    d
                    for d in disclosure.descriptors
                    if isinstance(d, CallableInput) and consumer.id in d.registration_ids
                ]
                assert len(owners) == 1, consumer.id


def test_native_help_never_creates_persistent_state(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("static disclosure crossed the persistence boundary")

    monkeypatch.setattr(SessionStore, "__init__", forbidden)
    registry = prepare()
    for descriptor in registry.descriptors:
        render(registry, descriptor.canonical_id)
    assert tuple(mv.__all__) == EXPECTED_EXPORTS
    assert LIVE_REGISTRY.canonical_ids() == registry.canonical_ids()
    assert marivo.help("analysis.metric_dataset.rollup") is None


def test_incomplete_or_duplicate_owner_is_rejected(disclosure: DatasetDisclosureRegistry) -> None:
    with pytest.raises(DatasetRegistrationError):
        assemble(disclosure.families, disclosure.providers[:-1])
    with pytest.raises(DatasetRegistrationError):
        assemble(disclosure.families, disclosure.providers + disclosure.providers[:1])


@pytest.mark.parametrize(
    "fault",
    (
        "parameter",
        "signature",
        "type_fields",
        "variant_fields",
        "duplicate",
        "dangling",
        "missing_consumer",
    ),
)
def test_corrupt_native_input_fails_closed(
    disclosure: DatasetDisclosureRegistry, fault: str
) -> None:
    providers = list(disclosure.providers)
    owner_index = 0
    descriptors = list(providers[owner_index].descriptors)
    if fault in ("parameter", "signature", "missing_consumer"):
        owner_index = 2 if fault == "missing_consumer" else 0
        descriptors = list(providers[owner_index].descriptors)
        i = next(
            i
            for i, d in enumerate(descriptors)
            if isinstance(d, CallableInput)
            and (d.registration_ids if fault == "missing_consumer" else True)
        )
        d = descriptors[i]
        assert isinstance(d, CallableInput)
        if fault == "parameter":
            descriptors[i] = replace(d, parameters=(ParameterInput("nonexistent", "Invalid"),))
        elif fault == "signature":
            descriptors[i] = replace(
                d, bindings=(replace(d.bindings[0], signature=inspect.Signature()),)
            )
        else:
            descriptors[i] = replace(d, registration_ids=())
    elif fault == "type_fields":
        i = next(i for i, d in enumerate(descriptors) if isinstance(d, TypeInput))
        d = descriptors[i]
        assert isinstance(d, TypeInput)
        descriptors[i] = replace(d, bindings=(replace(d.bindings[0], methods=("fabricated",)),))
    elif fault == "variant_fields":
        owner_index = 1
        descriptors = list(providers[owner_index].descriptors)
        d = descriptors[0]
        assert isinstance(d, FamilyInput)
        descriptors[0] = replace(d, variants=(replace(d.variants[0], fields=()),))
    elif fault == "duplicate":
        descriptors.append(descriptors[0])
    else:
        d = descriptors[0]
        assert isinstance(d, TypeInput)
        descriptors[0] = replace(d, producers=("nonexistent",))
    providers[owner_index] = replace(providers[owner_index], descriptors=tuple(descriptors))
    with pytest.raises(DatasetRegistrationError):
        candidate = assemble(disclosure.families, tuple(providers))
        for d in candidate.descriptors:
            render(candidate, d.canonical_id)


def test_registered_nested_values_and_module_resolve(disclosure: DatasetDisclosureRegistry) -> None:
    values = example_inputs(disclosure)
    metric = values["metric"]
    assert isinstance(metric, Dataset)
    for value, target in (
        (values["grain"]("day"), "Grain"),
        (values["window"], "TimeScope"),
        (values["runtime_metric"], "runtime_metric"),
        (metric.row_set_contract.cardinality, "datasets.cardinality"),
        (metric.schema.columns[0].physical_type_state, "datasets.physical_type_state"),
    ):
        resolved = disclosure.resolve(value)
        assert (resolved.canonical_id or resolved.type_name) == target
        assert render(disclosure, value) == render(disclosure, target)


# Frozen directly from the owning row-semantics declarations, independently of providers.
EXPECTED_VARIANT_FIELDS = {
    "_CompleteFromSchema": ("kind",),
    "EntityPresentMetricSemantics": (
        "coordinate_semantics",
        "fold_authority",
        "kind",
        "metric_bindings",
    ),
    "EntityReducedMetricSemantics": (
        "coordinate_semantics",
        "fold_authority",
        "kind",
        "metric_bindings",
        "reduced_entity_ref",
        "reduced_identity_signature",
    ),
    "AssociationSemantics": (
        "approximations",
        "fold_authority",
        "input_shape",
        "kind",
        "lag_offsets",
        "method",
        "metric_keys",
        "metric_units",
    ),
    "ForecastSemantics": (
        "approximation",
        "assumption_contract",
        "fold_authority",
        "horizon",
        "interval_level",
        "interval_method",
        "kind",
        "metric_key",
        "metric_unit",
        "model_id",
        "season_length",
    ),
    "CandidateSemantics": (
        "approximation",
        "item_id_field_id",
        "kind",
        "method_id",
        "objective",
        "reason_codes_field_id",
        "score_field_id",
    ),
}


def test_complete_family_variant_fields_are_pinned(disclosure: DatasetDisclosureRegistry) -> None:
    actual = {
        variant.implementation.__name__: tuple(f.name for f in variant.fields)
        for descriptor in disclosure.descriptors
        if isinstance(descriptor, FamilyInput)
        for variant in descriptor.variants
    }
    assert actual == EXPECTED_VARIANT_FIELDS


def test_expected_parameter_acquisition_and_default_contracts(
    disclosure: DatasetDisclosureRegistry,
) -> None:
    expected = {
        "observe": ("metrics", "population", "time_scope", "time_dimension"),
        "actions.execute": (),
        "datasets.rank": ("by", "order", "ties", "partition_by"),
        "metric_dataset.rollup": ("drop_dimensions", "grain", "drop_time"),
        "events.match": (
            "pattern",
            "population",
            "cohort_window",
            "completion_through",
            "matching",
            "business_order",
            "completeness",
        ),
        "lifecycle.replay": ("model", "population", "window", "seed", "completeness"),
    }
    for target, parameters in expected.items():
        descriptor = disclosure.by_canonical_id(target)
        assert isinstance(descriptor, CallableInput)
        assert tuple(p.name for p in descriptor.parameters) == parameters
    rank = disclosure.by_canonical_id("datasets.rank")
    assert isinstance(rank, CallableInput)
    for binding in rank.bindings:
        assert binding.signature.parameters["order"].default == "descending"
        assert binding.signature.parameters["ties"].default == "ordinal"
        assert binding.signature.parameters["partition_by"].default == ()


def test_retained_catalog_inputs_are_original_native_descriptors(
    disclosure: DatasetDisclosureRegistry,
) -> None:
    expected = (
        "catalog.domains",
        "catalog.datasources",
        "catalog.entities",
        "catalog.dimensions",
        "catalog.time_dimensions",
        "catalog.measures",
        "catalog.metrics",
        "catalog.relationships",
        "catalog.events",
        "catalog.business_orders",
        "catalog.state_models",
        "catalog.period_calendars",
        "catalog.temporal_sets",
        "catalog.work_schedules",
        "catalog.require",
        "catalog.readiness",
        "catalog.period_calendars.grain",
        "catalog.period_calendars.period",
        "catalog.period_calendars.period_on",
        "catalog.period_calendars.periods",
        "catalog.temporal_sets.occurrence",
        "catalog.temporal_sets.occurrences",
    )
    inputs = disclosure.retained_catalog_inputs
    assert tuple(d.id for d in inputs) == expected
    for descriptor in inputs:
        assert descriptor is LIVE_REGISTRY.by_canonical_id(descriptor.id)


def test_foreign_method_binding_with_identical_signature_is_rejected(
    disclosure: DatasetDisclosureRegistry,
) -> None:
    providers = list(disclosure.providers)
    index = 2
    descriptors = list(providers[index].descriptors)
    i = next(
        i
        for i, d in enumerate(descriptors)
        if isinstance(d, CallableInput) and d.canonical_id == "datasets.limit"
    )
    descriptor = descriptors[i]
    assert isinstance(descriptor, CallableInput)
    binding = descriptor.bindings[0]

    def limit(self, count):
        raise AssertionError("A fake callable must never be accepted as native evidence")

    limit.__signature__ = binding.signature
    descriptors[i] = replace(descriptor, bindings=(replace(binding, implementation=limit),))
    providers[index] = replace(providers[index], descriptors=tuple(descriptors))
    with pytest.raises(DatasetRegistrationError, match="receiver-owned"):
        assemble(disclosure.families, tuple(providers))


def test_owner_discovery_memberships_match_independent_contract(
    disclosure: DatasetDisclosureRegistry,
) -> None:
    expected = {
        "filters": (
            "eq",
            "not_eq",
            "lt",
            "lte",
            "gt",
            "gte",
            "is_in",
            "is_null",
            "is_not_null",
            "all_of",
            "any_of",
            "not_",
        ),
        "session.namespace": (
            "session.get_or_create",
            "session.current",
            "session.resume",
            "session.recent",
            "session.inspect",
            "session.abandon_run",
        ),
    }
    for group, members in expected.items():
        page = disclosure.by_canonical_id(group)
        assert isinstance(page, NavigationInput)
        assert page.members == members
        assert (
            tuple(
                d.canonical_id
                for d in disclosure.descriptors
                if isinstance(d, CallableInput) and d.discovery_group == group
            )
            == members
        )


def test_lookup_miss_protocol_is_adapted_to_structured_resolve_error(
    disclosure: DatasetDisclosureRegistry,
) -> None:
    def unregistered() -> None:
        pass

    with pytest.raises(KeyError):
        disclosure.by_canonical_id("nonexistent")
    with pytest.raises(KeyError):
        disclosure.by_callable(unregistered)
    for target in ("nonexistent", unregistered, object()):
        with pytest.raises(HelpTargetError) as caught:
            disclosure.resolve(target)
        assert caught.value.location == "marivo.help.target"
        assert caught.value.expected and caught.value.received
        assert caught.value.repair is not None
        assert caught.value.repair.candidates
        for candidate in caught.value.repair.candidates:
            disclosure.resolve(candidate)


@pytest.mark.parametrize("fault", ("missing_default", "duplicate_default", "dangling_export"))
def test_invalid_callable_ownership_and_export_links_fail_during_assembly(
    disclosure: DatasetDisclosureRegistry, fault: str
) -> None:
    providers = list(disclosure.providers)
    if fault == "dangling_export":
        p = providers[0]
        providers[0] = replace(
            p, exports=(replace(p.exports[0], target="nonexistent"), *p.exports[1:])
        )
    else:
        index = next(i for i, provider in enumerate(providers) if provider.owner == "operators")
        p = providers[index]
        original = next(
            d
            for d in p.descriptors
            if isinstance(d, CallableInput) and d.canonical_id == "metric_dataset.correlate"
        )
        providers[index] = replace(
            p,
            descriptors=(
                *tuple(
                    replace(d, unbound_default=fault == "duplicate_default") if d is original else d
                    for d in p.descriptors
                ),
                replace(
                    original,
                    canonical_id="fault.second_correlate",
                    unbound_default=fault == "duplicate_default",
                ),
            ),
        )
    with pytest.raises(DatasetRegistrationError):
        assemble(disclosure.families, tuple(providers))


@pytest.mark.parametrize("name", ["sum", "count", "count_defined", "min", "max", "mean"])
def test_row_factory_help_has_its_own_executable_example(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    marivo.help(f"analysis.dsl.{name}")
    rendered = capsys.readouterr().out
    assert f"result = mv.{name}()" in rendered
    assert "mv.routes(" not in rendered


def test_statistic_docstring_and_count_descriptor_acquisition(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert mv.MaterializedStatisticRelation.__doc__
    assert "current-row statistic" in mv.MaterializedStatisticRelation.__doc__
    marivo.help("analysis.CountMethod")
    rendered = capsys.readouterr().out
    assert "Call mv.count() or mv.count_defined()." in rendered
    assert "mv.sum()" not in rendered
    assert "dsl.sum" not in rendered
