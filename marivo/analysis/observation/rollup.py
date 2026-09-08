"""Current-row Entity reduction and normalized time-then-Dimension rollup."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from marivo._temporal import Grain, TemporalResolver
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetFieldId,
    _canonical_digest,
    _CatalogFieldIdentity,
    _deferred_type,
    _EntityFieldIdentity,
    _keyed_cardinality,
    _make_field_id,
    _make_row_contract,
    _make_row_set_contract,
    _make_schema,
    _make_shape_id,
    _singleton_cardinality,
    _unknown_row_bound,
    _unordered_ordering,
)
from marivo.analysis.observation.contracts import (
    DimensionInput,
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
    construction_error,
    owner_of,
    producer_contract,
    retained_field,
)
from marivo.analysis.observation.fold_contracts import (
    BUILTIN_GRAIN_MONTHS,
    BUILTIN_GRAIN_SECONDS,
    FoldSpecV1,
    RetainedFoldPayload,
    decode_fold_authority,
    grain_authority,
)
from marivo.refs import Ref
from marivo.semantic.catalog import DimensionEntry

_REPAIR = "Author and execute a fresh observation at the target coordinates; retained origins cannot be replayed."


def _coarser(dataset: Dataset, grain: Grain) -> None:
    semantics = dataset.row_contract.family_semantics
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise construction_error("exact retained temporal authority", "invalid Metric semantics")
    source = semantics.fold_time_grain
    valid = False
    if source is not None and isinstance(grain, Grain) and source != grain:
        if source.kind == "builtin" and grain.kind == "builtin":
            su, tu = source.unit or "", grain.unit or ""
            sc, tc = source.count or 0, grain.count or 0
            if su in BUILTIN_GRAIN_SECONDS and tu in BUILTIN_GRAIN_SECONDS:
                small, large = BUILTIN_GRAIN_SECONDS[su] * sc, BUILTIN_GRAIN_SECONDS[tu] * tc
                valid = bool(small and large > small and large % small == 0)
                # Weeks have a distinct Monday origin from other fixed periods.
                if tu == "week":
                    valid = valid and (su == "week" or 86400 % small == 0)
            elif su in BUILTIN_GRAIN_MONTHS and tu in BUILTIN_GRAIN_MONTHS:
                small, large = BUILTIN_GRAIN_MONTHS[su] * sc, BUILTIN_GRAIN_MONTHS[tu] * tc
                valid = bool(small and large > small and large % small == 0)
            elif su in BUILTIN_GRAIN_SECONDS and tu in BUILTIN_GRAIN_MONTHS:
                width = BUILTIN_GRAIN_SECONDS[su] * sc
                valid = bool(width and width <= 86400 and 86400 % width == 0)
        elif grain.kind == "semantic":
            snapshot = semantics.fold_temporal_snapshot
            if (
                snapshot is not None
                and grain.calendar == snapshot.calendar_ref
                and grain.level is not None
            ):
                if (
                    source.kind == "semantic"
                    and source.calendar == grain.calendar
                    and source.level is not None
                ):
                    valid = TemporalResolver(snapshot).rolls_up_to(source.level, grain.level)
                elif source.kind == "builtin":
                    width = BUILTIN_GRAIN_SECONDS.get(source.unit or "", 0) * (source.count or 0)
                    valid = bool(width and width <= 86400 and 86400 % width == 0)
    if not valid:
        raise construction_error(
            "strictly coarser grain with exact bucket containment",
            "incompatible grain or calendar",
            repair=_REPAIR,
        )


def _admit(
    dataset: Dataset,
    axis: Literal["entity", "dimension", "time"],
    dropped: tuple[DatasetFieldId, ...],
) -> None:
    semantics = dataset.row_contract.family_semantics
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise construction_error(
            "complete retained Metric fold authority", "missing fold contract", repair=_REPAIR
        )
    columns = {field.field_id: field for field in dataset.schema.columns}
    for metric in semantics.metric_folds:
        partitions = dict(metric.axis_partitions)
        for component in metric.components:
            merge = component.time_merge if axis == "time" else component.spatial_merge
            if merge == "blocked" or (axis == "entity" and component.cumulative):
                raise construction_error(
                    "an exact registered component fold on every removed axis",
                    f"{metric.metric_ref}:{component.node_id}: unsupported {axis} fold",
                    repair=_REPAIR,
                )
            if merge == "sum":
                folded_fields = (
                    tuple(
                        field.field_id
                        for field in dataset.schema.columns
                        if field.role_id == "time_dimension"
                    )
                    if axis == "time"
                    else dropped
                )
                for field_id in folded_fields:
                    identity = columns[field_id].identity
                    key = (
                        "entity_identity"
                        if isinstance(identity, _EntityFieldIdentity)
                        else identity.identity_id.partition(":")[2]
                        if isinstance(identity, _CatalogFieldIdentity)
                        else ""
                    )
                    if partitions.get(key) not in ("disjoint", "functional"):
                        raise construction_error(
                            "disjoint or conserving allocated contribution partition",
                            f"{metric.metric_ref}: overlapping or unproved {axis} contributions",
                            repair=_REPAIR,
                        )


def _fold(
    dataset: Dataset,
    *,
    axis: Literal["entity", "dimension", "time"],
    dropped: tuple[DatasetFieldId, ...],
    grain: Grain | None = None,
    drop_time: bool = False,
) -> Dataset:
    _admit(dataset, axis, dropped)
    semantics = dataset.row_contract.family_semantics
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise construction_error("Metric semantics", "invalid semantics")
    ids = dataset._registration.ids
    authority = decode_fold_authority(semantics.fold_authority)
    columns = [
        field
        for field in dataset.schema.columns
        if field.field_id not in dropped and field.role_id != "rank"
    ]
    coordinate_semantics = [
        item for item in semantics.coordinate_semantics if item[0] not in dropped
    ]
    if grain is not None:
        old = next(field for field in columns if field.role_id == "time_dimension")
        new_id = _make_field_id(
            f"time.{_canonical_digest((old.field_id.value, grain_authority(grain)))[:32]}@v1"
        )
        new = replace(
            old,
            _token=_CORE_TOKEN,
            field_id=new_id,
            derivation_identity=_canonical_digest(
                (old.derivation_identity, grain_authority(grain))
            ),
            physical_type_state=_deferred_type(old.logical_type_id, ids=ids),
        )
        columns[columns.index(old)] = new
        coordinate_semantics = [
            (new_id, grain.to_token(), parameters)
            if field_id == old.field_id
            else (field_id, kind, parameters)
            for field_id, kind, parameters in coordinate_semantics
        ]
    authority = authority.model_copy(
        update={
            "grain": grain_authority(grain)
            if grain is not None
            else None
            if drop_time
            else authority.grain
        }
    )
    output_semantics: EntityPresentMetricSemantics | EntityReducedMetricSemantics
    if axis == "entity":
        identity = dataset.schema.columns[0].identity
        if not isinstance(identity, _EntityFieldIdentity):
            raise construction_error("complete Entity identity", "missing identity")
        output_semantics = EntityReducedMetricSemantics(
            _token=_CORE_TOKEN,
            reduced_entity_ref=identity.entity_ref.path,
            reduced_identity_signature=identity.identity_signature,
            metric_bindings=semantics.metric_bindings,
            coordinate_semantics=tuple(coordinate_semantics),
            fold_authority=authority.to_json(),
        )
    else:
        output_semantics = replace(
            semantics,
            _token=_CORE_TOKEN,
            coordinate_semantics=tuple(coordinate_semantics),
            fold_authority=authority.to_json(),
        )
    coordinates = tuple(
        field.field_id
        for field in columns
        if field.role_id in ("entity_identity", "dimension", "time_dimension")
    )
    shape = (
        "-".join(
            part
            for part, role in (
                ("entity", "entity_identity"),
                ("dimension", "dimension"),
                ("time", "time_dimension"),
            )
            if any(field.role_id == role for field in columns)
        )
        or "scalar"
    )
    row = _make_row_contract(
        schema_version=1,
        shape_id=_make_shape_id("metric", shape, 1, ids=ids),
        schema=_make_schema(tuple(columns)),
        coordinate_field_ids=coordinates,
        key_field_ids=coordinates,
        family_semantics=output_semantics,
    )
    row_set = _make_row_set_contract(
        schema_version=1,
        cardinality=_singleton_cardinality()
        if shape == "scalar"
        else _keyed_cardinality(_unknown_row_bound()),
        ordering=_unordered_ordering(),
    )
    operator = "metric.aggregate" if axis == "entity" else "metric.rollup"
    spec = FoldSpecV1(axis, dropped, grain, drop_time, dataset.row_contract, row)
    return construct_operator(
        owner=owner_of(dataset),
        registry=dataset._registry,
        operator_id=operator,
        contract_versions=producer_contract(operator).versions,
        inputs=(dataset,),
        row_contract=row,
        row_set_contract=row_set,
        payload=RetainedFoldPayload(_token=_CORE_TOKEN, spec=spec),
    )


def retained_aggregate(dataset: Dataset) -> Dataset:
    if not isinstance(dataset.row_contract.family_semantics, EntityPresentMetricSemantics):
        raise construction_error("Entity axis present before reduction", "already reduced Metric")
    return _fold(dataset, axis="entity", dropped=(dataset.schema.columns[0].field_id,))


def rollup(
    dataset: Dataset,
    *,
    drop_dimensions: tuple[DimensionInput, ...] = (),
    grain: Grain | None = None,
    drop_time: bool = False,
) -> Dataset:
    if not isinstance(dataset.row_contract.family_semantics, EntityReducedMetricSemantics):
        raise construction_error(
            "Entity-reduced Metric shape",
            "Entity is still present",
            repair="Call aggregate() before rolling up coordinates.",
        )
    if type(drop_dimensions) is not tuple or type(drop_time) is not bool:
        raise construction_error(
            "immutable Dimension tuple and exact Boolean drop_time", "invalid rollup arguments"
        )
    if not drop_dimensions and grain is None and not drop_time:
        raise construction_error("at least one coordinate reduction", "empty rollup request")
    if grain is not None and drop_time:
        raise construction_error("grain or drop_time exclusively", "conflicting time reductions")
    if any(type(item) not in (Ref, DimensionEntry) for item in drop_dimensions):
        raise construction_error(
            "exact retained Dimension refs or catalog entries", "invalid dropped Dimension selector"
        )
    selected = tuple(retained_field(dataset, item) for item in drop_dimensions)
    if any(field.role_id != "dimension" for field in selected) or len(
        {field.field_id for field in selected}
    ) != len(selected):
        raise construction_error("distinct exact retained Dimensions", "invalid dropped Dimensions")
    times = tuple(field for field in dataset.schema.columns if field.role_id == "time_dimension")
    current = dataset
    if grain is not None or drop_time:
        if not times:
            raise construction_error("retained time coordinate", "time reduction without time")
        if grain is not None:
            _coarser(dataset, grain)
        current = _fold(
            current,
            axis="time",
            dropped=tuple(field.field_id for field in times) if drop_time else (),
            grain=grain,
            drop_time=drop_time,
        )
    if selected:
        current = _fold(
            current, axis="dimension", dropped=tuple(field.field_id for field in selected)
        )
    return current
