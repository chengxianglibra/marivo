"""Private lazy semantics derive from authored declarations without source access."""

from collections import UserList
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone

import pytest

from marivo.datasource.ir import (
    AiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
    JsonSourceIR,
    SourceParamIR,
)
from marivo.refs import Ref, SemanticKindTag, ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar, ExpressionBody
from marivo.semantic.errors import SemanticLoadError
from marivo.semantic.ir import (
    AggKind,
    DateParse,
    DimensionIR,
    DimensionKind,
    DomainIR,
    EntityIR,
    EntityVersioningIR,
    MeasureIR,
    MetricIR,
    RatioComposition,
    SemiAdditive,
    SnapshotVersioningIR,
    SourceLocation,
    TargetSnapshotSelection,
    TargetValiditySelection,
    TimeFoldIR,
    ValidityVersioningIR,
    WeightedMeanAggregation,
)
from marivo.semantic.metric_graph_lowering import lower_catalog_metrics, normalize_target_metric
from marivo.semantic.validator import (
    Registry,
    normalize_target_dimension,
    normalize_target_entity,
    normalize_target_version_selection,
)

_LOCATION = SourceLocation("declared.py", 1)


def _entity(
    name: str,
    *,
    key: tuple[str, ...] = ("id",),
    version: EntityVersioningIR | None = None,
) -> EntityIR:
    return EntityIR(
        semantic_id=f"sales.{name}",
        domain="sales",
        name=name,
        datasource="warehouse",
        source=JsonSourceIR(
            path="https://example.test/facts",
            schema=(
                ("id", "int64"),
                ("tenant", "string"),
                ("amount", "float64"),
                ("weight", "float64"),
                ("day", "date"),
                ("start", "date"),
                ("end", "date"),
            ),
            query_params=(("region", SourceParamIR("region")),),
        ),
        primary_key=key,
        ai_context=AiContextIR(),
        python_symbol=name,
        location=_LOCATION,
        versioning=version,
    )


def _registry() -> Registry:
    registry = Registry()
    registry.domains["sales"] = DomainIR("sales", "sales", True, AiContextIR(), _LOCATION)
    registry.datasources["warehouse"] = DatasourceIR(
        "warehouse",
        "warehouse",
        "duckdb",
        {},
        {"http_bearer_token": "API_TOKEN"},
        AiContextIR(),
        "warehouse",
        DatasourceSourceLocation("declared.py", 1),
    )
    for name in ("orders", "customers", "visits"):
        entity = _entity(name)
        registry.entities[entity.semantic_id] = entity
        for column in ("day", "start", "end"):
            path = f"{entity.semantic_id}.{column}"
            registry.dimensions[path] = DimensionIR(
                path,
                "sales",
                entity.semantic_id,
                column,
                AiContextIR(),
                True,
                DimensionKind.TIME,
                column,
                _LOCATION,
                granularity="day",
                parse=DateParse(),
                is_default=column == "day",
                source_column=column,
                body_ast_hash=ExpressionBody.for_column(column).body_ast_hash,
            )
        for column in ("amount", "weight"):
            path = f"{entity.semantic_id}.{column}"
            registry.measures[path] = MeasureIR(
                path,
                "sales",
                entity.semantic_id,
                column,
                AiContextIR(),
                "additive",
                "USD" if column == "amount" else None,
                column,
                _LOCATION,
                body_ast_hash=ExpressionBody.for_column(column).body_ast_hash,
            )
    return registry


def _metric(name: str, *, agg: AggKind = "sum", entity: str = "orders") -> MetricIR:
    return MetricIR(
        semantic_id=f"sales.{name}",
        domain="sales",
        name=name,
        metric_type="simple",
        entities=(f"sales.{entity}",),
        aggregation=agg,
        measure=f"sales.{entity}.amount",
        composition=None,
        additivity=None,
        provenance=None,
        ai_context=AiContextIR(),
        body_ast_hash="direct",
        python_symbol=name,
        location=_LOCATION,
        aggregation_target=f"sales.{entity}.amount",
        aggregation_target_kind="measure",
    )


def _sidecar(registry: Registry) -> CompiledExpressionSidecar:
    bodies: dict[Ref[SemanticKindTag], ExpressionBody] = {}
    # The production body factory establishes the exact source-column binding.
    for measure in registry.measures.values():
        bodies[ref.measure(measure.semantic_id)] = ExpressionBody.for_column(measure.name)
    return CompiledExpressionSidecar(bodies=bodies, field_owners={}, catalog_refs=frozenset(bodies))


