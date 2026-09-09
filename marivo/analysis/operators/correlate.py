"""Pure Association construction and exact closed family registration."""

from __future__ import annotations

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
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
    RetainedRowsPayload,
    owner_of,
    producer_contract,
)
from marivo.analysis.observation.fold_contracts import decode_fold_authority
from marivo.analysis.operators.association import (
    LogicalAssociationDataset,
    MaterializedAssociationDataset,
)
from marivo.analysis.operators.association_contracts import (
    ASSOCIATION_SHAPES,
    MAX_CANDIDATES,
    SELECTION_RULE_ID,
    AssociationSemantics,
    CorrelatePayload,
    CorrelateSpecV1,
    CorrelationMethod,
    candidate_count,
    pair_count,
    selection_description,
)
from marivo.analysis.operators.contracts import comparison_basis, decode_comparison_basis
from marivo.analysis.operators.errors import correlation_error
from marivo.semantic._quantile import approximation_class

BASE_FIELDS = (
    ("metric_key_a", "metric_identity", "string", False),
    ("metric_key_b", "metric_identity", "string", False),
    ("status", "status", "string", False),
    ("coefficient", "effect_value", "float64", True),
    ("input_observation_count", "effect_value", "int64", False),
    ("null_pair_count", "effect_value", "int64", False),
    ("complete_pair_count", "effect_value", "int64", False),
)
LAG_FIELDS = (
    ("lag_offset", "effect_value", "int64", False),
    ("selected_for_pair", "status", "boolean", False),
    ("matched_observation_count", "effect_value", "int64", False),
    ("lag_boundary_drop_count", "effect_value", "int64", False),
)


def correlate(
    dataset: Dataset, *, method: CorrelationMethod = "pearson", lag_range: range | None = None
) -> LogicalAssociationDataset:
    """Freeze one complete invocation without reading sources or retained rows."""
    shape = dataset.row_contract.shape_id.local_shape_id
    if dataset.kind != "metric" or shape not in ("entity", "dimension", "time", "dimension-time"):
        raise correlation_error(
            "Entity, Dimension, Time or Dimension-Time Metric", "unsupported input shape"
        )
    if method not in ("pearson", "spearman", "kendall"):
        raise correlation_error("pearson, spearman or kendall", "unknown method")
    if lag_range is not None and (type(lag_range) is not range or "time" not in shape):
        raise correlation_error("a range on a time-bearing Metric", "invalid explicit lag range")
    metrics = tuple(f for f in dataset.schema.columns if f.role_id == "metric")
    if not 2 <= len(metrics) <= 16 or len({f.identity for f in metrics}) != len(metrics):
        raise correlation_error(
            "2-16 distinct quantitative Metrics", "invalid Metric arity or duplicate identity"
        )
    for f in metrics:
        if f.logical_type_id not in (
            "integer",
            "int32",
            "int64",
            "floating",
            "float32",
            "float64",
            "decimal",
        ) or not isinstance(f.identity, d._CatalogFieldIdentity):
            raise correlation_error(
                "quantitative governed Metric fields", "unsupported value or identity type"
            )
    try:
        lag_count = 1 if lag_range is None else len(lag_range)
    except OverflowError:
        raise correlation_error("at most 4096 pair/lag candidates", "oversized lag range") from None
    if lag_count == 0 or candidate_count(len(metrics), lag_count) > MAX_CANDIDATES:
        raise correlation_error(
            "1-4096 pair/lag candidates", "candidate ceiling exceeded or empty range"
        )
    lags = (0,) if lag_range is None else tuple(lag_range)
    if any(not -(2**63) <= k < 2**63 for k in lags):
        raise correlation_error("signed int64 bucket offsets", "lag overflow")
    incoming = dataset.row_contract.family_semantics
    if not isinstance(incoming, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise correlation_error("complete Metric authority", "missing Metric semantics")
    authority = decode_fold_authority(incoming.fold_authority)
    if "time" in shape and authority.time_grain() is None:
        raise correlation_error("bound temporal grain", "missing lag coordinate authority")
    sampled = bool(decode_comparison_basis(comparison_basis(dataset)).sampling_definition)
    semantics = AssociationSemantics(
        _token=d._CORE_TOKEN,
        method=method,
        input_shape=shape,
        metric_keys=tuple(
            f.identity.identity_id
            for f in metrics
            if isinstance(f.identity, d._CatalogFieldIdentity)
        ),
        metric_units=tuple(item[1] for item in incoming.metric_bindings),
        approximations=tuple(
            approximation_class(
                sampled=sampled,
                semantic=fold.distribution is not None
                and fold.distribution.quantile.method == "duckdb_tdigest@v1",
            )
            for fold in authority.metrics
        ),
        lag_offsets=lags,
        fold_authority=incoming.fold_authority,
    )
    ids = dataset._registration.ids
    dims = (
        tuple(f for f in dataset.schema.columns if f.role_id == "dimension")
        if shape == "dimension-time"
        else ()
    )
    generated = []
    for name, role, kind, nullable in (*BASE_FIELDS, *(LAG_FIELDS if "time" in shape else ())):
        field_id = d._make_field_id(f"generated.correlate.{name}@v1")
        generated.append(
            d._make_field(
                field_id=field_id,
                name=name,
                role_id=role,
                identity=d._generated_identity(field_id),
                derivation_identity=f"correlate.{name}@v1",
                logical_type_id=kind,
                physical_type_state=d._deferred_type(kind, ids=ids),
                nullable=nullable,
                ids=ids,
            )
        )
    keys = tuple(
        f.field_id
        for f in (*dims, *generated)
        if f in dims or f.name in ("metric_key_a", "metric_key_b", "lag_offset")
    )
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id(
            "association", shape + "-lag" if "time" in shape else shape, 1, ids=ids
        ),
        schema=d._make_schema((*dims, *generated)),
        coordinate_field_ids=keys,
        key_field_ids=keys,
        family_semantics=semantics,
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._static_row_bound(MAX_CANDIDATES)),
        ordering=d._ordered_ordering(
            tuple(
                d._make_order_term(
                    f.field_id,
                    direction="ascending",
                    nulls="last",
                    value_order_contract_id="association.metric_request_order@v1"
                    if f.role_id == "metric_identity"
                    else "association.lag_request_order@v1"
                    if f.name == "lag_offset"
                    else "observation.scalar_order@v1",
                    ids=ids,
                )
                for f in (*dims, *generated)
                if f.field_id in keys
            )
        ),
    )
    result = construct_operator(
        owner=owner_of(dataset),
        registry=dataset._registry,
        operator_id="metric.correlate",
        contract_versions=producer_contract("metric.correlate").versions,
        inputs=(dataset,),
        row_contract=row,
        row_set_contract=rows,
        payload=CorrelatePayload(
            _token=d._CORE_TOKEN,
            spec=CorrelateSpecV1(dataset.row_contract, dataset.row_set_contract, row, rows),
        ),
    )
    if not isinstance(result, LogicalAssociationDataset):
        raise correlation_error("paired Logical Association", "invalid family registration")
    return result


