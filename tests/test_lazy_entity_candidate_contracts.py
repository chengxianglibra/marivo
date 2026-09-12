"""Pure Entity Candidate admission, identity retention and scalar continuations."""

from dataclasses import FrozenInstanceError, replace

import pytest

import marivo.analysis as mv
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.fold_contracts import decode_fold_authority
from marivo.analysis.observation.predicates import eq, gt
from marivo.analysis.operators.candidate_contracts import (
    CandidatePayload,
    CandidateSemantics,
    EntityCandidateEvaluationSummary,
)
from marivo.analysis.operators.candidate_dataset import LogicalCandidateDataset
from marivo.analysis.operators.discovery import (
    candidate_filterable_field,
    validate_candidate,
    validate_definition,
)
from marivo.analysis.operators.errors import CandidateError
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from tests.lazy_observation_fixtures import (
    NoIoActionPort,
    make_semantic_registry,
    make_sources,
)

REVENUE = ref.metric("sales.revenue")


@pytest.mark.parametrize("composite", [False, True])
def test_entity_schema_preserves_exact_identity_and_order(composite: bool) -> None:
    registry, sidecar = make_semantic_registry()
    if composite:
        entity = registry.entities["sales.orders"]
        registry = replace(
            registry,
            entities={
                **registry.entities,
                "sales.orders": replace(entity, primary_key=("tenant", "id")),
            },
        )
        registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        session_id="entity-candidate-contract",
        store_id="entity-candidate-contract",
        action_port=NoIoActionPort(),
    )
    metric = sources.observe(REVENUE)
    candidate = metric.discover.entity_outliers()
    fields = {f.name: f for f in candidate.schema.columns}
    assert tuple(fields) == (
        "item_id",
        "score",
        "reason_codes",
        "entity_identity",
        "observed_value",
        "baseline_value",
        "signed_deviation",
        "scale_method",
        "direction",
    )
    assert str(candidate.row_contract.shape_id) == "candidate/entity-outlier@v1"
    identity = fields["entity_identity"]
    assert identity is metric.schema.columns[0]
    assert isinstance(identity.identity, d._EntityFieldIdentity)
    assert identity.identity.identity_signature == (
        (("tenant", "string"), ("id", "int64")) if composite else (("id", "int64"),)
    )
    assert candidate.row_contract.coordinate_field_ids == (identity.field_id,)
    assert candidate.row_contract.key_field_ids == (identity.field_id,)
    ordering = candidate.row_set_contract.ordering
    assert isinstance(ordering, d._OrderedOrdering)
    assert tuple(
        (term.field_id, term.direction, term.value_order_contract_id) for term in ordering.terms
    ) == (
        (fields["score"].field_id, "descending", "observation.scalar_order@v1"),
        (identity.field_id, "ascending", "observation.identity_tuple@v1"),
        (fields["item_id"].field_id, "ascending", "observation.scalar_order@v1"),
    )
    for name, field in fields.items():
        assert candidate_filterable_field(field) == (
            name not in ("entity_identity", "reason_codes")
        )
        if name != "entity_identity":
            assert field.field_id.value == f"generated.discover.entity_outliers.{name}@v1"
            assert not field.nullable
    assert isinstance(candidate.row_contract.family_semantics, CandidateSemantics)
    assert candidate.row_contract.family_semantics.method_id == "entity_mad@v1"
    assert isinstance(candidate.row_set_contract.cardinality, d._KeyedCardinality)
    assert isinstance(candidate.row_set_contract.cardinality.row_bound, d._StaticRowBound)
    assert candidate.row_set_contract.cardinality.row_bound.max_rows == 50


@pytest.mark.parametrize("name", ["revenue", "mean_amount", "weighted_amount"])
def test_entity_definition_is_temporal_neutral_and_pure(name: str) -> None:
    metric = make_sources().observe(ref.metric(f"sales.{name}"))
    namespace = metric.discover
    candidate = namespace.entity_outliers()
    assert not callable(namespace)
    assert not hasattr(metric, "entity_outliers")
    assert candidate._inputs == (metric,)
    assert isinstance(candidate._root, LogicalRootHandle)
    assert isinstance(candidate._root.payload, CandidatePayload)
    spec = candidate._root.payload.spec
    assert spec.metric_name == name and spec.dimensions == ()
    assert spec.definition.objective == "entity_outliers"
    assert spec.definition.method_id == "entity_mad@v1"
    assert spec.definition.input_authority == metric.definition_fingerprint
    assert spec.definition.input_state_kind == "logical"
    assert spec.definition.metric_key == f"metric:sales.{name}"
    assert spec.definition.threshold == 3.0 and spec.definition.limit == 50
    assert spec.definition.baseline_fold_authority is None
    authority = decode_fold_authority(spec.definition.fold_authority)
    assert authority.time_scope() is None and authority.time_grain() is None
    assert namespace.entity_outliers().definition_fingerprint == candidate.definition_fingerprint
    assert (
        namespace.entity_outliers(threshold=2).definition_fingerprint
        == namespace.entity_outliers(threshold=2.0).definition_fingerprint
    )
    assert (
        len(
            {
                candidate.definition_fingerprint,
                namespace.entity_outliers(threshold=2).definition_fingerprint,
                namespace.entity_outliers(limit=20).definition_fingerprint,
            }
        )
        == 3
    )
    disclosure = candidate.contract().render()
    assert "median" in disclosure and "1.4826*MAD" in disclosure
    assert "mean absolute deviation" in disclosure
    assert "population mean/stddev" not in disclosure
    exports = mv.__all__
    assert isinstance(exports, (list, tuple))
    for name in (
        "LogicalCandidateDataset",
        "MaterializedCandidateDataset",
    ):
        assert name in exports and hasattr(mv, name)
    assert "EntityCandidateEvaluationSummary" not in exports and not hasattr(
        mv, "EntityCandidateEvaluationSummary"
    )
    assert "MetricDiscovery" not in exports and not hasattr(mv, "MetricDiscovery")


