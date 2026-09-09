"""Pure Forecast construction and exact private family registration."""

from __future__ import annotations

import math

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset, _dataset_repr
from marivo.analysis.datasets.registry import (
    ConsumerRegistration,
    DatasetFamilyRegistration,
    DatasetFamilyRegistry,
)
from marivo.analysis.datasets.state import MaterializedDatasetState, _validate_materialized_state
from marivo.analysis.observation.contracts import (
    EntityReducedMetricSemantics,
    RetainedRowsPayload,
    owner_of,
    producer_contract,
)
from marivo.analysis.observation.fold_contracts import decode_fold_authority
from marivo.analysis.operators.contracts import comparison_basis, decode_comparison_basis
from marivo.analysis.operators.errors import forecast_error
from marivo.analysis.operators.forecast_contracts import (
    ASSUMPTIONS,
    DEFAULT_MODEL,
    INTERVAL_METHOD,
    ForecastHorizon,
    ForecastModel,
    ForecastPayload,
    ForecastSemantics,
    ForecastSpecV1,
)
from marivo.analysis.operators.forecast_dataset import (
    LogicalForecastDataset,
    MaterializedForecastDataset,
)
from marivo.semantic._quantile import approximation_class, decode_approximation

FIELDS = (
    ("horizon_ordinal", "int64"),
    ("forecast_value", "float64"),
    ("interval_lower", "float64"),
    ("interval_upper", "float64"),
    ("training_row_count", "int64"),
)


def forecast(
    dataset: Dataset,
    *,
    horizon: ForecastHorizon,
    model: ForecastModel = DEFAULT_MODEL,
    interval_level: float = 0.95,
) -> LogicalForecastDataset:
    """Freeze a complete forecast invocation without reading any history rows."""
    shape = dataset.row_contract.shape_id.local_shape_id
    incoming = dataset.row_contract.family_semantics
    metrics = tuple(f for f in dataset.schema.columns if f.role_id == "metric")
    if (
        dataset.kind != "metric"
        or shape not in ("time", "dimension-time")
        or len(metrics) != 1
        or not isinstance(incoming, EntityReducedMetricSemantics)
    ):
        raise forecast_error("one time-bearing Entity-reduced Metric", "unsupported receiver")
    if type(horizon) is not ForecastHorizon or type(model) is not ForecastModel:
        raise forecast_error(
            "helper-produced ForecastHorizon and ForecastModel", "invalid arguments"
        )
    if (
        type(interval_level) not in (float, int)
        or not math.isfinite(interval_level)
        or not 0 < interval_level < 1
    ):
        raise forecast_error("finite interval_level in (0, 1)", "invalid interval level")
    metric = metrics[0]
    if not isinstance(metric.identity, d._CatalogFieldIdentity) or (
        not metric.logical_type_id.startswith(("int", "uint", "float", "decimal"))
        and metric.logical_type_id not in ("integer", "floating")
    ):
        raise forecast_error("a quantitative governed Metric", "invalid Metric value type")
    authority = decode_fold_authority(incoming.fold_authority)
    if authority.time_grain() is None or authority.time_scope() is None:
        raise forecast_error("bound time grain and observation scope", "missing time authority")
    sampled = bool(decode_comparison_basis(comparison_basis(dataset)).sampling_definition)
    semantics = ForecastSemantics(
        _token=d._CORE_TOKEN,
        metric_key=metric.identity.identity_id,
        metric_unit=incoming.metric_bindings[0][1],
        approximation=approximation_class(
            sampled=sampled,
            semantic=any(
                f.distribution is not None and f.distribution.quantile.method == "duckdb_tdigest@v1"
                for f in authority.metrics
            ),
        ),
        fold_authority=incoming.fold_authority,
        model_id=model.model_id,
        season_length=model.season_length,
        horizon=horizon.count,
        interval_level=float(interval_level),
    )
    ids = dataset._registration.ids
    coordinates = tuple(
        f for f in dataset.schema.columns if f.role_id in ("dimension", "time_dimension")
    )
    generated = []
    for name, kind in FIELDS:
        field_id = d._make_field_id(f"generated.forecast.{name}@v1")
        generated.append(
            d._make_field(
                field_id=field_id,
                name=name,
                role_id="effect_value",
                identity=d._generated_identity(field_id),
                derivation_identity=f"forecast.{name}@v1",
                logical_type_id=kind,
                physical_type_state=d._deferred_type(kind, ids=ids),
                nullable=False,
                ids=ids,
            )
        )
    keys = tuple(f.field_id for f in coordinates)
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id("forecast", shape, 1, ids=ids),
        schema=d._make_schema((*coordinates, *generated)),
        coordinate_field_ids=keys,
        key_field_ids=keys,
        family_semantics=semantics,
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._unknown_row_bound()),
        ordering=d._ordered_ordering(
            tuple(
                d._make_order_term(
                    f.field_id,
                    direction="ascending",
                    nulls="last",
                    value_order_contract_id="observation.scalar_order@v1",
                    ids=ids,
                )
                for f in coordinates
            )
        ),
    )
    result = construct_operator(
        owner=owner_of(dataset),
        registry=dataset._registry,
        operator_id="metric.forecast",
        contract_versions=producer_contract("metric.forecast").versions,
        inputs=(dataset,),
        row_contract=row,
        row_set_contract=rows,
        payload=ForecastPayload(
            _token=d._CORE_TOKEN,
            spec=ForecastSpecV1(dataset.row_contract, dataset.row_set_contract, row, rows),
        ),
    )
    if not isinstance(result, LogicalForecastDataset):
        raise forecast_error("paired Logical Forecast", "invalid family registration")
    return result


