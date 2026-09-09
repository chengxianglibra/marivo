"""Private Metric ranking and ordered-prefix construction."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    _canonical_digest,
    _deferred_type,
    _generated_identity,
    _keyed_cardinality,
    _KeyedCardinality,
    _make_field,
    _make_field_id,
    _make_order_term,
    _make_row_contract,
    _make_row_set_contract,
    _make_schema,
    _ordered_ordering,
    _static_row_bound,
    _StaticRowBound,
)
from marivo.analysis.datasets.fields import DatasetFieldRef, validate_field_ref
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.contracts import (
    MetricPayload,
    RankSpec,
    RetainedRowsPayload,
    construction_error,
    owner_of,
    producer_contract,
)

RankOrder = Literal["ascending", "descending"]
RankTies = Literal["ordinal", "dense", "min", "max"]
RANK_FIELD_ID = _make_field_id("generated.rank@v1")
RANK_SHAPES = frozenset(
    {
        "point-anomaly",
        "interesting-window",
        "period-shift",
        "entity",
        "dimension",
        "time",
        "dimension-time",
        "joint",
        "hierarchy",
        "time-lag",
        "dimension-time-lag",
    }
)
VALUE_ORDER_ID = "observation.scalar_order@v1"


def rank(
    dataset: Dataset,
    by: DatasetFieldRef,
    *,
    order: RankOrder = "descending",
    ties: RankTies = "ordinal",
    partition_by: tuple[DatasetFieldRef, ...] = (),
) -> Dataset:
    """Describe one exact rank and total order without reading current rows."""
    if dataset.row_contract.shape_id.local_shape_id not in RANK_SHAPES:
        raise construction_error("registered non-singleton Metric rank shape", "unsupported shape")
    if order not in ("ascending", "descending") or ties not in ("ordinal", "dense", "min", "max"):
        raise construction_error("registered rank direction and ties", "invalid rank policy")
    if any(field.field_id == RANK_FIELD_ID for field in dataset.schema.columns):
        raise construction_error("rows without an existing generated rank", "repeated rank")
    selected = validate_field_ref(
        dataset, by, allowed_roles=("metric", "rank", "comparison_value", "effect_value")
    )
    if selected.logical_type_id not in (
        "integer",
        "int32",
        "int64",
        "floating",
        "float32",
        "float64",
        "numeric",
        "decimal",
    ) and not selected.logical_type_id.startswith("decimal"):
        raise construction_error("current numeric value field", "non-numeric rank field")
    if type(partition_by) is not tuple:
        raise construction_error("immutable tuple of current key coordinates", "invalid partitions")
    submitted = tuple(validate_field_ref(dataset, item) for item in partition_by)
    if len({field.field_id for field in submitted}) != len(submitted) or any(
        field.field_id not in dataset.row_contract.key_field_ids for field in submitted
    ):
        raise construction_error("distinct current row-key coordinates", "invalid rank partitions")
    partitions = tuple(
        field
        for field in dataset.schema.columns
        if field.field_id in {item.field_id for item in submitted}
    )
    ids = dataset._registration.ids
    spec = RankSpec(selected, order, ties, partitions)
    generated = _make_field(
        field_id=RANK_FIELD_ID,
        name="rank",
        role_id="rank",
        identity=_generated_identity(RANK_FIELD_ID),
        derivation_identity="rank." + _canonical_digest(spec.identity_payload()),
        logical_type_id="int64",
        physical_type_state=_deferred_type("int64", ids=ids),
        nullable=True,
        ids=ids,
    )
    row = _make_row_contract(
        schema_version=dataset.row_contract.schema_version,
        shape_id=dataset.row_contract.shape_id,
        schema=_make_schema((*dataset.schema.columns, generated)),
        coordinate_field_ids=dataset.row_contract.coordinate_field_ids,
        key_field_ids=dataset.row_contract.key_field_ids,
        family_semantics=dataset.row_contract.family_semantics,
    )
    fields = {field.field_id: field for field in row.schema.columns}
    order_ids = tuple(
        dict.fromkeys(
            (
                *(field.field_id for field in partitions),
                RANK_FIELD_ID,
                *row.key_field_ids,
            )
        )
    )
    ordering = _ordered_ordering(
        tuple(
            _make_order_term(
                field_id,
                direction="ascending",
                nulls="last",
                value_order_contract_id=(
                    "observation.identity_tuple@v1"
                    if fields[field_id].role_id == "entity_identity"
                    else VALUE_ORDER_ID
                ),
                ids=ids,
            )
            for field_id in order_ids
        )
    )
    root = dataset._root
    payload: MetricPayload | RetainedRowsPayload
    if isinstance(root, LogicalRootHandle) and isinstance(root.payload, MetricPayload):
        payload = MetricPayload(
            _token=_CORE_TOKEN,
            definition=root.payload.definition,
            captures=(),
            rank=spec,
        )
    else:
        payload = RetainedRowsPayload(_token=_CORE_TOKEN, rank=spec)
    return construct_operator(
        owner=owner_of(dataset),
        registry=dataset._registry,
        operator_id=f"{dataset.kind}.rank",
        contract_versions=producer_contract(f"{dataset.kind}.rank").versions,
        inputs=(dataset,),
        row_contract=row,
        row_set_contract=_make_row_set_contract(
            schema_version=1, cardinality=dataset.row_set_contract.cardinality, ordering=ordering
        ),
        payload=payload,
    )


def limit(dataset: Dataset, count: int) -> Dataset:
    """Keep a bounded prefix of the input's existing logical total order."""
    if type(count) is not int or not 1 <= count <= 100_000:
        raise construction_error("exact integer count in [1, 100000]", "invalid limit count")
    if dataset.row_set_contract.ordering.kind != "ordered":
        raise construction_error(
            "registered deterministic logical ordering",
            "unordered rows",
            repair="Call rank(by=...) on an admitted numeric field before limit(count).",
        )
    cardinality = dataset.row_set_contract.cardinality
    if not isinstance(cardinality, _KeyedCardinality):
        raise construction_error("ordered keyed rows", "singleton limit")
    if isinstance(cardinality.row_bound, _StaticRowBound):
        count = min(count, cardinality.row_bound.max_rows)
    root = dataset._root
    payload: MetricPayload | RetainedRowsPayload
    if isinstance(root, LogicalRootHandle) and isinstance(root.payload, MetricPayload):
        definition = replace(
            root.payload.definition,
            selection_boundaries=(
                *root.payload.definition.selection_boundaries,
                dataset.definition_fingerprint,
            ),
        )
        payload = MetricPayload(
            _token=_CORE_TOKEN, definition=definition, captures=(), limit_count=count
        )
    else:
        payload = RetainedRowsPayload(_token=_CORE_TOKEN, limit_count=count)
    return construct_operator(
        owner=owner_of(dataset),
        registry=dataset._registry,
        operator_id=f"{dataset.kind}.limit",
        contract_versions=producer_contract(f"{dataset.kind}.limit").versions,
        inputs=(dataset,),
        row_contract=dataset.row_contract,
        row_set_contract=_make_row_set_contract(
            schema_version=1,
            cardinality=_keyed_cardinality(_static_row_bound(count)),
            ordering=dataset.row_set_contract.ordering,
        ),
        payload=payload,
    )
