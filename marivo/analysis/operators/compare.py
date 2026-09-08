"""Private exact Metric comparison construction and bounded pandas arithmetic."""

from __future__ import annotations

import math
from dataclasses import replace
from decimal import Decimal, InvalidOperation, localcontext
from functools import cmp_to_key

import pandas as pd
import pyarrow as pa

from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset, _dataset_repr, _validate_input_ownership
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetField,
    DatasetRowContract,
    DatasetRowSetContract,
    _CatalogFieldIdentity,
    _deferred_type,
    _EntityFieldIdentity,
    _generated_identity,
    _GeneratedFieldIdentity,
    _keyed_cardinality,
    _make_field,
    _make_field_id,
    _make_row_contract,
    _make_row_set_contract,
    _make_schema,
    _make_shape_id,
    _singleton_cardinality,
    _StableIdRegistry,
    _unknown_row_bound,
    _unordered_ordering,
)
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
from marivo.analysis.operators.contracts import (
    DEFAULT_ALIGNMENT,
    DELTA_SHAPES,
    ComparePayload,
    CompareSpecV1,
    DeltaSemantics,
    WindowBucketAlignment,
    comparison_basis,
    decode_comparison_basis,
)
from marivo.analysis.operators.delta import LogicalDeltaDataset, MaterializedDeltaDataset
from marivo.analysis.operators.errors import comparison_error
from marivo.analysis.operators.row_values import compare_value, frame_keys, row_key_names

_GENERATED = (
    ("coordinate_presence", "status", "string", False),
    ("current_value", "comparison_value", "numeric", True),
    ("baseline_value", "comparison_value", "numeric", True),
    ("delta", "comparison_value", "numeric", True),
    ("relative_delta", "effect_value", "float64", True),
    ("calculation_status", "status", "string", False),
    ("relative_delta_status", "status", "string", False),
)


def promoted_numeric_type(logical_type: str) -> str:
    if logical_type in ("integer", "int8", "int16", "int32", "int64"):
        return "int64"
    if logical_type in ("floating", "float32", "float64"):
        return "float64"
    if logical_type == "decimal":
        return "decimal"
    raise comparison_error(
        "registered lossless signed numeric input", "unsupported Metric value type"
    )


def _field_signature(field: DatasetField) -> tuple[object, ...]:
    return (
        field.field_id,
        field.role_id,
        field.identity,
        field.derivation_identity,
        field.logical_type_id,
        field.nullable,
    )


def _generated(
    name: str, role: str, logical_type: str, nullable: bool, ids: _StableIdRegistry
) -> DatasetField:
    field_id = _make_field_id(f"generated.compare.{name}@v1")
    return _make_field(
        field_id=field_id,
        name=name,
        role_id=role,
        identity=_generated_identity(field_id),
        derivation_identity=f"compare.{name}@v1",
        logical_type_id=logical_type,
        physical_type_state=_deferred_type(logical_type, ids=ids),
        nullable=nullable,
        ids=ids,
    )