def validate_association(row: d.DatasetRowContract, rows: d.DatasetRowSetContract) -> None:
    s = row.family_semantics
    if (
        not isinstance(s, AssociationSemantics)
        or row.shape_id.local_shape_id not in ASSOCIATION_SHAPES
    ):
        raise correlation_error("closed Association family", "invalid row semantics")
    if s.input_shape not in ("entity", "dimension", "time", "dimension-time") or s.method not in (
        "pearson",
        "spearman",
        "kendall",
    ):
        raise correlation_error("registered input shape and method", "invalid retained method")
    if row.shape_id.local_shape_id != (
        s.input_shape + "-lag" if "time" in s.input_shape else s.input_shape
    ):
        raise correlation_error("shape-derived observation units", "changed input shape")
    n = len(s.metric_keys)
    if (
        not 2 <= n <= 16
        or len(set(s.metric_keys)) != n
        or any(not k.startswith("metric:") for k in s.metric_keys)
        or len(s.metric_units) != n
        or len(s.approximations) != n
    ):
        raise correlation_error("complete ordered Metric identities", "invalid retained bindings")
    from marivo.semantic._quantile import decode_approximation

    for a in s.approximations:
        decode_approximation(a)
    authority = decode_fold_authority(s.fold_authority)
    if tuple("metric:" + item.metric_ref for item in authority.metrics) != s.metric_keys or (
        (authority.time_grain() is not None) != ("time" in s.input_shape)
    ):
        raise correlation_error(
            "Metric bindings and time authority matching the retained fold contract",
            "inconsistent Association source authority",
        )
    if (
        not s.lag_offsets
        or len(set(s.lag_offsets)) != len(s.lag_offsets)
        or candidate_count(len(s.metric_keys), len(s.lag_offsets)) > MAX_CANDIDATES
        or any(type(k) is not int or not -(2**63) <= k < 2**63 for k in s.lag_offsets)
        or ("time" not in s.input_shape and s.lag_offsets != (0,))
    ):
        raise correlation_error("bounded exact lag candidates", "invalid retained lag scope")
    dims = tuple(f for f in row.schema.columns if f.role_id == "dimension")
    if bool(dims) != (s.input_shape == "dimension-time") or any(
        not isinstance(f.identity, d._CatalogFieldIdentity) for f in dims
    ):
        raise correlation_error("shape-exact retained Dimensions", "invalid coordinates")
    fields = {f.name: f for f in row.schema.columns}
    spec = (*BASE_FIELDS, *(LAG_FIELDS if "time" in s.input_shape else ()))
    for name, role, kind, nullable in spec:
        f = fields.get(name)
        if (
            f is None
            or (f.role_id, f.logical_type_id, f.nullable) != (role, kind, nullable)
            or f.field_id.value != f"generated.correlate.{name}@v1"
            or not isinstance(f.identity, d._GeneratedFieldIdentity)
            or f.identity.producer_field_id != f.field_id
        ):
            raise correlation_error("exact generated Association fields", "invalid generated field")
    expected = (*(f.name for f in dims), *(name for name, _, _, _ in spec))
    if "rank" in fields:
        rank = fields["rank"]
        if (
            rank.field_id.value != "generated.rank@v1"
            or rank.role_id != "rank"
            or rank.logical_type_id != "int64"
            or not rank.nullable
        ):
            raise correlation_error("registered nullable rank", "invalid rank field")
        expected = (*expected, "rank")
    keys = tuple(
        fields[name].field_id
        for name in (
            *(f.name for f in dims),
            "metric_key_a",
            "metric_key_b",
            *(("lag_offset",) if "time" in s.input_shape else ()),
        )
    )
    if (
        tuple(fields) != expected
        or row.key_field_ids != keys
        or row.coordinate_field_ids != keys
        or rows.cardinality.kind != "keyed"
    ):
        raise correlation_error(
            "exact ordered fields and pair keys", "invalid Association row contract"
        )
    if not isinstance(rows.ordering, d._OrderedOrdering):
        raise correlation_error("authored or ranked total Association order", "missing row order")
    from marivo.analysis.operators.association_contracts import LAG_ORDER, METRIC_ORDER

    authored = {
        f.field_id: METRIC_ORDER
        if f.role_id == "metric_identity"
        else LAG_ORDER
        if f.name == "lag_offset"
        else "observation.scalar_order@v1"
        for f in row.schema.columns
        if f.field_id in keys
    }
    if "rank" not in fields and (
        tuple(t.field_id for t in rows.ordering.terms) != keys
        or any(
            (t.direction, t.nulls, t.value_order_contract_id)
            != ("ascending", "last", authored[t.field_id])
            for t in rows.ordering.terms
        )
    ):
        raise correlation_error("exact authored Metric and lag order", "changed Association order")
    if any(
        t.value_order_contract_id in (LAG_ORDER, METRIC_ORDER)
        and authored.get(t.field_id) != t.value_order_contract_id
        for t in rows.ordering.terms
    ):
        raise correlation_error(
            "field-specific authored value order", "invalid Association comparator"
        )


