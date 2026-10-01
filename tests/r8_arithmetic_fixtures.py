"""Frozen arithmetic fixtures for actual R7/R8 helpers; no registered producer or Runtime."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    _CatalogFieldIdentity,
    _keyed_cardinality,
    _make_row_contract,
    _make_row_set_contract,
    _make_schema,
    _make_shape_id,
    _RuntimeMetricFieldIdentity,
    _singleton_cardinality,
    _unknown_row_bound,
    _unordered_ordering,
)
from marivo.analysis.observation.contracts import (
    IDENTITY_FIELD_ID,
    DimensionInput,
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
)
from marivo.analysis.observation.fold_contracts import decode_fold_authority
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.operators.attribute import GENERATED, _axis_ref, attribute_method
from marivo.analysis.operators.attribute import _generated as attribute_field
from marivo.analysis.operators.attribution_contracts import (
    INDEPENDENT_RESOLUTION_METHODS,
    AttributeSpecV1,
    AttributionSemantics,
    delta_part_authorities,
)
from marivo.analysis.operators.candidate_contracts import CandidateSemantics, CandidateSpecV1
from marivo.analysis.operators.compare import (
    _GENERATED,
    _field_signature,
    _generated,
    promoted_numeric_type,
)
from marivo.analysis.operators.contracts import (
    DELTA_SHAPES,
    CompareSpecV1,
    DeltaSemantics,
    comparison_basis,
    decode_comparison_basis,
)
from marivo.analysis.operators.driver_axes import DRIVER_FIELDS, validate_driver_definition
from marivo.analysis.operators.driver_contracts import (
    DriverCandidateDefinition,
    DriverCandidateSpecV1,
)
from marivo.analysis.operators.errors import attribution_error, comparison_error
from marivo.analysis.operators.errors import driver_error as discovery_error
from marivo.semantic._quantile import approximation_class


def comparison_for_metric(current: LogicalMetricDataset) -> CompareSpecV1:
    baseline = current
    shape = current.row_contract.shape_id.local_shape_id
    if shape not in DELTA_SHAPES or current.row_contract.shape_id != baseline.row_contract.shape_id:
        raise comparison_error(
            "one identical admitted Metric shape", "incompatible comparison shapes"
        )
    current_values = tuple(field for field in current.schema.columns if field.role_id == "metric")
    baseline_values = tuple(field for field in baseline.schema.columns if field.role_id == "metric")
    if len(current_values) != 1 or len(baseline_values) != 1:
        raise comparison_error(
            "exactly one Metric per input",
            "multi-Metric input",
            repair="Select each input with dataset.metric(metric_ref) before compare().",
        )
    a, b = current_values[0], baseline_values[0]
    left, right = current.row_contract.family_semantics, baseline.row_contract.family_semantics
    if not isinstance(
        left, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)
    ) or not isinstance(right, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise comparison_error("exact retained Metric contracts", "unsupported row semantics")
    if (
        _field_signature(a) != _field_signature(b)
        or left.metric_bindings != right.metric_bindings
        or left.metric_folds != right.metric_folds
    ):
        raise comparison_error(
            "identical Metric identity, type, unit and aggregation", "incompatible Metric contracts"
        )
    if (
        isinstance(left, EntityReducedMetricSemantics)
        and isinstance(right, EntityReducedMetricSemantics)
        and (left.reduced_entity_ref, left.reduced_identity_signature)
        != (right.reduced_entity_ref, right.reduced_identity_signature)
    ):
        raise comparison_error("identical reduced Entity authority", "different Entity contracts")
    current_coordinates = tuple(
        field
        for field in current.schema.columns
        if field.field_id in current.row_contract.coordinate_field_ids
    )
    baseline_coordinates = tuple(
        field
        for field in baseline.schema.columns
        if field.field_id in baseline.row_contract.coordinate_field_ids
    )
    if (
        tuple(_field_signature(item) for item in current_coordinates)
        != tuple(_field_signature(item) for item in baseline_coordinates)
        or left.coordinate_semantics != right.coordinate_semantics
    ):
        raise comparison_error(
            "identical ordered coordinate contracts", "incompatible coordinate identity or grain"
        )
    current_basis, baseline_basis = comparison_basis(current), comparison_basis(baseline)
    current_authority, baseline_authority = (
        decode_comparison_basis(current_basis),
        decode_comparison_basis(baseline_basis),
    )
    if current_authority.model_copy(
        update={"observation_scope": None}
    ) != baseline_authority.model_copy(update={"observation_scope": None}):
        raise comparison_error(
            "same Population membership, sampling and non-time selection",
            "incompatible comparison scope",
        )
    promoted = promoted_numeric_type(a.logical_type_id)
    ids = current._registration.ids
    current_time = next(
        (field for field in current_coordinates if field.role_id == "time_dimension"), None
    )
    baseline_time = next(
        (field for field in baseline_coordinates if field.role_id == "time_dimension"), None
    )
    coordinates = tuple(field for field in current_coordinates if field.role_id != "time_dimension")
    columns = list(coordinates)
    if current_time is not None and baseline_time is not None:
        ordinal = _generated("comparison_ordinal", "comparison_coordinate", "int64", False, ids)
        coordinates = (*coordinates, ordinal)
        columns.extend(
            (
                ordinal,
                _generated(
                    "current_time",
                    "comparison_time",
                    current_time.logical_type_id,
                    current_time.nullable,
                    ids,
                ),
                _generated(
                    "baseline_time",
                    "comparison_time",
                    baseline_time.logical_type_id,
                    baseline_time.nullable,
                    ids,
                ),
            )
        )
    if current_time is not None and baseline_time is not None:
        columns = [
            replace(
                field,
                _token=_CORE_TOKEN,
                derivation_identity=f"{field.derivation_identity}:{current_time.derivation_identity if field.name == 'current_time' else baseline_time.derivation_identity}",
            )
            if field.name in ("current_time", "baseline_time")
            and field.role_id == "comparison_time"
            else field
            for field in columns
        ]
    columns.extend(
        _generated(name, role, promoted if kind == "numeric" else kind, nullable, ids)
        for name, role, kind, nullable in _GENERATED
    )
    if len({field.name for field in columns}) != len(columns):
        raise comparison_error(
            "unambiguous retained coordinate and generated comparison names",
            "coordinate name collides with a generated comparison field",
        )
    if not isinstance(a.identity, (_CatalogFieldIdentity, _RuntimeMetricFieldIdentity)):
        raise comparison_error("exact retained Metric identity", "unsupported Metric identity")
    semantics = DeltaSemantics(
        _token=_CORE_TOKEN,
        metric_ref=(
            a.identity.identity_id.split(":", 1)[1]
            if isinstance(a.identity, _CatalogFieldIdentity)
            else a.identity.identity_id
        ),
        metric_unit=left.metric_bindings[0][1],
        numeric_type=promoted,
        exact_empty_zero=left.metric_bindings[0][5] == "zero",
        approximation_class=approximation_class(
            sampled=bool(current_authority.sampling_definition),
            semantic=any(
                item.distribution is not None
                and item.distribution.quantile.method == "duckdb_tdigest@v1"
                for item in left.metric_folds
            ),
        ),
        current_time_field_name=None if current_time is None else "current_time",
        baseline_time_field_name=None if baseline_time is None else "baseline_time",
        current_fold_authority=decode_fold_authority(left.fold_authority)
        .model_copy(update={"scope": None})
        .to_json(),
        baseline_fold_authority=decode_fold_authority(right.fold_authority)
        .model_copy(update={"scope": None})
        .to_json(),
    )
    row = _make_row_contract(
        schema_version=1,
        shape_id=_make_shape_id("delta", shape, 1, ids=ids),
        schema=_make_schema(tuple(columns)),
        coordinate_field_ids=tuple(field.field_id for field in coordinates),
        key_field_ids=tuple(field.field_id for field in coordinates),
        family_semantics=semantics,
    )
    rows = _make_row_set_contract(
        schema_version=1,
        cardinality=_singleton_cardinality()
        if shape == "scalar"
        else _keyed_cardinality(_unknown_row_bound()),
        ordering=_unordered_ordering(),
    )
    spec = CompareSpecV1(
        current.row_contract,
        current.row_set_contract,
        baseline.row_contract,
        baseline.row_set_contract,
        row,
        rows,
        a.name,
        b.name,
        promoted,
        semantics.exact_empty_zero,
        current_basis,
        baseline_basis,
    )
    return spec


def attribution_for_spec(
    compare: CompareSpecV1,
    metric: LogicalMetricDataset,
    axes: tuple[DimensionInput, ...],
    *,
    mode: Literal["joint", "hierarchy"] = "joint",
    top_k: int | None = None,
) -> AttributeSpecV1:
    input_row, input_rows = compare.output_row, compare.output_rows
    refs = tuple(_axis_ref(axis) for axis in axes)
    known = {
        f.identity.identity_id.split(":", 1)[1]: f
        for f in input_row.schema.columns
        if f.role_id == "dimension" and isinstance(f.identity, _CatalogFieldIdentity)
    }
    expanded_compare = original_input_row = None
    semantics = input_row.family_semantics
    if not isinstance(semantics, DeltaSemantics):
        raise attribution_error("exact expanded Delta semantics", "invalid expansion")
    axis_fields = tuple(known[axis.path] for axis in refs)
    authority_pairs = delta_part_authorities(input_row)
    methods = tuple(
        attribute_method(authority, tuple(axis.path for axis in refs))
        for _, authority in authority_pairs
    )
    if len(set(methods)) != 1:
        raise attribution_error("same method on both comparison sides", "incompatible side folds")
    method = methods[0]
    if method == "distribution_shapley@v1":
        if authority_pairs[0][1].distribution != authority_pairs[1][1].distribution:
            raise attribution_error(
                "identical percentile methods and parameters", "incompatible distribution sides"
            )
        if any(field.role_id == "entity_identity" for field in input_row.schema.columns):
            raise attribution_error(
                "non-identity distribution Attribution", "source-required Entity scope"
            )
    axis_ids = tuple(field.field_id for field in axis_fields)
    scope = tuple(
        field
        for field in input_row.schema.columns
        if field.field_id in input_row.coordinate_field_ids and field.field_id not in axis_ids
    )
    ids = metric._registration.ids
    numeric_type = semantics.numeric_type if method == "additive_difference@v1" else "float64"
    output_axes = tuple(replace(field, _token=_CORE_TOKEN, nullable=True) for field in axis_fields)
    times = tuple(field for field in input_row.schema.columns if field.role_id == "comparison_time")
    generated = tuple(
        attribute_field(
            name,
            role,
            f"bool_tuple:{len(axes)}"
            if kind == "mask"
            else numeric_type
            if kind == "numeric"
            else kind,
            nullable,
            ids,
        )
        for name, role, kind, nullable in GENERATED
    )
    columns = (*scope, *output_axes, *times, *generated)
    if len({field.name for field in columns}) != len(columns):
        raise attribution_error(
            "unambiguous scope, axes and generated field names", "attribution field collision"
        )
    active_id, other_id = generated[0].field_id, generated[1].field_id
    scope_ids = tuple(field.field_id for field in scope)
    output_semantics = AttributionSemantics(
        _token=_CORE_TOKEN,
        metric_ref=semantics.metric_ref,
        metric_unit=semantics.metric_unit,
        numeric_type=numeric_type,
        scope_field_ids=scope_ids,
        axis_field_ids=axis_ids,
        resolution_prefixes=(axis_ids,)
        if mode == "joint"
        else tuple(axis_ids[:index] for index in range(1, len(axes) + 1)),
        current_time_field_name=semantics.current_time_field_name,
        baseline_time_field_name=semantics.baseline_time_field_name,
        method=method,
        approximation_class=semantics.approximation_class,
        resolution_semantics="independent"
        if method in INDEPENDENT_RESOLUTION_METHODS
        else "rollup",
        rollup_safe=method not in INDEPENDENT_RESOLUTION_METHODS,
    )
    row = _make_row_contract(
        schema_version=1,
        shape_id=_make_shape_id("attribution", mode, 1, ids=ids),
        schema=_make_schema(columns),
        coordinate_field_ids=(*scope_ids, active_id, *axis_ids, other_id),
        key_field_ids=(
            *scope_ids,
            *((active_id,) if mode == "hierarchy" else ()),
            *axis_ids,
            other_id,
        ),
        family_semantics=output_semantics,
    )
    rows = _make_row_set_contract(
        schema_version=1,
        cardinality=_keyed_cardinality(_unknown_row_bound()),
        ordering=_unordered_ordering(),
    )
    spec = AttributeSpecV1(
        input_row,
        input_rows,
        row,
        rows,
        axis_fields,
        scope,
        method,
        mode,
        top_k,
        semantics.current_fold_authority,
        semantics.baseline_fold_authority,
        expanded_compare,
        original_input_row,
    )
    return spec


def driver_for_spec(
    compare: CompareSpecV1,
    metric: LogicalMetricDataset,
    axes: tuple[DimensionInput, ...],
    *,
    limit: int = 50,
) -> DriverCandidateSpecV1:
    input_row, input_rows = compare.output_row, compare.output_rows
    refs = tuple(_axis_ref(axis) for axis in axes)
    paths = tuple(axis.path for axis in refs)
    known = {
        f.identity.identity_id.split(":", 1)[1]: f
        for f in input_row.schema.columns
        if f.role_id == "dimension" and isinstance(f.identity, _CatalogFieldIdentity)
    }
    if any(axis.path not in known for axis in refs):
        projected = metric.with_dimensions(*axes)
        known.update(
            (field.identity.identity_id.split(":", 1)[1], field)
            for field in projected.schema.columns
            if field.role_id == "dimension" and isinstance(field.identity, _CatalogFieldIdentity)
        )
    incoming = original = input_row.family_semantics
    assert isinstance(incoming, DeltaSemantics)
    expanded_compare = original_input_row = None
    axis_fields = tuple(known[axis.path] for axis in refs)
    axis_ids = tuple(f.field_id for f in axis_fields)
    scope = tuple(
        f
        for f in input_row.schema.columns
        if f.field_id in input_row.coordinate_field_ids and f.field_id not in axis_ids
    )
    times = tuple(f for f in input_row.schema.columns if f.role_id == "comparison_time")
    definition = DriverCandidateDefinition(
        "logical",
        metric.definition_fingerprint,
        limit,
        original.approximation_class,
        original.current_fold_authority,
        original.baseline_fold_authority,
        d._metric_identity_id(original.metric_ref),
        original.metric_unit,
        paths,
        scope,
        times,
    )
    validate_driver_definition(definition)
    from marivo.analysis.operators.discovery import COMMON_FIELDS, _generated

    ids = metric._registration.ids
    common = tuple(_generated("driver_axes", name, kind, ids) for name, kind in COMMON_FIELDS)
    values = tuple(_generated("driver_axes", name, kind, ids) for name, kind in DRIVER_FIELDS)
    columns = (*common, *scope, *times, *values)
    if len({f.name for f in columns}) != len(columns):
        raise discovery_error("unambiguous scope and generated names", "Candidate field collision")
    semantics = CandidateSemantics(
        _token=d._CORE_TOKEN,
        objective="driver_axes",
        method_id="axis_concentration@v1",
        approximation=definition.approximation,
        item_id_field_id=common[0].field_id,
        score_field_id=common[1].field_id,
        reason_codes_field_id=common[2].field_id,
    )
    keys = (*(f.field_id for f in scope), values[0].field_id)
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id("candidate", "driver-axis", 1, ids=ids),
        schema=d._make_schema(columns),
        coordinate_field_ids=keys,
        key_field_ids=keys,
        family_semantics=semantics,
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._static_row_bound(limit)),
        ordering=d._ordered_ordering(
            tuple(
                d._make_order_term(
                    key,
                    direction="descending" if key == semantics.score_field_id else "ascending",
                    nulls="last",
                    value_order_contract_id="observation.identity_tuple@v1"
                    if key == IDENTITY_FIELD_ID
                    else "observation.scalar_order@v1",
                    ids=ids,
                )
                for key in (semantics.score_field_id, *keys, semantics.item_id_field_id)
            )
        ),
    )
    spec = DriverCandidateSpecV1(
        input_row,
        input_rows,
        row,
        rows,
        definition,
        axis_fields,
        scope,
        incoming.current_fold_authority,
        incoming.baseline_fold_authority,
        expanded_compare,
        original_input_row,
        None,
    )
    return spec


def period_candidate_for_metric(
    metric: LogicalMetricDataset, *, threshold: float = 1.0, limit: int = 50
) -> CandidateSpecV1:
    from marivo.analysis.operators.candidate_contracts import (
        METHODS,
        SHAPES,
        CandidateDefinition,
        CandidateSpecV1,
    )
    from marivo.analysis.operators.discovery import (
        COMMON_FIELDS,
        TIME_FIELDS,
        VALUE_FIELDS,
        _generated,
        validate_definition,
    )

    compare = comparison_for_metric(metric)
    input_row, input_rows = compare.output_row, compare.output_rows
    incoming = input_row.family_semantics
    assert isinstance(incoming, DeltaSemantics)
    objective = "period_shifts"
    ids = metric._registration.ids
    definition = CandidateDefinition(
        objective,
        METHODS[objective],
        "logical",
        metric.definition_fingerprint,
        threshold,
        limit,
        incoming.approximation_class,
        incoming.current_fold_authority,
        incoming.baseline_fold_authority,
        d._metric_identity_id(incoming.metric_ref),
        incoming.metric_unit,
    )
    validate_definition(definition)
    dimensions = tuple(f for f in input_row.schema.columns if f.role_id == "dimension")
    generated = tuple(_generated(objective, name, kind, ids) for name, kind in COMMON_FIELDS)
    values = tuple(_generated(objective, name, kind, ids) for name, kind in VALUE_FIELDS[objective])
    time = next(
        field
        for field in input_row.schema.columns
        if field.role_id == "comparison_time" and field.name == "current_time"
    )
    baseline_time = next(
        field
        for field in input_row.schema.columns
        if field.role_id == "comparison_time" and field.name == "baseline_time"
    )
    temporal = tuple(
        _generated(
            objective,
            name,
            baseline_time.logical_type_id if name.startswith("baseline_") else time.logical_type_id,
            ids,
        )
        for name in TIME_FIELDS[objective]
    )
    columns = (*generated, *dimensions, *temporal, *values)
    coordinates = (*dimensions, *temporal)
    if len({f.name for f in columns}) != len(columns):
        raise discovery_error(
            "unambiguous retained and generated names", "Candidate field collision"
        )
    keys = tuple(field.field_id for field in coordinates)
    semantics = CandidateSemantics(
        _token=d._CORE_TOKEN,
        objective=objective,
        method_id=definition.method_id,
        approximation=definition.approximation,
        item_id_field_id=generated[0].field_id,
        score_field_id=generated[1].field_id,
        reason_codes_field_id=generated[2].field_id,
    )
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id("candidate", SHAPES[objective], 1, ids=ids),
        schema=d._make_schema(columns),
        coordinate_field_ids=tuple(f.field_id for f in coordinates),
        key_field_ids=keys,
        family_semantics=semantics,
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._static_row_bound(limit)),
        ordering=d._ordered_ordering(
            tuple(
                d._make_order_term(
                    field_id,
                    direction="descending" if field_id == semantics.score_field_id else "ascending",
                    nulls="last",
                    value_order_contract_id=(
                        "observation.identity_tuple@v1"
                        if field_id == IDENTITY_FIELD_ID
                        else "observation.scalar_order@v1"
                    ),
                    ids=ids,
                )
                for field_id in (semantics.score_field_id, *keys, semantics.item_id_field_id)
            )
        ),
    )
    spec = CandidateSpecV1(input_row, input_rows, row, rows, definition)
    return spec
