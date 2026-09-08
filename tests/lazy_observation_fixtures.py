"""Pure authored semantic fixtures shared by private Observation acceptance."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from marivo._compat import Never
from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.observation.population import LogicalPopulationDataset
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.datasource.ir import (
    AiContextIR,
    CsvSourceIR,
    DatasourceIR,
    DatasourceSourceLocation,
    JsonSourceIR,
    SourceParamIR,
)
from marivo.refs import Ref, SemanticKind, SemanticKindTag, _create_ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar, ExpressionBody
from marivo.semantic.ir import (
    AggKind,
    DateParse,
    DimensionIR,
    DimensionKind,
    DomainIR,
    EntityIR,
    EntityVersioningIR,
    JoinKey,
    MeasureIR,
    MetricIR,
    RatioComposition,
    RelationshipIR,
    SnapshotVersioningIR,
    SourceLocation,
    ValidityVersioningIR,
    WeightedMeanAggregation,
)
from marivo.semantic.validator import Registry

if TYPE_CHECKING:
    from marivo.analysis.operators.delta import LogicalDeltaDataset
    from marivo.refs import EntityKind, FieldKind

_LOCATION = SourceLocation("lazy_fixture.py", 1)
_SCHEMA = (
    ("id", "int64"),
    ("tenant", "string"),
    ("customer_id", "int64"),
    ("order_id", "int64"),
    ("amount", "float64"),
    ("weight", "float64"),
    ("region", "string"),
    ("channel", "string"),
    ("day", "date"),
    ("start", "date"),
    ("end", "date"),
)


class NoIoActionPort:
    """Explicit test port proving definition actions do not cross runtime boundaries."""

    def execute_population(self, dataset: LogicalPopulationDataset) -> Never:
        raise AssertionError("Population execution is not part of definition-only acceptance")

    def execute_metric(self, dataset: LogicalMetricDataset) -> Never:
        raise AssertionError("Metric execution is not part of definition-only acceptance")

    def execute_delta(self, dataset: LogicalDeltaDataset) -> Never:
        raise AssertionError("Delta execution is not part of definition-only acceptance")

    def show(self, dataset: MaterializedDataset, *, max_output_bytes: int | None) -> Never:
        raise AssertionError("No retained row read is authorized")

    def to_pandas(self, dataset: MaterializedDataset) -> Never:
        raise AssertionError("No retained row collection is authorized")

    def evidence_digest(self, dataset: MaterializedDataset) -> Never:
        raise AssertionError("No retained Evidence read is authorized")

    def findings(self, dataset: MaterializedDataset, *, limit: int, cursor: str | None) -> Never:
        raise AssertionError("No Finding page read is authorized")

    def finding(self, dataset: MaterializedDataset, finding_id: str) -> Never:
        raise AssertionError("No Finding read is authorized")


def _metric(name: str, entity: str = "orders", agg: AggKind = "sum") -> MetricIR:
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


def make_semantic_registry() -> tuple[Registry, CompiledExpressionSidecar]:
    """Build frozen authored declarations; actual target normalizers run in sources."""
    registry = Registry()
    registry.domains["sales"] = DomainIR("sales", "sales", True, AiContextIR(), _LOCATION)
    registry.datasources["warehouse"] = DatasourceIR(
        "warehouse",
        "warehouse",
        "duckdb",
        {},
        {},
        AiContextIR(),
        "warehouse",
        DatasourceSourceLocation("lazy_fixture.py", 1),
    )
    bodies: dict[Ref[SemanticKindTag], ExpressionBody] = {}
    field_owners: dict[Ref[FieldKind], Ref[EntityKind]] = {}
    for name in (
        "orders",
        "customers",
        "lines",
        "snapshots",
        "validity",
        "api",
        "unkeyed",
        "composite",
    ):
        path = f"sales.{name}"
        source = (
            JsonSourceIR(
                path="https://fixture.invalid/facts",
                schema=_SCHEMA,
                query_params=(("tenant", SourceParamIR("tenant")),),
            )
            if name == "api"
            else CsvSourceIR(path=f"{name}.csv", schema=_SCHEMA)
        )
        version: EntityVersioningIR | None = None
        if name == "snapshots":
            version = SnapshotVersioningIR("snapshot", f"{path}.snapshot_at", "day")
        elif name == "validity":
            version = ValidityVersioningIR(
                "validity", f"{path}.valid_from", f"{path}.valid_to", "closed_open", (None,)
            )
        key = () if name == "unkeyed" else (("tenant", "id") if name == "composite" else ("id",))
        registry.entities[path] = EntityIR(
            path, "sales", name, "warehouse", source, key, AiContextIR(), name, _LOCATION, version
        )
        for column in ("amount", "weight"):
            field_path = f"{path}.{column}"
            body = ExpressionBody.for_column(column)
            registry.measures[field_path] = MeasureIR(
                field_path,
                "sales",
                path,
                column,
                AiContextIR(),
                "additive",
                "USD" if column == "amount" else None,
                column,
                _LOCATION,
                body_ast_hash=body.body_ast_hash,
            )
            reference = _create_ref(SemanticKind.MEASURE, field_path)
            bodies[reference] = body
            field_owners[cast("Ref[FieldKind]", reference)] = cast(
                "Ref[EntityKind]", _create_ref(SemanticKind.ENTITY, path)
            )
        dimensions: tuple[tuple[str, str, bool, bool], ...] = ()
        if name == "orders":
            dimensions = (("order_time", "day", True, True), ("channel", "channel", False, False))
        elif name == "customers":
            dimensions = (("region", "region", False, False),)
        elif name == "snapshots":
            dimensions = (("snapshot_at", "day", True, True),)
        elif name == "validity":
            dimensions = (("valid_from", "start", True, True), ("valid_to", "end", True, False))
        elif name == "api":
            dimensions = (("observed_at", "day", True, True), ("region", "region", False, False))
        for label, column, temporal, default in dimensions:
            field_path = f"{path}.{label}"
            body = ExpressionBody.for_column(column)
            registry.dimensions[field_path] = DimensionIR(
                field_path,
                "sales",
                path,
                label,
                AiContextIR(),
                temporal,
                DimensionKind.TIME if temporal else DimensionKind.CATEGORICAL,
                label,
                _LOCATION,
                granularity="day" if temporal else None,
                parse=DateParse() if temporal else None,
                is_default=default,
                body_ast_hash=body.body_ast_hash,
                source_column=column,
            )
            reference = _create_ref(
                SemanticKind.TIME_DIMENSION if temporal else SemanticKind.DIMENSION, field_path
            )
            bodies[reference] = body
            field_owners[cast("Ref[FieldKind]", reference)] = cast(
                "Ref[EntityKind]", _create_ref(SemanticKind.ENTITY, path)
            )
    for name, left, right, left_key in (
        ("order_customer", "orders", "customers", "customer_id"),
        ("line_order", "lines", "orders", "order_id"),
    ):
        path = f"sales.{name}"
        registry.relationships[path] = RelationshipIR(
            path,
            "sales",
            name,
            f"sales.{left}",
            f"sales.{right}",
            (JoinKey(left_key, "id"),),
            AiContextIR(),
            _LOCATION,
        )
    for metric in (
        _metric("revenue"),
        _metric("order_count", agg="count"),
        _metric("mean_amount", agg="mean"),
        _metric("line_revenue", "lines"),
        _metric("api_value", "api"),
    ):
        registry.metrics[metric.semantic_id] = metric
    weighted = replace(
        _metric("weighted_amount"),
        aggregation=None,
        measure=None,
        aggregation_target=None,
        aggregation_target_kind=None,
        weighted_mean=WeightedMeanAggregation("sales.orders.amount", "sales.orders.weight"),
    )
    registry.metrics[weighted.semantic_id] = weighted
    for name, numerator, denominator in (
        ("conversion_rate", "sales.revenue", "sales.order_count"),
        ("cross_root_ratio", "sales.line_revenue", "sales.revenue"),
    ):
        metric = MetricIR(
            f"sales.{name}",
            "sales",
            name,
            "derived",
            (),
            None,
            None,
            RatioComposition(numerator, denominator),
            None,
            None,
            AiContextIR(),
            "derived",
            name,
            _LOCATION,
        )
        registry.metrics[metric.semantic_id] = metric
    registry.freeze()
    references = (
        frozenset(bodies)
        | frozenset(_create_ref(SemanticKind.ENTITY, path) for path in registry.entities)
        | frozenset(_create_ref(SemanticKind.METRIC, path) for path in registry.metrics)
    )
    sidecar = CompiledExpressionSidecar(
        bodies=bodies, field_owners=field_owners, catalog_refs=references
    )
    return registry, sidecar


def make_sources(
    *, session_id: str = "session-observation", store_id: str = "store-observation"
) -> LazySources:
    registry, sidecar = make_semantic_registry()
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id=session_id,
        store_id=store_id,
    )