def validate_forecast(row: d.DatasetRowContract, rows: d.DatasetRowSetContract) -> None:
    s = row.family_semantics
    if not isinstance(s, ForecastSemantics) or row.shape_id.local_shape_id not in (
        "time",
        "dimension-time",
    ):
        raise forecast_error("closed Forecast family", "invalid semantics")
    if (
        s.model_id not in ("naive@v1", "drift@v1", "seasonal_naive@v1")
        or s.interval_method != INTERVAL_METHOD
        or s.assumption_contract != ASSUMPTIONS
    ):
        raise forecast_error(
            "registered model and nominal interval assumptions", "changed model contract"
        )
    if (
        (type(s.season_length) is not int or not s.season_length > 1)
        if s.model_id == "seasonal_naive@v1"
        else s.season_length is not None
    ):
        raise forecast_error(
            "explicit integer season length greater than one", "invalid retained season"
        )
    if (
        type(s.horizon) is not int
        or not 1 <= s.horizon <= 1000
        or type(s.interval_level) is not float
        or not math.isfinite(s.interval_level)
        or not 0 < s.interval_level < 1
    ):
        raise forecast_error(
            "bounded horizon and finite interval level", "invalid retained invocation"
        )
    decode_approximation(s.approximation)
    authority = decode_fold_authority(s.fold_authority)
    if (
        len(authority.metrics) != 1
        or "metric:" + authority.metrics[0].metric_ref != s.metric_key
        or authority.time_grain() is None
        or authority.time_scope() is None
    ):
        raise forecast_error(
            "one exact Metric and retained time authority", "invalid retained binding"
        )
    coordinates = tuple(
        f for f in row.schema.columns if f.role_id in ("dimension", "time_dimension")
    )
    dims = tuple(f for f in coordinates if f.role_id == "dimension")
    times = tuple(f for f in coordinates if f.role_id == "time_dimension")
    if (
        len(times) != 1
        or bool(dims) != (row.shape_id.local_shape_id == "dimension-time")
        or any(not isinstance(f.identity, d._CatalogFieldIdentity) for f in coordinates)
    ):
        raise forecast_error("shape-exact governed coordinates", "invalid Forecast coordinates")
    fields = {f.name: f for f in row.schema.columns}
    for name, kind in FIELDS:
        f = fields.get(name)
        if f is None or not forecast_filterable_field(f) or f.logical_type_id != kind:
            raise forecast_error("exact generated Forecast fields", "invalid generated field")
    expected = (*(f.name for f in coordinates), *(name for name, _ in FIELDS))
    if "rank" in fields:
        f = fields["rank"]
        if (
            f.field_id.value != "generated.rank@v1"
            or f.role_id != "rank"
            or f.logical_type_id != "int64"
            or not f.nullable
        ):
            raise forecast_error("registered nullable rank", "invalid rank field")
        expected = (*expected, "rank")
    keys = tuple(f.field_id for f in coordinates)
    if (
        tuple(fields) != expected
        or row.coordinate_field_ids != keys
        or row.key_field_ids != keys
        or rows.cardinality.kind != "keyed"
        or not isinstance(rows.ordering, d._OrderedOrdering)
    ):
        raise forecast_error("exact fields, keys and total order", "invalid Forecast row contract")
    if "rank" not in fields and (
        tuple(t.field_id for t in rows.ordering.terms) != keys
        or any(
            (t.direction, t.nulls, t.value_order_contract_id)
            != ("ascending", "last", "observation.scalar_order@v1")
            for t in rows.ordering.terms
        )
    ):
        raise forecast_error(
            "ascending Dimension and future-period order", "invalid coordinate order"
        )


