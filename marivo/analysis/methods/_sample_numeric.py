"""Local reductions over already completed internal Metric sample values."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo._definition.expressions import PerReduce, PerWeightedMean
from marivo.analysis.core.data_types import DataType, DecimalDataType
from marivo.analysis.core.data_validation import typed_null_mask
from marivo.analysis.methods.numeric_state import _decimal_context
from marivo.semantic._numeric_lowering import numeric_failure


def reduce_samples(
    node: PerReduce | PerWeightedMean,
    frame: pd.DataFrame,
    keys: tuple[str, ...],
    domain: pd.DataFrame,
    value_names: tuple[str, ...],
    inherited: tuple[tuple[str, str, str], ...],
    *,
    order_key: str | None = None,
    exact_duration: bool = False,
    output_type: DataType,
) -> tuple[pd.DataFrame, tuple[tuple[str, str, str], ...]]:
    """Reduce each complete sample identity, retaining checks for outer demand."""
    metadata = (
        *inherited,
        (
            "sample.null",
            "non-NA internal samples",
            f"{node.reducer if isinstance(node, PerReduce) else 'weighted_mean'}(nulls='error')",
        ),
        (
            "sample.numeric",
            "finite values in the admitted numeric type",
            "internal sample reduction overflow",
        ),
        ("sample.order", "one internal sample per time key", "first/last duplicate time keys"),
    )

    def finite(items: list[object]) -> list[int | float | Decimal]:
        numeric = [value for value in items if isinstance(value, (int, float, Decimal))]
        if len(numeric) != len(items):
            numeric_failure("typed numeric internal sample values", "non-numeric sample")
        if any(
            (isinstance(value, float) and not math.isfinite(value))
            or (isinstance(value, Decimal) and not value.is_finite())
            for value in numeric
        ):
            raise ArithmeticError("internal sample inputs must remain finite")
        return numeric

    def total(items: list[object]) -> int | float | Decimal:
        numeric = finite(items)
        result = (
            math.fsum(float(value) for value in numeric)
            if any(isinstance(value, float) for value in numeric)
            else sum(numeric, 0)
        )
        if (isinstance(result, float) and not math.isfinite(result)) or (
            isinstance(result, Decimal) and not result.is_finite()
        ):
            raise ArithmeticError("internal sample arithmetic must remain finite")
        return result

    records: list[dict[str, object]] = []
    # Build the complete-key index once. Arrow scalars retain exact integers,
    # Decimal values and temporal ticks, including valid minimum time values.
    sample_arrays: list[pa.Array] = []
    domain_arrays: list[pa.Array] = []
    for key in keys:
        samples = pa.array(frame[key], from_pandas=True)
        coordinates = pa.array(domain[key], from_pandas=True)
        carrier = coordinates.type if pa.types.is_null(samples.type) else samples.type
        sample_arrays.append(samples.cast(carrier, safe=True))
        domain_arrays.append(coordinates.cast(carrier, safe=True))

    def key_at(arrays: list[pa.Array], position: int) -> tuple[pa.Scalar | None, ...]:
        return tuple(
            cell if cell.is_valid else None for array in arrays for cell in (array[position],)
        )

    groups: dict[tuple[pa.Scalar | None, ...], list[int]] = {}
    for sample_position in range(len(frame)):
        groups.setdefault(key_at(sample_arrays, sample_position), []).append(sample_position)
    for domain_position in range(len(domain)):
        group = frame.iloc[groups.get(key_at(domain_arrays, domain_position), [])]
        record: dict[str, object] = {key: domain[key].iloc[domain_position] for key in keys}
        for name, _, _ in inherited:
            record[name] = bool(group[name].astype("boolean").fillna(False).any())
        record.update(
            {
                "sample.null": False,
                "sample.numeric": False,
                "sample.order": False,
            }
        )
        series = group[value_names[0]]
        missing = typed_null_mask(series)
        record["sample.null"] = node.nulls == "error" and bool(missing.any())
        present = series.loc[~missing].tolist()
        result: object = None
        try:
            with localcontext(_decimal_context()):
                if isinstance(node, PerWeightedMean):
                    weight = group[value_names[1]]
                    weight_missing = typed_null_mask(weight)
                    record["sample.null"] = node.nulls == "error" and bool(
                        (missing | weight_missing).any()
                    )
                    # Validate both demanded inputs before the common pair mask.
                    # A missing counterpart cannot hide a valid nonfinite value.
                    finite(present)
                    finite(weight.loc[~weight_missing].tolist())
                    pairs = [
                        (value, mass)
                        for value, mass, absent, mass_absent in zip(
                            series, weight, missing, weight_missing, strict=True
                        )
                        if not absent and not mass_absent
                    ]
                    denominator = total([mass for _, mass in pairs])
                    numerator = total(
                        [
                            float(value) * float(mass)
                            if isinstance(value, float) or isinstance(mass, float)
                            else value * mass
                            for value, mass in pairs
                        ]
                    )
                    if denominator != 0:
                        if exact_duration:
                            result = Fraction(numerator) / Fraction(denominator)
                        elif isinstance(numerator, float) or isinstance(denominator, float):
                            result = float(numerator) / float(denominator)
                        else:
                            result = numerator / denominator
                elif node.reducer == "sum":
                    result = total(present) if present or group.empty else None
                elif node.reducer in ("count", "count_distinct"):
                    result = len(present) if node.reducer == "count" else len(set(present))
                elif node.reducer == "approx_nunique":
                    numeric_failure(
                        "a qualified approximate internal distinct route",
                        "no qualified local approximate distinct kernel",
                    )
                elif node.reducer in ("first", "last"):
                    assert order_key is not None
                    record["sample.order"] = bool(group[order_key].duplicated().any())
                    ordered = group.sort_values(order_key, kind="stable")
                    if not node.include_null:
                        ordered = ordered.loc[~typed_null_mask(ordered[value_names[0]])]
                    if len(ordered):
                        result = ordered[value_names[0]].iloc[0 if node.reducer == "first" else -1]
                elif present:
                    if node.reducer == "mean":
                        result = (
                            Fraction(total(present)) / len(present)
                            if exact_duration
                            else total(present) / len(present)
                        )
                    elif node.reducer in ("min", "max"):
                        finite(present)
                        result = min(present) if node.reducer == "min" else max(present)
                    elif node.reducer in ("approx_median", "approx_quantile"):
                        finite(present)
                        q = node.q if node.q is not None else 0.5
                        result = pc.tdigest(pa.array([float(value) for value in present]), q=q)[
                            0
                        ].as_py()
                    else:
                        finite(present)
                        ordered_values = sorted(present)
                        q = node.q if node.q is not None else 0.5
                        position = (len(ordered_values) - 1) * Fraction(str(q))
                        low, high = math.floor(position), math.ceil(position)
                        fraction = (
                            Decimal((position - low).numerator)
                            / Decimal((position - low).denominator)
                            if isinstance(ordered_values[low], Decimal)
                            else position - low
                            if exact_duration
                            else float(position - low)
                        )
                        result = (
                            ordered_values[low]
                            + (ordered_values[high] - ordered_values[low]) * fraction
                        )
        except (ArithmeticError, TypeError, ValueError):
            record["sample.numeric"] = True
        if (
            isinstance(output_type, DecimalDataType)
            and result is not None
            and not record["sample.numeric"]
        ):
            try:
                with localcontext(_decimal_context()):
                    finished = Decimal(str(result)).quantize(
                        Decimal(1).scaleb(-output_type.scale), rounding=ROUND_HALF_UP
                    )
                result = pa.scalar(
                    finished, type=pa.decimal128(output_type.precision, output_type.scale)
                ).as_py()
            except (ArithmeticError, ValueError):
                record["sample.numeric"] = True
        if (
            exact_duration
            and isinstance(result, (int, float, Decimal, Fraction))
            and not -(2**63) <= round(result) < 2**63
        ):
            record["sample.numeric"] = True
        if (
            (isinstance(result, int) and not -(2**63) <= result < 2**63)
            or (isinstance(result, float) and not math.isfinite(result))
            or (isinstance(result, Decimal) and not result.is_finite())
        ):
            record["sample.numeric"] = True
        record["value"] = None if record["sample.numeric"] else result
        records.append(record)
    return pd.DataFrame(
        records, columns=[*keys, "value", *(name for name, _, _ in metadata)], dtype=object
    ), metadata