def test_entity_discovery_rejects_other_shapes_and_metric_arity() -> None:
    sources = make_sources()
    metric = sources.observe(REVENUE)
    dimension = ref.dimension("sales.orders.channel")
    day = ref.time_dimension("sales.orders.order_time")
    for invalid in (
        metric.aggregate(),
        metric.with_dimensions(dimension),
        metric.with_time_axis(day, grain=mv.grain("day")),
        metric.with_dimensions(dimension).with_time_axis(day, grain=mv.grain("day")),
        metric.with_dimensions(dimension).aggregate(),
        metric.with_time_axis(day, grain=mv.grain("day")).aggregate(),
        sources.observe([REVENUE, ref.metric("sales.order_count")]),
    ):
        with pytest.raises(CandidateError):
            invalid.discover.entity_outliers()
        assert ".discover.entity_outliers(" not in invalid.contract().render()
    assert "discover.entity_outliers" in metric.contract().render()


@pytest.mark.parametrize("threshold", [True, 0, -1.0, float("nan"), float("inf"), 10**1000])
def test_entity_threshold_validation_is_source_free(threshold: float) -> None:
    with pytest.raises(CandidateError):
        make_sources().observe(REVENUE).discover.entity_outliers(threshold=threshold)


@pytest.mark.parametrize("limit", [True, 0, -1, 1001, 1.5, "3", None])
def test_entity_limit_validation_is_source_free(limit: int) -> None:
    with pytest.raises(CandidateError):
        make_sources().observe(REVENUE).discover.entity_outliers(limit=limit)


def test_entity_retained_selection_rank_and_identity_are_closed() -> None:
    candidate = make_sources().observe(REVENUE).discover.entity_outliers()
    selected = candidate.where(
        gt(candidate.fields.get("score"), 1),
        eq(candidate.fields.get("scale_method"), "mad"),
        eq(candidate.fields.get("direction"), "high"),
    )
    ranked = selected.rank(selected.fields.get("score")).limit(2)
    assert isinstance(ranked, LogicalCandidateDataset)
    assert ranked.row_contract.family_semantics == candidate.row_contract.family_semantics
    assert ranked.row_contract.key_field_ids == candidate.row_contract.key_field_ids
    assert ranked.schema.columns[3] == candidate.schema.columns[3]
    assert "median" in ranked.contract().render()
    for field in ("entity_identity", "reason_codes"):
        with pytest.raises(CandidateError):
            candidate.where(eq(candidate.fields.get(field), "forbidden"))
    differently_ranked = candidate.rank(candidate.fields.get("score"), order="ascending")
    with pytest.raises(DatasetConstructionError):
        differently_ranked.where(gt(ranked.fields.get("rank"), 1))
    foreign = make_sources(session_id="other").observe(REVENUE).discover.entity_outliers()
    with pytest.raises(DatasetConstructionError):
        candidate.where(gt(foreign.fields.get("score"), 1))
    with pytest.raises(DatasetConstructionError):
        candidate.rank(candidate.fields.get("entity_identity"))
    with pytest.raises(DatasetConstructionError):
        ranked.rank(ranked.fields.get("score"))


def test_entity_corrupt_identity_and_temporal_definition_are_rejected() -> None:
    candidate = make_sources().observe(REVENUE).discover.entity_outliers()
    row = candidate.row_contract
    identity = row.schema.columns[3]
    for bad_identity in (
        replace(identity, _token=d._CORE_TOKEN, nullable=True),
        replace(identity, _token=d._CORE_TOKEN, role_id="effect_value"),
        replace(identity, _token=d._CORE_TOKEN, derivation_identity="changed@v1"),
    ):
        columns = tuple(
            bad_identity if f.name == "entity_identity" else f for f in row.schema.columns
        )
        with pytest.raises(CandidateError):
            validate_candidate(
                replace(row, _token=d._CORE_TOKEN, schema=d._make_schema(columns)),
                candidate.row_set_contract,
            )
    assert isinstance(candidate._root, LogicalRootHandle)
    assert isinstance(candidate._root.payload, CandidatePayload)
    definition = candidate._root.payload.spec.definition
    temporal = decode_fold_authority(definition.fold_authority).model_copy(
        update={"grain": ("builtin", "day", "1")}
    )
    for invalid in (
        replace(definition, fold_authority=temporal.to_json()),
        replace(definition, baseline_fold_authority=definition.fold_authority),
        replace(definition, method_id="point_zscore@v1"),
    ):
        with pytest.raises(CandidateError):
            validate_definition(invalid)


def test_entity_evaluation_summary_has_only_frozen_cohort_facts() -> None:
    summary = EntityCandidateEvaluationSummary(
        input_row_count=5,
        non_null_value_count=4,
        null_value_count=1,
        center=1.0,
        scale=2.25,
        scale_method="mean_absolute_deviation",
        pre_limit_candidate_count=1,
        emitted_candidate_count=1,
        score_range=(4.0, 4.0),
        reason_counts=(("entity_mad_threshold_met", 1),),
    )
    assert summary.center == 1.0 and summary.scale == 2.25
    with pytest.raises(FrozenInstanceError):
        attribute = "center"
        setattr(summary, attribute, 2.0)
    assert not hasattr(summary, "entity_identity")
    assert not hasattr(summary, "series_count")