def _contract_facts(dataset: Dataset) -> tuple[tuple[str, str], ...]:
    s = dataset.row_contract.family_semantics
    if not isinstance(s, AssociationSemantics):
        raise correlation_error("closed Association meaning", "missing disclosure authority")
    return (
        ("method", s.method),
        ("approximation", ", ".join(dict.fromkeys(s.approximations))),
        ("interpretation", "descriptive/exploratory; no significance or causal claim"),
        ("observation_unit", s.input_shape),
        ("searched_pairs", str(pair_count(len(s.metric_keys)))),
        (
            "searched_lags",
            f"count={len(s.lag_offsets)}; first={s.lag_offsets[0]}; last={s.lag_offsets[-1]}",
        ),
        ("selection", selection_description()),
        ("selection_rule_id", SELECTION_RULE_ID),
    )


def register_association(registry: DatasetFamilyRegistry, ids: d._StableIdRegistry) -> None:
    def decode(state: MaterializedDatasetState) -> MaterializedDatasetState:
        _validate_materialized_state(state, ids=ids)
        return state

    shapes = tuple(d._make_shape_id("association", s, 1, ids=ids) for s in ASSOCIATION_SHAPES)
    registry.register(
        DatasetFamilyRegistration(
            family_id="association",
            logical_type=LogicalAssociationDataset,
            materialized_type=MaterializedAssociationDataset,
            shape_ids=shapes,
            owner_id="operators.correlate",
            ids=ids,
            row_validator=validate_association,
            consumers=tuple(
                ConsumerRegistration(
                    f"association.{m}",
                    ("input",),
                    "association",
                    shapes,
                    ("association.current_rows@v1",),
                )
                for m in ("where", "rank", "limit")
            ),
            repr_renderer=_dataset_repr,
            materialized_state_decoder=decode,
            node_payload_types=(CorrelatePayload, RetainedRowsPayload),
            contract_facts=_contract_facts,
            consumer_admission=lambda dataset, method: (
                not any(f.role_id == "rank" for f in dataset.schema.columns)
                if method == "association.rank"
                else dataset.row_set_contract.ordering.kind == "ordered"
                if method == "association.limit"
                else True
            ),
        )
    )


def association_filterable_field(value: d.DatasetField) -> bool:
    """Admit only the owner's exact generated non-identity predicate fields."""
    return any(
        value.name == name
        and value.field_id.value == f"generated.correlate.{name}@v1"
        and value.role_id == role
        and value.logical_type_id == kind
        and value.nullable == nullable
        and isinstance(value.identity, d._GeneratedFieldIdentity)
        and value.identity.producer_field_id == value.field_id
        for name, role, kind, nullable in (*BASE_FIELDS, *LAG_FIELDS)
        if role != "metric_identity"
    )