def test_entity_identity_is_ordered_and_independent_of_version_row_key() -> None:
    registry = _registry()
    registry.entities["sales.orders"] = _entity("orders", key=("tenant", "id"))
    normalized = normalize_target_entity(registry, "sales.orders")
    assert normalized.primary_key == ("tenant", "id")
    assert normalized.identity_signature == (("tenant", "string"), ("id", "int64"))
    assert normalized.version_row_key == ("tenant", "id")
    assert normalized.credential_slots == ("http_bearer_token",)
    assert normalized.datasource_ref.path == "warehouse"
    assert normalize_target_entity(registry, "sales.customers").identity_signature == (
        ("id", "int64"),
    )
    with pytest.raises(FrozenInstanceError):
        normalized.__setattr__("primary_key", ())
    registry.entities["sales.orders"] = _entity("orders", key=())
    unkeyed = normalize_target_entity(registry, "sales.orders")
    assert unkeyed.primary_key == ()
    assert unkeyed.identity_signature == ()
    assert unkeyed.obligations == ()


def test_normalized_source_detaches_authored_mutable_query_sequences() -> None:
    registry = _registry()
    values: UserList[str | int | float | bool | SourceParamIR] = UserList([SourceParamIR("region")])
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, JsonSourceIR)
    registry.entities[entity.semantic_id] = replace(
        entity,
        source=replace(entity.source, query_params=(("region", values),)),
    )
    normalized = normalize_target_entity(registry, entity.semantic_id)
    assert isinstance(normalized.source, JsonSourceIR)
    values.append("later")
    assert normalized.source.query_params == (("region", (SourceParamIR("region"),)),)


def test_normalized_entity_preserves_datasource_identity() -> None:
    registry = _registry()
    first = normalize_target_entity(registry, "sales.orders")
    registry.entities["sales.orders"] = replace(
        registry.entities["sales.orders"], datasource="other"
    )
    registry.datasources["other"] = replace(
        registry.datasources["warehouse"], semantic_id="other", name="other"
    )
    second = normalize_target_entity(registry, "sales.orders")
    assert first != second
    assert first.datasource_ref.path == "warehouse" and second.datasource_ref.path == "other"
    assert first.dependency_fingerprint != second.dependency_fingerprint
    fingerprint_before_change = second.dependency_fingerprint
    registry.datasources["other"].fields["path"] = "changed.duckdb"
    changed = normalize_target_entity(registry, "sales.orders")
    assert changed.dependency_fingerprint != fingerprint_before_change
    assert second.dependency_fingerprint == fingerprint_before_change


def test_snapshot_normalization_declares_obligations_and_exact_left_limit() -> None:
    registry = _registry()
    registry.entities["sales.orders"] = _entity(
        "orders",
        version=SnapshotVersioningIR("snapshot", "sales.orders.day", "day", "Asia/Shanghai"),
    )
    normalized = normalize_target_entity(registry, "sales.orders")
    assert normalized.primary_key == ("id",)
    assert normalized.version_row_key == ("id", "day")
    assert {obligation.kind for obligation in normalized.obligations} == {
        "identity_non_null",
        "source_row_unique",
        "exact_snapshot_available",
        "selected_identity_unique",
    }
    boundary = datetime(2026, 2, 1, 16, tzinfo=timezone.utc)
    instant = normalize_target_version_selection(
        normalized, boundary=boundary, interpretation="instant"
    )
    previous = normalize_target_version_selection(
        normalized, boundary=boundary, interpretation="before_endpoint"
    )
    assert isinstance(instant, TargetSnapshotSelection) and instant.period == "2026-02-02"
    assert isinstance(previous, TargetSnapshotSelection) and previous.period == "2026-02-01"
    within_day = normalize_target_version_selection(
        normalized,
        boundary=boundary.replace(minute=1),
        interpretation="before_endpoint",
    )
    assert isinstance(within_day, TargetSnapshotSelection) and within_day.period == "2026-02-02"


