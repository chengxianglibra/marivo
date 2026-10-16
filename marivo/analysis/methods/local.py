"""Registered local methods over verified current-row Cells."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.graph_plan import LocalCompute, LocalMethodStage
from marivo.analysis.core.data_types import DecimalDataType, ScalarDataType
from marivo.analysis.core.model import Cell, Defined, Null, Undefined, Unknown, reject
from marivo.analysis.core.rules import AssociationScore, RowState
from marivo.analysis.methods.association_numeric import score
from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType


@dataclass(frozen=True, slots=True)
class CountResult:
    cell: Defined
    count: int


@dataclass(frozen=True, slots=True)
class ArithmeticResult:
    cell: Defined | Undefined
    current_sum: int | float
    current_count: int


@dataclass(frozen=True, slots=True)
class SpearmanResult:
    status: str
    coefficient: float | None
    input_observation_count: int
    matched_observation_count: int
    null_pair_count: int
    complete_pair_count: int


def score_spearman(
    stage: LocalMethodStage,
    left: pd.DataFrame,
    right: pd.DataFrame,
    keys: tuple[str, ...],
    schemas: tuple[pa.Schema, pa.Schema],
) -> SpearmanResult:
    """Pair exact retained keys and score complete finite Cells with average ranks."""
    if (
        not isinstance(stage.node.parameters, AssociationScore)
        or stage.implementation.key.route not in ("ibis_python",)
        or not keys
    ):
        reject(
            "a qualified local Spearman stage",
            str(stage.node.method),
            "Use registered pair inputs.",
            "analysis.local",
        )
    from marivo.analysis.materialization.cell_arrow import logical_schema
    from marivo.analysis.materialization.graph_exchange import _pandas_records

    expected = {*keys, "value", "cell_tag", "cell_reason"}
    if not expected <= set(logical_schema(schemas[0]).names) or not expected <= set(
        logical_schema(schemas[1]).names
    ):
        reject(
            "two complete keyed Cell inputs",
            "missing fields",
            "Retain the exact pair layout.",
            "analysis.local",
        )
    paired: list[dict[tuple[object, ...], tuple[str, int | float | None]]] = []
    for frame, schema in zip((left, right), schemas, strict=True):
        mapping: dict[tuple[object, ...], tuple[str, int | float | None]] = {}
        for row in _pandas_records(frame, schema):
            identity = tuple(row[key] for key in keys)
            tag, value, reason = row["cell_tag"], row["value"], row["cell_reason"]
            if any(item is None for item in identity) or identity in mapping:
                reject(
                    "unique complete pair keys",
                    repr(identity),
                    "Correct the retained key.",
                    "analysis.local",
                )
            if tag == "defined":
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or reason is not None
                ):
                    reject(
                        "finite Defined pair Cell",
                        repr((tag, value, reason)),
                        "Correct the Cell.",
                        "analysis.local",
                    )
            elif tag == "null":
                if value is not None or reason not in ("source_null", "empty_contribution"):
                    reject(
                        "ordinary Null pair Cell",
                        repr((tag, value, reason)),
                        "Correct the Cell.",
                        "analysis.local",
                    )
            else:
                reject(
                    "Defined or ordinary Null pair Cell",
                    str(tag),
                    "Use a qualified pair policy.",
                    "analysis.local",
                )
            assert isinstance(tag, str) and (value is None or isinstance(value, (int, float)))
            mapping[identity] = (tag, value)
        paired.append(mapping)
    a, b = paired
    if a.keys() != b.keys():
        reject(
            "the same complete pair key set",
            "unmatched keys",
            "Correct the paired observations.",
            "analysis.local",
        )
    xs: list[int | float] = []
    ys: list[int | float] = []
    null_count = 0
    for key, (tag_a, value_a) in a.items():
        tag_b, value_b = b[key]
        if tag_a == "null" or tag_b == "null":
            null_count += 1
            continue
        assert isinstance(value_a, (int, float)) and isinstance(value_b, (int, float))
        xs.append(value_a)
        ys.append(value_b)
    size = len(xs)
    scored = score(tuple(Fraction(x) for x in xs), tuple(Fraction(y) for y in ys), "spearman")
    return SpearmanResult(scored.status, scored.coefficient, len(a), len(a), null_count, size)


def arithmetic(stage: LocalMethodStage, cells: tuple[Cell, ...]) -> ArithmeticResult:
    """Finish admitted int64 or float64 rows after R4 verifies their input."""
    if (
        not isinstance(stage.node.parameters, RowState)
        or stage.node.parameters.method not in ("sum", "mean")
        or stage.implementation.key.route not in ("ibis_python",)
        or type(cells) is not tuple
    ):
        reject(
            "one qualified local current-row sum or mean",
            str(stage.node.method),
            "Use the exact registered local method and validated Cells.",
            "analysis.local",
        )
    input_type = stage.implementation.key.input_types[0]
    floating = input_type == ScalarType("float64")
    integer_values: list[int] = []
    float_values: list[float] = []
    for cell in cells:
        if not isinstance(cell, Defined):
            reject(
                "finite Defined numeric input Cells",
                type(cell).__name__,
                "Select complete numeric Cells.",
                "analysis.local",
            )
        value = cell.value
        if floating:
            if type(value) is not float or not math.isfinite(value):
                reject(
                    "finite float64 input",
                    repr(value),
                    "Correct the retained value.",
                    "analysis.local",
                )
            float_values.append(value)
        else:
            if type(value) is not int or not -(2**63) <= value < 2**63:
                reject(
                    "finite Defined int64 input Cells",
                    repr(value),
                    "Correct the retained value.",
                    "analysis.local",
                )
            if stage.node.parameters.method == "mean" and abs(value) > 2**53:
                reject(
                    "int64 mean operands within exact float64 integer range",
                    str(value),
                    "Use a separately qualified high-precision mean method.",
                    "analysis.local",
                )
            integer_values.append(value)
    total: int | float
    if floating:
        try:
            total = math.fsum(float_values)
        except OverflowError:
            reject(
                "finite float64 current sum",
                "overflow",
                "Use a qualified wider method.",
                "analysis.local",
            )
        if not math.isfinite(total):
            reject(
                "finite float64 current sum",
                repr(total),
                "Use a qualified wider method.",
                "analysis.local",
            )
    else:
        total = sum(integer_values)
        if not -(2**63) <= total < 2**63:
            reject(
                "checked int64 current sum",
                str(total),
                "Use a qualified wider method.",
                "analysis.local",
            )
    if stage.node.parameters.method == "mean":
        cell_result: Defined | Undefined = (
            Defined(float(total) / len(cells)) if cells else Undefined("empty_mean")
        )
    else:
        cell_result = Defined(total)
    return ArithmeticResult(cell_result, total, len(cells))


def count(stage: LocalMethodStage, cells: tuple[Cell, ...]) -> CountResult:
    """Consume validated retained or prepared rows without reading or publishing."""
    if (
        not isinstance(stage.node.parameters, RowState)
        or stage.node.parameters.method != "count"
        or stage.implementation.key.route not in ("ibis_python",)
    ):
        reject(
            "the registered local count stage",
            str(stage.node.method),
            "Use row.count over verified fixed or prepared inputs.",
            "analysis.local",
        )
    limit = stage.implementation.resources.max_rows
    if (
        type(cells) is not tuple
        or any(type(c) not in (Defined, Null, Undefined, Unknown) for c in cells)
        or any(
            isinstance(c, Defined)
            and not (
                (
                    (
                        stage.implementation.key.input_types[0] == ScalarType("int64")
                        or isinstance(stage.implementation.key.input_types[0], DurationType)
                    )
                    and type(c.value) is int
                    and -(2**63) <= c.value < 2**63
                )
                or (
                    stage.implementation.key.input_types[0] == ScalarType("float64")
                    and type(c.value) is float
                    and math.isfinite(c.value)
                )
                or (
                    isinstance(stage.implementation.key.input_types[0], DecimalType)
                    and type(c.value) is Decimal
                    and c.value.is_finite()
                )
                or (
                    stage.implementation.key.input_types[0] == ScalarType("string")
                    and type(c.value) is str
                )
                or (
                    stage.implementation.key.input_types[0] == ScalarType("boolean")
                    and type(c.value) is bool
                )
                or (
                    stage.implementation.key.input_types[0] == ScalarType("date")
                    and type(c.value) is date
                )
                or (
                    stage.implementation.key.input_types[0] == ScalarType("timestamp")
                    and isinstance(c.value, datetime)
                )
            )
            for c in cells
        )
        or (limit is not None and len(cells) > limit)
    ):
        reject(
            f"validated Cells within {limit} rows",
            str(len(cells)),
            "Supply validated retained Cells within the registered resource limit.",
            "analysis.local",
        )
    return CountResult(Defined(len(cells)), len(cells))


def count_defined(stage: LocalMethodStage, cells: tuple[Cell, ...]) -> CountResult:
    """Count validated Defined Cells without a fixed row-cap qualification."""
    if (
        not isinstance(stage.node.parameters, RowState)
        or stage.node.parameters.method != "count_defined"
        or stage.implementation.key.route not in ("ibis_python",)
        or type(cells) is not tuple
        or any(type(cell) not in (Defined, Null, Undefined, Unknown) for cell in cells)
    ):
        reject(
            "a qualified fixed defined-count over validated Cells",
            str(stage.node.method),
            "Use the exact retained four-state input.",
            "analysis.local",
        )
    value = sum(isinstance(cell, Defined) for cell in cells)
    return CountResult(Defined(value), value)


def query_combine(stage: LocalCompute, inputs: tuple[pd.DataFrame, ...]) -> pd.DataFrame:
    """Combine reduced inputs on their complete nullable coordinate keys."""
    from marivo._definition.expressions import (
        ContributionFilter,
        DeclaredMetricRef,
        MetricAdd,
        MetricConstant,
        MetricDivide,
        MetricMultiply,
        MetricNegate,
        MetricNode,
        MetricSubtract,
    )
    from marivo.analysis.compiler.observation import duration_type
    from marivo.analysis.core.data_types import DurationDataType
    from marivo.analysis.methods.numeric_state import nullable_arithmetic
    from marivo.analysis.methods.physical import stage_arrow_type
    from marivo.semantic.metric_graph_lowering import numeric_failure

    program = stage.program
    assert program is not None
    keys = list(stage.keys)
    if keys:
        domain = (
            pd.concat([frame.loc[:, keys] for frame in inputs], ignore_index=True).drop_duplicates()
            if inputs
            else pd.DataFrame(columns=keys)
        )
        if program.calendar is not None:
            buckets = pd.DataFrame(
                {"key.0": pd.Series(range(len(program.calendar.cells)), dtype="int64[pyarrow]")}
            )
            field_keys = keys[1:]
            domain = (
                domain.loc[:, field_keys].drop_duplicates().merge(buckets, how="cross")
                if field_keys
                else buckets
            )
            domain = domain.loc[:, keys]
        domain = domain.reset_index(drop=True)
    else:
        domain = pd.DataFrame(index=pd.RangeIndex(1))
    values: dict[tuple[int, ...], pd.Series] = {}
    for binding, frame in zip(program.values, inputs, strict=True):
        slot = binding.column.slot_id
        if isinstance(binding.column.dtype, DurationDataType):
            frame = frame.copy()
            frame[slot] = pd.Series(
                pd.arrays.ArrowExtensionArray(pa.array(frame[slot]).cast(pa.int64()))
            )
        if keys:
            marked = frame.assign(**{"__present": True})
            merged = domain.merge(marked, on=keys, how="left", validate="one_to_one", sort=False)
            value = merged[slot].astype(object)
            if binding.empty_zero:
                value = value.mask(
                    merged["__present"].isna(),
                    pd.Series(
                        [Decimal(0) if isinstance(binding.column.dtype, DecimalDataType) else 0]
                        * len(value),
                        dtype=object,
                    ),
                )
        else:
            value = frame[slot].astype(object).reset_index(drop=True)
        values[binding.occurrence] = value
    declarations = dict(program.declared)

    def evaluate(node: MetricNode, occurrence: tuple[int, ...]) -> pd.Series:
        duration = duration_type(
            node,
            {},
            values={v.occurrence: v.column.dtype for v in program.values},
            declared=declarations,
            occurrence=occurrence,
        )
        if occurrence in values:
            return values[occurrence]
        if isinstance(node, DeclaredMetricRef):
            return evaluate(declarations[node.ref], (*occurrence, 0))
        if isinstance(node, ContributionFilter):
            return evaluate(node.operand, (*occurrence, 0))
        if isinstance(node, MetricConstant):
            return pd.Series([node.value] * len(domain), dtype=object)
        if isinstance(node, MetricNegate):
            return nullable_arithmetic(
                "multiply",
                evaluate(node.operand, (*occurrence, 0)),
                pd.Series([-1] * len(domain), dtype=object),
            )
        if isinstance(node, MetricDivide):
            return nullable_arithmetic(
                "divide",
                evaluate(node.numerator, (*occurrence, 0)),
                evaluate(node.denominator, (*occurrence, 1)),
                zero_denominator=node.zero_denominator,
                exact_duration=duration is not None,
            )
        if isinstance(node, (MetricAdd, MetricSubtract, MetricMultiply)):
            operation = (
                "add"
                if isinstance(node, MetricAdd)
                else "subtract"
                if isinstance(node, MetricSubtract)
                else "multiply"
            )
            return nullable_arithmetic(
                operation,
                evaluate(node.left, (*occurrence, 0)),
                evaluate(node.right, (*occurrence, 1)),
                exact_duration=duration is not None,
            )
        numeric_failure("ordinary Metric arithmetic", type(node).__name__)

    result = domain.copy()
    for i, (_, node) in enumerate(program.outputs):
        column = stage.columns[len(keys) + i]
        series = evaluate(node, (i,))
        # Match native Ibis's numeric result type, including integer true division.
        if isinstance(column.dtype, DurationDataType):
            series = pd.Series(
                [round(value) if pd.notna(value) else None for value in series], dtype=object
            )
        if isinstance(column.dtype, ScalarDataType) and column.dtype.name == "float64":
            series = series.map(lambda value: float(value) if pd.notna(value) else None)
        try:
            result[column.slot_id] = pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(series, type=stage_arrow_type(column.dtype), from_pandas=True)
                )
            )
        except (pa.ArrowException, OverflowError, ValueError, TypeError) as error:
            numeric_failure(f"{column.name}: {column.dtype}", f"numeric output conversion: {error}")
    return result.loc[:, [column.slot_id for column in stage.columns]]
