"""Private exact Metric comparison construction and bounded pandas arithmetic."""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation, localcontext
from functools import cmp_to_key

import pandas as pd
import pyarrow as pa

from marivo.analysis.datasets.descriptors import (
    DatasetField,
    DatasetRowContract,
    DatasetRowSetContract,
    _CatalogFieldIdentity,
    _deferred_type,
    _EntityFieldIdentity,
    _generated_identity,
    _GeneratedFieldIdentity,
    _make_field,
    _make_field_id,
    _StableIdRegistry,
)
from marivo.analysis.operators.contracts import (
    DELTA_SHAPES,
    CompareSpecV1,
    DeltaSemantics,
)
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
    if logical_type == "unknown":
        return "unknown"
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


def validate_delta(row: DatasetRowContract, rows: DatasetRowSetContract) -> None:
    semantics = row.family_semantics
    if not isinstance(semantics, DeltaSemantics) or row.shape_id.local_shape_id not in DELTA_SHAPES:
        raise comparison_error("exact Delta shape and semantics", "invalid Delta contract")
    if semantics.numeric_type not in ("unknown", "int64", "float64", "decimal"):
        raise comparison_error("canonical promoted Delta numeric type", "invalid numeric promotion")
    from marivo.analysis.operators.attribution_contracts import delta_part_authorities

    delta_part_authorities(row)
    if semantics.approximation_class not in (
        "exact",
        "sampled_population",
        "semantic_percentile",
        "sampled_semantic_percentile",
    ):
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


def _missing(value: object) -> bool:
    return value is None or value is pd.NA or value is pd.NaT


def _number(value: object, promoted: str) -> int | float | Decimal | None:
    if _missing(value):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise comparison_error("one exact numeric scalar", "invalid input scalar")
    if promoted == "unknown":
        promoted = (
            "decimal"
            if isinstance(value, Decimal)
            else "float64"
            if isinstance(value, float)
            else "int64"
        )
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
    if isinstance(value, int) and promoted == "decimal":
        value = Decimal(value)
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


def _observed_numeric_type(series: pd.Series) -> str:
    """Resolve an undeclared Metric type from its materialized values."""
    dtype = series.dtype
    arrow_type = dtype.pyarrow_dtype if isinstance(dtype, pd.ArrowDtype) else None
    if arrow_type is not None:
        if pa.types.is_decimal(arrow_type):
            return "decimal"
        if pa.types.is_floating(arrow_type):
            return "float64"
        if pa.types.is_integer(arrow_type):
            return "int64"
    if pd.api.types.is_float_dtype(dtype):
        return "float64"
    if pd.api.types.is_integer_dtype(dtype) and not pd.api.types.is_bool_dtype(dtype):
        return "int64"
    values = tuple(value for value in series.tolist() if not _missing(value))
    if not values:
        raise comparison_error(
            "an observed numeric Metric type", "cannot infer a type from an empty untyped result"
        )
    if any(isinstance(value, bool) for value in values):
        raise comparison_error("an observed numeric Metric type", "Boolean Metric values")
    has_decimal = any(isinstance(value, Decimal) for value in values)
    has_float = any(isinstance(value, float) for value in values)
    if has_decimal and has_float:
        raise comparison_error("lossless observed numeric promotion", "Decimal and float values")
    if has_decimal:
        return "decimal"
    if has_float:
        return "float64"
    if all(isinstance(value, int) for value in values):
        return "int64"
    raise comparison_error("an observed numeric Metric type", "unsupported materialized values")


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
    current: pd.DataFrame,
    baseline: pd.DataFrame,
    spec: CompareSpecV1,
    *,
    ordinal_preassigned: bool = False,
) -> pd.DataFrame:
    """Consume two completely guarded inputs without mutating either frame."""
    promoted_type = spec.promoted_type
    if promoted_type == "unknown":
        left_type = _observed_numeric_type(current[spec.current_metric_name])
        right_type = _observed_numeric_type(baseline[spec.baseline_metric_name])
        if "decimal" in (left_type, right_type) and "float64" in (left_type, right_type):
            raise comparison_error(
                "lossless observed numeric promotion", "Decimal and float Metrics"
            )
        promoted_type = (
            "decimal"
            if "decimal" in (left_type, right_type)
            else "float64"
            if "float64" in (left_type, right_type)
            else "int64"
        )
    current_values = [
        _number(value, promoted_type) for value in current[spec.current_metric_name].tolist()
    ]
    baseline_values = [
        _number(value, promoted_type) for value in baseline[spec.baseline_metric_name].tolist()
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
    if "time" in shape and ordinal_preassigned:
        anchored: list[dict[tuple[object, ...], int]] = []
        for frame, keys in ((current, left_keys), (baseline, right_keys)):
            positions: dict[tuple[object, ...], int] = {}
            if "comparison_ordinal" not in frame:
                raise comparison_error("original comparison ordinals", "missing expansion anchor")
            for index, (key, ordinal) in enumerate(
                zip(keys, frame.comparison_ordinal.tolist(), strict=True)
            ):
                if type(ordinal) is not int or ordinal < 0:
                    raise comparison_error("nonnegative exact ordinal", "invalid expansion anchor")
                anchored_key = (*key[:-1], ordinal)
                if anchored_key in positions:
                    raise comparison_error("unique expanded ordinal keys", "duplicate expansion")
                positions[anchored_key] = index
            anchored.append(positions)
        left_positions, right_positions = anchored
        pairs = [
            (key, left_positions.get(key), right_positions.get(key))
            for key in sorted(
                set(left_positions) | set(right_positions), key=cmp_to_key(compare_value)
            )
        ]
    elif "time" in shape:
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
        Decimal(0) if promoted_type == "decimal" else 0.0 if promoted_type == "float64" else 0
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
        delta = _subtract(c, v, promoted_type) if c is not None and v is not None else None
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
        field_type = (
            promoted_type
            if field.logical_type_id == "unknown" and field.role_id == "comparison_value"
            else field.logical_type_id
        )
        if field_type == "decimal":
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
        elif field_type in ("int64", "float64", "string"):
            dtype = pd.ArrowDtype(
                {"int64": pa.int64(), "float64": pa.float64(), "string": pa.string()}[field_type]
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
                source_dtype = current[source_field.name].dtype
                if field.logical_type_id == "unknown" and not isinstance(
                    source_dtype, pd.ArrowDtype
                ):
                    source_dtype = pd.ArrowDtype(pa.array(values, from_pandas=True).type)
                output[field.name] = pd.Series(values, dtype=source_dtype)
                continue
            source_name = next(
                item.name
                for item in spec.current_row.schema.columns
                if item.role_id == "time_dimension"
            )
            source_dtype = current[source_name].dtype
            if field.logical_type_id == "unknown" and not isinstance(source_dtype, pd.ArrowDtype):
                source_dtype = pd.ArrowDtype(pa.array(values, from_pandas=True).type)
            output[field.name] = pd.Series(values, dtype=source_dtype)
            continue
        try:
            output[field.name] = pd.Series(values, dtype=dtype)
        except (ValueError, pa.ArrowException) as exc:
            raise comparison_error(
                "lossless complete output representation", "numeric precision or scale overflow"
            ) from exc
    # Pair construction already owns the exact canonical output-key order.
    return pd.DataFrame(output)