@pytest.mark.parametrize("interval", ["closed_open", "closed_closed"])
def test_validity_exact_endpoint_comparisons(interval: str) -> None:
    registry = _registry()
    assert interval in {"closed_open", "closed_closed"}
    version = ValidityVersioningIR(
        "validity",
        "sales.orders.start",
        "sales.orders.end",
        "closed_open" if interval == "closed_open" else "closed_closed",
        (None, "9999-12-31"),
        "UTC",
    )
    registry.entities["sales.orders"] = _entity("orders", version=version)
    normalized = normalize_target_entity(registry, "sales.orders")
    assert normalized.version_row_key == ("id", "start")
    assert "validity_non_overlapping" in {item.kind for item in normalized.obligations}
    boundary = datetime(2026, 2, 1, tzinfo=timezone.utc)
    instant = normalize_target_version_selection(
        normalized, boundary=boundary, interpretation="instant"
    )
    before = normalize_target_version_selection(
        normalized, boundary=boundary, interpretation="before_endpoint"
    )
    assert isinstance(instant, TargetValiditySelection) and isinstance(
        before, TargetValiditySelection
    )
    assert instant.boundary == before.boundary == boundary.isoformat()
    assert (before.start_operator, before.end_operator) == ("lt", "ge")
    assert (instant.start_operator, instant.end_operator) == (
        "le",
        "gt" if interval == "closed_open" else "ge",
    )


def test_invalid_identity_and_version_axes_fail_without_guessing_columns() -> None:
    registry = _registry()
    for key in (("unknown",), ("id", "id")):
        registry.entities["sales.orders"] = _entity("orders", key=key)
        with pytest.raises(SemanticLoadError, match="declared source types"):
            normalize_target_entity(registry, "sales.orders")
    registry.entities["sales.orders"] = _entity(
        "orders",
        version=SnapshotVersioningIR("snapshot", "sales.customers.day", "day"),
    )
    with pytest.raises(SemanticLoadError, match="same Entity"):
        normalize_target_entity(registry, "sales.orders")
    registry.entities["sales.orders"] = _entity(
        "orders",
        key=("id", "day"),
        version=SnapshotVersioningIR("snapshot", "sales.orders.day", "day"),
    )
    with pytest.raises(SemanticLoadError, match="separate from snapshot"):
        normalize_target_entity(registry, "sales.orders")


@pytest.mark.parametrize("agg", ["sum", "count", "mean"])
def test_aggregate_contract_is_derived_from_canonical_graph(agg: AggKind) -> None:
    registry = _registry()
    registry.metrics["sales.value"] = _metric("value", agg=agg)
    sidecar = _sidecar(registry)
    normalized = normalize_target_metric(registry, "sales.value", sidecar=sidecar)
    assert (
        normalized.graph == lower_catalog_metrics(registry, ("sales.value",), sidecar=sidecar).graph
    )
    assert tuple(item.path for item in normalized.computation_roots) == ("sales.orders",)
    assert normalized.logical_type == ("int64" if agg == "count" else "float64")
    assert normalized.empty_rule == ("zero" if agg == "count" else "null")
    assert normalized.nullable is (agg != "count")
    assert "value.row_count" in normalized.required_state
    if agg == "mean":
        assert normalized.required_state == ("value.sum", "value.non_null_count", "value.row_count")


def test_weighted_mean_and_different_root_ratio_retain_intrinsic_component_state() -> None:
    registry = _registry()
    registry.metrics["sales.weighted"] = replace(
        _metric("weighted"),
        aggregation=None,
        measure=None,
        aggregation_target=None,
        aggregation_target_kind=None,
        weighted_mean=WeightedMeanAggregation("sales.orders.amount", "sales.orders.weight"),
    )
    registry.metrics["sales.visits"] = _metric("visits", agg="count", entity="visits")
    registry.metrics["sales.ratio"] = replace(
        _metric("ratio"),
        metric_type="derived",
        entities=(),
        aggregation=None,
        measure=None,
        aggregation_target=None,
        aggregation_target_kind=None,
        composition=RatioComposition("sales.weighted", "sales.visits"),
    )
    sidecar = _sidecar(registry)
    weighted = normalize_target_metric(registry, "sales.weighted", sidecar=sidecar)
    assert weighted.null_rule == "non_null_pairs" and weighted.empty_rule == "null"
    assert weighted.required_state == (
        "value.weighted_numerator",
        "value.weight_sum",
        "value.non_null_pair_count",
        "value.row_count",
    )
    ratio = normalize_target_metric(registry, "sales.ratio", sidecar=sidecar)
    assert tuple(root.path for root in ratio.computation_roots) == ("sales.orders", "sales.visits")
    assert tuple(component.role for component in ratio.components) == (
        "value.numerator",
        "value.denominator",
    )
    assert ratio.null_rule == "null_component_or_zero_denominator"
    assert ratio.evaluation_order == ("space", "time", "compose")
    assert "value.numerator.weighted_numerator" in ratio.required_state
    assert "value.denominator.count" in ratio.required_state