def forecast_filterable_field(f: d.DatasetField) -> bool:
    return any(
        f.name == name
        and f.logical_type_id == kind
        and f.role_id == "effect_value"
        and f.field_id.value == f"generated.forecast.{name}@v1"
        and not f.nullable
        and isinstance(f.identity, d._GeneratedFieldIdentity)
        and f.identity.producer_field_id == f.field_id
        for name, kind in FIELDS
    )


def _contract_facts(dataset: Dataset) -> tuple[tuple[str, str], ...]:
    s = dataset.row_contract.family_semantics
    if not isinstance(s, ForecastSemantics):
        raise forecast_error("closed Forecast meaning", "missing disclosure authority")
    return (
        ("model", s.model_id),
        ("horizon", str(s.horizon)),
        ("season_length", str(s.season_length)),
        ("interval", f"{s.interval_method}; nominal level={s.interval_level}"),
        ("assumptions", s.assumption_contract),
        (
            "interpretation",
            "model-conditional nominal prediction; no empirical coverage calibration; drift includes estimated-increment uncertainty",
        ),
        (
            "zero_variance",
            "only all-zero observed innovations; no guarantee of deterministic future values",
        ),
        ("approximation", s.approximation),
    )


def register_forecast(registry: DatasetFamilyRegistry, ids: d._StableIdRegistry) -> None:
    def decode(state: MaterializedDatasetState) -> MaterializedDatasetState:
        _validate_materialized_state(state, ids=ids)
        return state

    shapes = tuple(
        d._make_shape_id("forecast", shape, 1, ids=ids) for shape in ("time", "dimension-time")
    )
    registry.register(
        DatasetFamilyRegistration(
            family_id="forecast",
            logical_type=LogicalForecastDataset,
            materialized_type=MaterializedForecastDataset,
            shape_ids=shapes,
            owner_id="operators.forecast",
            ids=ids,
            row_validator=validate_forecast,
            consumers=tuple(
                ConsumerRegistration(
                    f"forecast.{m}", ("input",), "forecast", shapes, ("forecast.current_rows@v1",)
                )
                for m in ("where", "rank", "limit")
            ),
            repr_renderer=_dataset_repr,
            materialized_state_decoder=decode,
            node_payload_types=(ForecastPayload, RetainedRowsPayload),
            contract_facts=_contract_facts,
            consumer_admission=lambda dataset, method: (
                not any(f.role_id == "rank" for f in dataset.schema.columns)
                if method == "forecast.rank"
                else dataset.row_set_contract.ordering.kind == "ordered"
                if method == "forecast.limit"
                else True
            ),
        )
    )
