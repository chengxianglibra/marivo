"""Local reductions over already completed internal Metric sample values."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo._definition.expressions import PerReduce, PerWeightedMean
from marivo.analysis.core.data_types import DataType, DecimalDataType
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
        ("sample.zero", "a nonzero denominator", "weighted_mean(zero_denominator='error')"),
        (
            "sample.numeric",
            "finite values in the admitted numeric type",
            "internal sample reduction overflow",
        ),
        ("sample.order", "one internal sample per time key", "first/last duplicate time keys"),
    )

    def total(items: list[object]) -> int | float | Decimal:
        numeric = [value for value in items if isinstance(value, (int, float, Decimal))]
        if len(numeric) != len(items):
            numeric_failure("typed numeric internal sample values", "non-numeric sample")
        return (
            math.fsum(float(value) for value in numeric)
            if any(isinstance(value, float) for value in numeric)
            else sum(numeric, 0)
        )

    records: list[dict[str, object]] = []
    for _, coordinate in domain.iterrows():
        selected = pd.Series(True, index=frame.index)
        for key in keys:
            value = coordinate[key]
            selected &= frame[key].isna() if pd.isna(value) else (frame[key] == value).fillna(False)
        group = frame.loc[selected]
        record: dict[str, object] = {key: coordinate[key] for key in keys}
        for name, _, _ in inherited:
            record[name] = bool(group[name].astype("boolean").fillna(False).any())
        record.update(
            {
                "sample.null": False,
                "sample.zero": False,
                "sample.numeric": False,
                "sample.order": False,
            }
        )
        series = group[value_names[0]]
        record["sample.null"] = node.nulls == "error" and bool(series.isna().any())
        present = series.dropna().tolist()
        result: object = None
        try:
            with localcontext() as context:
                context.prec = 120
                if isinstance(node, PerWeightedMean):
                    weight = group[value_names[1]]
                    record["sample.null"] = node.nulls == "error" and bool(
                        (series.isna() | weight.isna()).any()
                    )
                    pairs = [
                        (value, mass)
                        for value, mass in zip(series, weight, strict=True)
                        if pd.notna(value) and pd.notna(mass)
                    ]
                    denominator = total([mass for _, mass in pairs])
                    record["sample.zero"] = denominator == 0 and node.zero_denominator == "error"
                    if denominator != 0:
                        numerator = total(
                            [
                                float(value) * float(mass)
                                if isinstance(value, float) or isinstance(mass, float)
                                else value * mass
                                for value, mass in pairs
                            ]
                        )
                        if exact_duration:
                            result = Fraction(numerator) / Fraction(denominator)
                        elif isinstance(numerator, float) or isinstance(denominator, float):
                            result = float(numerator) / float(denominator)
                        else:
                            result = numerator / denominator
                elif node.reducer == "sum":
                    result = total(present) if present or node.empty == "zero" else None
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
                        ordered = ordered.loc[ordered[value_names[0]].notna()]
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
                        result = min(present) if node.reducer == "min" else max(present)
                    elif node.reducer in ("approx_median", "approx_quantile"):
                        q = node.q if node.q is not None else 0.5
                        result = pc.tdigest(pa.array([float(value) for value in present]), q=q)[
                            0
                        ].as_py()
                    else:
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
                with localcontext() as context:
                    context.prec = 120
                    finished = Decimal(str(result)).quantize(
                        Decimal(1).scaleb(-output_type.scale), rounding=ROUND_HALF_EVEN
                    )
                record["sample.numeric"] = finished.copy_abs() >= Decimal(1).scaleb(
                    output_type.precision - output_type.scale
                )
                result = finished
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