def test_metric_missing_type_facts_fail_and_quantiles_require_source() -> None:
    registry = _registry()
    registry.metrics["sales.value"] = _metric("value")
    with pytest.raises(SemanticLoadError, match="direct-column"):
        normalize_target_metric(registry, "sales.value")
    registry.metrics["sales.value"] = _metric("value", agg="median")
    normalized = normalize_target_metric(registry, "sales.value", sidecar=_sidecar(registry))
    assert normalized.requires_source_recompute
    assert normalized.required_state == ()
    assert "metric.source_quantile@v1" in normalized.source_requirements


def test_metric_never_rewrites_the_authored_computation_root() -> None:
    registry = _registry()
    registry.metrics["sales.value"] = replace(
        _metric("value"),
        entities=("sales.orders", "sales.customers"),
        root_entity="sales.customers",
    )
    with pytest.raises(SemanticLoadError, match="declared computation root"):
        normalize_target_metric(registry, "sales.value", sidecar=_sidecar(registry))


def test_status_measure_requires_spatial_before_temporal_state() -> None:
    registry = _registry()
    registry.measures["sales.orders.amount"] = replace(
        registry.measures["sales.orders.amount"],
        additivity=SemiAdditive("sales.orders.day", TimeFoldIR("last")),
    )
    registry.metrics["sales.value"] = _metric("value")
    normalized = normalize_target_metric(registry, "sales.value", sidecar=_sidecar(registry))
    assert normalized.components[0].time_fold == "last"
    assert normalized.components[0].status_time_dimension is not None
    assert normalized.components[0].status_time_dimension.path == "sales.orders.day"
    assert "metric.source_temporal_fold@v1" in normalized.source_requirements
    assert normalized.requires_source_recompute
    assert normalized.required_state == ()
    assert normalized.evaluation_order.index("space") < normalized.evaluation_order.index("time")
    axis = normalize_target_dimension(registry, "sales.orders.day")
    assert axis.logical_type == "date" and axis.is_default and axis.granularity == "day"


def test_weighted_temporal_value_requires_additive_weight() -> None:
    registry = _registry()
    temporal = SemiAdditive("sales.orders.day", TimeFoldIR("last"))
    registry.measures["sales.orders.amount"] = replace(
        registry.measures["sales.orders.amount"], additivity=temporal
    )
    registry.metrics["sales.weighted"] = replace(
        _metric("weighted"),
        aggregation=None,
        measure=None,
        aggregation_target=None,
        aggregation_target_kind=None,
        weighted_mean=WeightedMeanAggregation("sales.orders.amount", "sales.orders.weight"),
    )
    normalized = normalize_target_metric(registry, "sales.weighted", sidecar=_sidecar(registry))
    component = normalized.components[0]
    assert component.time_fold == "last"
    assert component.status_time_dimension is not None
    assert component.status_time_dimension.path == "sales.orders.day"
    assert "metric.source_temporal_fold@v1" in normalized.source_requirements
    assert normalized.requires_source_recompute
    assert normalized.required_state == ()
    registry.measures["sales.orders.weight"] = replace(
        registry.measures["sales.orders.weight"], additivity=temporal
    )
    with pytest.raises(SemanticLoadError, match="additive weight"):
        normalize_target_metric(registry, "sales.weighted", sidecar=_sidecar(registry))


def test_private_normalization_never_calls_telemetry_wrapped_public_ref_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry()
    registry.metrics["sales.value"] = _metric("value")
    sidecar = _sidecar(registry)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Public ref factory could perform telemetry I/O")

    for name in ("entity", "datasource", "dimension", "time_dimension", "measure", "metric"):
        monkeypatch.setattr(type(ref), name, forbidden)
    assert normalize_target_entity(registry, "sales.orders").primary_key == ("id",)
    assert normalize_target_dimension(registry, "sales.orders.day").logical_type == "date"
    assert (
        normalize_target_metric(registry, "sales.value", sidecar=sidecar).logical_type == "float64"
    )