def compare(
    current: Dataset, baseline: Dataset, *, alignment: WindowBucketAlignment = DEFAULT_ALIGNMENT
) -> LogicalDeltaDataset:
    """Bind two compatible exact input authorities without performing data work."""
    from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset

    if type(current) not in (LogicalMetricDataset, MaterializedMetricDataset) or type(
        baseline
    ) not in (LogicalMetricDataset, MaterializedMetricDataset):
        raise comparison_error(
            "Logical or Materialized Metric operands", "unsupported operand family"
        )
    _validate_input_ownership(current._owner, (current, baseline))
    if type(alignment) is not WindowBucketAlignment:
        raise comparison_error("window_bucket() alignment", "unsupported alignment policy")
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
    if not isinstance(a.identity, _CatalogFieldIdentity):
        raise comparison_error("exact retained Metric identity", "unsupported Metric identity")
    semantics = DeltaSemantics(
        _token=_CORE_TOKEN,
        metric_ref=a.identity.identity_id.split(":", 1)[1],
        metric_unit=left.metric_bindings[0][1],
        numeric_type=promoted,
        exact_empty_zero=left.metric_bindings[0][5] == "zero",
        approximation_class="sampled_population"
        if current_authority.sampling_definition
        else "exact",
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
    result = construct_operator(
        owner=owner_of(current),
        registry=current._registry,
        operator_id="metric.compare",
        contract_versions=producer_contract("metric.compare").versions,
        inputs=(current, baseline),
        row_contract=row,
        row_set_contract=rows,
        payload=ComparePayload(_token=_CORE_TOKEN, spec=spec),
    )
    if not isinstance(result, LogicalDeltaDataset):
        raise comparison_error("paired Logical Delta", "invalid family registration")
    return result


def validate_delta(row: DatasetRowContract, rows: DatasetRowSetContract) -> None:
    semantics = row.family_semantics
    if not isinstance(semantics, DeltaSemantics) or row.shape_id.local_shape_id not in DELTA_SHAPES:
        raise comparison_error("exact Delta shape and semantics", "invalid Delta contract")
    if semantics.numeric_type not in ("int64", "float64", "decimal"):
        raise comparison_error("canonical promoted Delta numeric type", "invalid numeric promotion")
    from marivo.analysis.operators.attribution_contracts import delta_part_authorities

    delta_part_authorities(row)
    if semantics.approximation_class not in ("exact", "sampled_population"):
        raise comparison_error(
            "closed comparison approximation class", "invalid approximation meaning"
        )
    shape = row.shape_id.local_shape_id
    coordinates = tuple(
        field
        for field in row.schema.columns
        if field.role_id in ("entity_identity", "dimension", "comparison_coordinate")
    )
    if (
        row.coordinate_field_ids != tuple(field.field_id for field in coordinates)
        or row.key_field_ids != row.coordinate_field_ids
    ):
        raise comparison_error("complete ordered Delta coordinate key", "invalid Delta key")
    if (rows.cardinality.kind == "singleton") != (shape == "scalar") or (
        shape == "scalar" and any(field.role_id == "rank" for field in row.schema.columns)
    ):
        raise comparison_error("shape-exact Delta cardinality", "invalid singleton contract")
    fields = {field.name: field for field in row.schema.columns}
    for name, role, kind, nullable in _GENERATED:
        field = fields.get(name)
        if (
            field is None
            or not isinstance(field.identity, _GeneratedFieldIdentity)
            or field.identity.producer_field_id != field.field_id
            or field.field_id.value != f"generated.compare.{name}@v1"
            or field.role_id != role
            or field.logical_type_id != (semantics.numeric_type if kind == "numeric" else kind)
            or field.nullable != nullable
        ):
            raise comparison_error(
                "exact generated Delta fields", "invalid comparison field contract"
            )
    times = "time" in shape
    if times != (
        semantics.current_time_field_name is not None
        and semantics.baseline_time_field_name is not None
    ):
        raise comparison_error("shape-exact paired time authority", "invalid time context")
    if times:
        if (semantics.current_time_field_name, semantics.baseline_time_field_name) != (
            "current_time",
            "baseline_time",
        ):
            raise comparison_error(
                "exact generated paired time fields", "invalid temporal field names"
            )
        for name in ("current_time", "baseline_time"):
            temporal = fields.get(name)
            if (
                temporal is None
                or temporal.role_id != "comparison_time"
                or temporal.field_id.value != f"generated.compare.{name}@v1"
                or not isinstance(temporal.identity, _GeneratedFieldIdentity)
                or temporal.identity.producer_field_id != temporal.field_id
            ):
                raise comparison_error(
                    "exact paired temporal value fields", "invalid comparison time field"
                )
        if fields["current_time"].logical_type_id != fields["baseline_time"].logical_type_id:
            raise comparison_error(
                "same temporal logical type on both operands", "mismatched paired time types"
            )
        ordinal = fields.get("comparison_ordinal")
        if (
            ordinal is None
            or ordinal.field_id.value != "generated.compare.comparison_ordinal@v1"
            or ordinal.logical_type_id != "int64"
            or ordinal.nullable
            or ordinal.field_id not in row.key_field_ids
        ):
            raise comparison_error(
                "non-null int64 ordinal comparison coordinate", "invalid time key"
            )
    identities = tuple(field for field in coordinates if field.role_id == "entity_identity")
    dimensions = tuple(field for field in coordinates if field.role_id == "dimension")
    ordinals = tuple(field for field in coordinates if field.role_id == "comparison_coordinate")
    if (
        bool(identities) != (shape == "entity")
        or len(identities) > 1
        or bool(dimensions) != ("dimension" in shape)
        or len(ordinals) != int(times)
        or coordinates != (*identities, *dimensions, *ordinals)
        or any(not isinstance(field.identity, _CatalogFieldIdentity) for field in dimensions)
        or any(
            not isinstance(field.identity, _EntityFieldIdentity)
            or field.nullable
            or field.logical_type_id != "identity_tuple"
            for field in identities
        )
    ):
        raise comparison_error("shape-exact Delta coordinates", "invalid coordinate roles")
    expected_names = (
        *(field.name for field in coordinates),
        *(("current_time", "baseline_time") if times else ()),
        *(name for name, _, _, _ in _GENERATED),
    )
    if "rank" in fields:
        rank = fields["rank"]
        if (
            rank.field_id.value != "generated.rank@v1"
            or rank.role_id != "rank"
            or rank.logical_type_id != "int64"
            or not rank.nullable
            or not isinstance(rank.identity, _GeneratedFieldIdentity)
            or rank.identity.producer_field_id != rank.field_id
        ):
            raise comparison_error("exact nullable generated rank", "invalid Delta rank")
        expected_names = (*expected_names, "rank")
    if tuple(fields) != expected_names:
        raise comparison_error(
            "ordered coordinates and generated Delta fields", "invalid field order"
        )


def register_delta(registry: DatasetFamilyRegistry, ids: _StableIdRegistry) -> None:
    def decode(state: MaterializedDatasetState) -> MaterializedDatasetState:
        _validate_materialized_state(state, ids=ids)
        return state

    shapes = tuple(_make_shape_id("delta", shape, 1, ids=ids) for shape in DELTA_SHAPES)
    non_scalar = tuple(shape for shape in shapes if shape.local_shape_id != "scalar")
    registry.register(
        DatasetFamilyRegistration(
            family_id="delta",
            logical_type=LogicalDeltaDataset,
            materialized_type=MaterializedDeltaDataset,
            shape_ids=shapes,
            owner_id="operators.compare",
            ids=ids,
            row_validator=validate_delta,
            consumers=(
                *(
                    ConsumerRegistration(
                        f"delta.{method}",
                        ("input",),
                        "delta",
                        non_scalar,
                        ("delta.current_rows@v1", "delta.sufficient_components@v1"),
                    )
                    for method in ("where", "rank", "limit")
                ),
                ConsumerRegistration(
                    "delta.attribute",
                    ("input",),
                    "attribution",
                    shapes,
                    ("delta.current_rows@v1", "delta.sufficient_components@v1"),
                ),
                ConsumerRegistration(
                    "delta.attribute_expanded",
                    ("input", "current", "baseline"),
                    "attribution",
                    shapes,
                    ("delta.current_rows@v1", "delta.sufficient_components@v1"),
                    discoverable=False,
                    operand_shape_ids=(
                        shapes,
                        registry.get("metric").shape_ids,
                        registry.get("metric").shape_ids,
                    ),
                ),
            ),
            repr_renderer=_dataset_repr,
            materialized_state_decoder=decode,
            node_payload_types=(ComparePayload, RetainedRowsPayload),
            consumer_admission=lambda dataset, method: (
                not any(field.role_id == "rank" for field in dataset.schema.columns)
                if method == "delta.rank"
                else dataset.row_set_contract.ordering.kind == "ordered"
                if method == "delta.limit"
                else True
            ),
        )
    )


def _missing(value: object) -> bool:
    return value is None or value is pd.NA or value is pd.NaT


def _number(value: object, promoted: str) -> int | float | Decimal | None:
    if _missing(value):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise comparison_error("one exact numeric scalar", "invalid input scalar")
    finite = (
        value.is_finite()
        if isinstance(value, Decimal)
        else True
        if isinstance(value, int)
        else math.isfinite(value)
    )
    if not finite:
        raise comparison_error("finite non-null Metric values", "non-finite comparison input")
    if promoted == "int64":
        if not isinstance(value, int) or not -(2**63) <= value < 2**63:
            raise comparison_error(
                "lossless signed int64 input", "integer type mismatch or overflow"
            )
        return value
    if promoted == "float64":
        converted = float(value)
        if not math.isfinite(converted) or (
            isinstance(value, (int, Decimal)) and converted != value
        ):
            raise comparison_error("lossless float64 input", "unrepresentable numeric promotion")
        return converted
    if not isinstance(value, Decimal):
        raise comparison_error("exact Decimal input", "decimal type mismatch")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise comparison_error("finite Decimal input", "invalid decimal exponent")
    scale = max(0, -exponent)
    precision = max(len(value.as_tuple().digits) + max(exponent, 0), scale)
    if precision > 38 or scale > 38:
        raise comparison_error("Decimal precision and scale within 38", "decimal range overflow")
    return value


def _subtract(
    current: int | float | Decimal, baseline: int | float | Decimal, promoted: str
) -> int | float | Decimal:
    if isinstance(current, Decimal) and isinstance(baseline, Decimal):
        try:
            with localcontext() as context:
                context.prec = 80
                result: int | float | Decimal = current - baseline
        except InvalidOperation as exc:
            raise comparison_error(
                "exact representable decimal subtraction", "decimal arithmetic failure"
            ) from exc
    elif (isinstance(current, int) and isinstance(baseline, int)) or (
        isinstance(current, float) and isinstance(baseline, float)
    ):
        result = current - baseline
    else:
        raise comparison_error("equal promoted numeric types", "incompatible subtraction operands")
    checked = _number(result, promoted)
    if checked is None:
        raise comparison_error("non-null numeric difference", "invalid subtraction")
    return checked


def _relative(
    delta: int | float | Decimal | None, baseline: int | float | Decimal | None
) -> tuple[float | None, str]:
    if delta is None or baseline is None:
        return None, "delta_unavailable"
    if baseline == 0:
        return None, "baseline_zero"
    try:
        numerator, denominator = float(delta), abs(float(baseline))
        value = numerator / denominator
    except (OverflowError, ZeroDivisionError):
        return None, "delta_unavailable"
    if not math.isfinite(numerator) or not math.isfinite(denominator) or not math.isfinite(value):
        return None, "delta_unavailable"
    return value, "ok"


def execute_compare(
    current: pd.DataFrame, baseline: pd.DataFrame, spec: CompareSpecV1
) -> pd.DataFrame:
    """Consume two completely guarded inputs without mutating either frame."""
    current_values = [
        _number(value, spec.promoted_type) for value in current[spec.current_metric_name].tolist()
    ]
    baseline_values = [
        _number(value, spec.promoted_type) for value in baseline[spec.baseline_metric_name].tolist()
    ]
    left_keys, right_keys = (
        frame_keys(current, row_key_names(spec.current_row)),
        frame_keys(baseline, row_key_names(spec.baseline_row)),
    )
    if len(set(left_keys)) != len(left_keys) or len(set(right_keys)) != len(right_keys):
        raise comparison_error("unique complete comparison input keys", "duplicate coordinate")
    shape = spec.output_row.shape_id.local_shape_id
    if shape == "scalar" and (len(current) != 1 or len(baseline) != 1):
        raise comparison_error("one explicit scalar row per operand", "invalid scalar cardinality")
    pairs: list[tuple[tuple[object, ...], int | None, int | None]] = []
    if "time" in shape:
        left_groups: dict[tuple[object, ...], list[int]] = {}
        right_groups: dict[tuple[object, ...], list[int]] = {}
        for keys, groups in ((left_keys, left_groups), (right_keys, right_groups)):
            for index, key in enumerate(keys):
                groups.setdefault(key[:-1], []).append(index)
        for key in sorted(set(left_groups) | set(right_groups), key=cmp_to_key(compare_value)):
            left_indices, right_indices = left_groups.get(key, []), right_groups.get(key, [])
            if len(left_indices) != len(right_indices):
                raise comparison_error(
                    "equal complete bucket counts per Dimension series",
                    "unequal window bucket counts",
                )

            def compare_left(a: int, b: int) -> int:
                return compare_value(left_keys[a][-1], left_keys[b][-1])

            def compare_right(a: int, b: int) -> int:
                return compare_value(right_keys[a][-1], right_keys[b][-1])

            left_indices.sort(key=cmp_to_key(compare_left))
            right_indices.sort(key=cmp_to_key(compare_right))
            pairs.extend(
                ((*key, ordinal), a, b)
                for ordinal, (a, b) in enumerate(zip(left_indices, right_indices, strict=True))
            )
    else:
        left_positions, right_positions = (
            {key: index for index, key in enumerate(left_keys)},
            {key: index for index, key in enumerate(right_keys)},
        )
        pairs = [
            (key, left_positions.get(key), right_positions.get(key))
            for key in sorted(
                set(left_positions) | set(right_positions), key=cmp_to_key(compare_value)
            )
        ]
    records: list[dict[str, object]] = []
    zero: int | float | Decimal = (
        Decimal(0)
        if spec.promoted_type == "decimal"
        else 0.0
        if spec.promoted_type == "float64"
        else 0
    )
    for key, a, b in pairs:
        presence = "baseline_only" if a is None else "current_only" if b is None else "matched"
        c = (zero if spec.exact_empty_zero else None) if a is None else current_values[a]
        v = (zero if spec.exact_empty_zero else None) if b is None else baseline_values[b]
        status = (
            "missing_side"
            if presence != "matched" and not spec.exact_empty_zero
            else "null_input"
            if c is None or v is None
            else "ok"
        )
        delta = _subtract(c, v, spec.promoted_type) if c is not None and v is not None else None
        relative, relative_status = _relative(delta, v)
        record: dict[str, object] = dict(zip(row_key_names(spec.output_row), key, strict=True))
        if "time" in shape:
            record.update(
                current_time=None if a is None else left_keys[a][-1],
                baseline_time=None if b is None else right_keys[b][-1],
            )
        record.update(
            coordinate_presence=presence,
            current_value=c,
            baseline_value=v,
            delta=delta,
            relative_delta=relative,
            calculation_status=status,
            relative_delta_status=relative_status,
        )
        records.append(record)
    output: dict[str, pd.Series] = {}
    for field in spec.output_row.schema.columns:
        values = [record[field.name] for record in records]
        if field.logical_type_id == "decimal":
            decimals = [
                value
                for record in records
                for name in ("current_value", "baseline_value", "delta")
                if isinstance((value := record[name]), Decimal)
            ]
            exponents = (value.as_tuple().exponent for value in decimals)
            scale = max(
                (max(0, -value) for value in exponents if isinstance(value, int)), default=0
            )
            dtype = pd.ArrowDtype(pa.decimal128(38, scale))
        elif field.logical_type_id in ("int64", "float64", "string"):
            dtype = pd.ArrowDtype(
                {"int64": pa.int64(), "float64": pa.float64(), "string": pa.string()}[
                    field.logical_type_id
                ]
            )
        else:
            source_field = next(
                (
                    item
                    for item in spec.current_row.schema.columns
                    if item.field_id == field.field_id
                ),
                None,
            )
            if source_field is not None:
                output[field.name] = pd.Series(values, dtype=current[source_field.name].dtype)
                continue
            source_name = next(
                item.name
                for item in spec.current_row.schema.columns
                if item.role_id == "time_dimension"
            )
            output[field.name] = pd.Series(values, dtype=current[source_name].dtype)
            continue
        try:
            output[field.name] = pd.Series(values, dtype=dtype)
        except (ValueError, pa.ArrowException) as exc:
            raise comparison_error(
                "lossless complete output representation", "numeric precision or scale overflow"
            ) from exc
    # Pair construction already owns the exact canonical output-key order.
    return pd.DataFrame(output)
