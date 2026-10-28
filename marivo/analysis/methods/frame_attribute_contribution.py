"""Exact Attribute rule composition over independently checked role inputs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from math import fsum, isfinite, ulp
from typing import Literal

import pandas as pd
import pyarrow as pa

from marivo.analysis.binding.attribute_rules import (
    AdditiveRule,
    AdditiveSource,
    AttributeRule,
    DirectRatio,
    NativeMean,
    NativeWeightedMean,
    RatioComponentRule,
    RuleAdd,
    RuleDivide,
    RuleNegate,
    RuleRescale,
    RuleScale,
    RuleSubtract,
    component_requests,
    rule_effects,
)
from marivo.analysis.compiler.graph_plan import LocalCompute
from marivo.analysis.compiler.observation import formula_columns
from marivo.analysis.core.attribute import (
    AttributeContributionProgram,
    AttributeFailureContext,
    AttributeFineFiber,
    AttributeFormulaContext,
    AttributeInputError,
    AttributeMatrixColumn,
    AttributeMethodPayload,
    AttributeNumericWitness,
    AttributeParentFiber,
    _coefficient_column,
    _logical,
    _rule_column,
    _type,
)
from marivo.analysis.core.data_schema import DataColumn
from marivo.analysis.core.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.core.frozen_calendar import FrozenKey, frozen_key_rows, frozen_key_table
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.methods.local import query_combine
from marivo.analysis.methods.numeric_state import (
    duration_arithmetic,
    finish_decimal,
    finish_division,
    finish_float64,
    nullable_arithmetic,
)
from marivo.analysis.methods.physical import DecimalType, DurationType
from marivo.semantic._numeric_lowering import numeric_failure


@dataclass(frozen=True, slots=True)
class _Value:
    values: pd.Series
    column: DataColumn
    roundoff: pd.Series | None = None


def _with_roundoff(value: _Value) -> _Value:
    """Seed effect-only error propagation from the actual Float64 operands."""
    if value.column.dtype != ScalarDataType("float64") or value.roundoff is not None:
        return value
    return replace(
        value,
        roundoff=pd.Series(
            [0.0 if pd.isna(cell) else ulp(float(cell)) for cell in value.values], dtype=float
        ),
    )


def _operation_roundoff(
    operation: Literal["add", "sub", "mul", "div"],
    a: _Value,
    b: _Value,
    values: pd.Series,
    dtype: DataType,
) -> pd.Series | None:
    """Propagate invocation-only effect bounds through the existing ordered kernel."""
    if dtype != ScalarDataType("float64") or (a.roundoff is None and b.roundoff is None):
        return None
    errors: list[float] = []
    for index, (left, right, result) in enumerate(zip(a.values, b.values, values, strict=True)):
        if pd.isna(left) or pd.isna(right) or pd.isna(result):
            errors.append(0.0)
            continue
        x, y, z = float(left), float(right), float(result)
        dx = 0.0 if a.roundoff is None else float(a.roundoff.iloc[index])
        dy = 0.0 if b.roundoff is None else float(b.roundoff.iloc[index])
        try:
            if operation in ("add", "sub"):
                error = fsum((dx, dy, ulp(z)))
            elif operation == "mul":
                error = fsum((abs(y) * dx, abs(x) * dy, dx * dy, ulp(z)))
            else:
                # This order avoids overflowing y*y or an unneeded x/y factor.
                numerator_error = dx / abs(y)
                denominator_error = abs(z) * (dy / abs(y))
                error = fsum((numerator_error, denominator_error, ulp(z)))
        except ArithmeticError as failure:
            numeric_failure("a finite Attribute effect rounding budget", str(failure))
        if not isfinite(error):
            numeric_failure("a finite Attribute effect rounding budget", repr(error))
        errors.append(error)
    return pd.Series(errors, dtype=float)


def _consistent(
    cells: list[int | float | Decimal],
    expected: int | float | Decimal,
    column: DataColumn,
    *,
    rounded: bool = False,
    roundoff: float = 0.0,
) -> bool:
    """Compare exact masses, ULP-bounded floats, or explicitly rounded typed effects."""
    if isinstance(expected, float) or any(isinstance(cell, float) for cell in cells):
        try:
            actual = fsum(float(cell) for cell in cells)
            magnitude = max(abs(float(expected)), fsum(abs(float(cell)) for cell in cells))
            tolerance = 16 * (len(cells) + 1) * fsum((ulp(magnitude), roundoff))
        except ArithmeticError as failure:
            numeric_failure("a finite Attribute component/effect numeric budget", str(failure))
        if not isfinite(tolerance):
            numeric_failure("a finite Attribute component/effect numeric budget", repr(tolerance))
        return isfinite(actual) and abs(actual - float(expected)) <= tolerance
    with localcontext() as decimal_context:
        decimal_context.prec = 120
        actual_exact = sum((Decimal(cell) for cell in cells), Decimal(0))
        tolerance_exact = (
            Decimal(len(cells) + 1) / 2
            if rounded and isinstance(column.dtype, DurationDataType)
            else Decimal(len(cells) + 1) * Decimal(1).scaleb(-column.dtype.scale) / 2
            if rounded and isinstance(column.dtype, DecimalDataType)
            else Decimal(0)
        )
        return abs(actual_exact - Decimal(expected)) <= tolerance_exact


def _has_division(rule: AdditiveRule) -> bool:
    """Keep scaling explanations inside otherwise collapsible additive rules."""
    operation = rule.operation
    if isinstance(operation, (RuleDivide, RuleRescale)):
        return True
    if isinstance(operation, (RuleAdd, RuleSubtract)):
        return _has_division(operation.left) or _has_division(operation.right)
    if isinstance(operation, (RuleNegate, RuleScale)):
        return _has_division(operation.operand)
    return False


def _numeric_binary(
    operation: Literal["add", "sub", "mul", "div"],
    a: _Value,
    b: _Value,
    *,
    output: DataType | None = None,
) -> _Value:
    dtype = output or _type(operation, a.column, b.column)
    names = {"add": "add", "sub": "subtract", "mul": "multiply", "div": "divide"}
    if isinstance(a.column.dtype, DurationDataType) or isinstance(b.column.dtype, DurationDataType):
        assert isinstance(dtype, (DurationDataType, ScalarDataType))
        values = duration_arithmetic(
            names[operation],
            a.values,
            b.values,
            left_type=a.column.dtype,
            right_type=b.column.dtype,
            output=dtype,
        )
    else:
        values = nullable_arithmetic(names[operation], a.values, b.values)
        if operation == "div" and isinstance(dtype, DurationDataType):
            try:
                values = pd.Series(
                    [
                        None
                        if pd.isna(x) or pd.isna(y) or y == 0
                        else finish_division(x, y, DurationType(dtype.unit))
                        for x, y in zip(a.values, b.values, strict=True)
                    ],
                    dtype=object,
                )
            except (ArithmeticError, TypeError, ValueError) as error:
                numeric_failure("exact signed int64 Duration arithmetic", str(error))
        if operation == "div" and isinstance(dtype, DecimalDataType):
            finished: list[int | float | Decimal | None] = []
            try:
                for x, y in zip(a.values, b.values, strict=True):
                    finished.append(
                        None
                        if pd.isna(x) or pd.isna(y) or y == 0
                        else finish_division(x, y, DecimalType(dtype.precision, dtype.scale))
                    )
            except (ArithmeticError, TypeError, ValueError) as error:
                numeric_failure("valid typed Decimal division", str(error))
            values = pd.Series(finished, dtype=object)
        if isinstance(dtype, DecimalDataType) and operation != "div":
            values = finish_decimal(values, dtype.precision, dtype.scale)
        if dtype == ScalarDataType("float64"):
            values = finish_float64(values, "Attribute contribution")
    from marivo.semantic.unit_algebra import ratio_unit

    column = replace(
        a.column,
        dtype=dtype,
        nullable=True,
        unit=ratio_unit(a.column.unit, b.column.unit) if operation == "div" else a.column.unit,
    )
    try:
        exact = pa.array(values, type=stage_arrow_type(dtype), from_pandas=True)
    except (pa.ArrowException, ArithmeticError, TypeError, ValueError) as error:
        numeric_failure("admitted Attribute numeric output", str(error))
    published = pd.Series(pd.arrays.ArrowExtensionArray(exact))
    return _Value(published, column, _operation_roundoff(operation, a, b, published, dtype))


def _binary(
    operation: Literal["add", "sub", "mul", "div"],
    a: _Value,
    b: _Value,
    *,
    output: DataType | None = None,
    contexts: tuple[AttributeFailureContext, ...] = (),
) -> _Value:
    from marivo.semantic.errors import SemanticExpressionError

    try:
        return _numeric_binary(operation, a, b, output=output)
    except SemanticExpressionError as error:
        # Existing numeric owners validate vectors. On failure, their same
        # scalar check identifies the exact typed diagnostic fiber, without
        # changing a result, source read or arithmetic policy.
        for index, captured in enumerate(contexts):
            try:
                _numeric_binary(
                    operation,
                    _Value(
                        a.values.take([index]).reset_index(drop=True),
                        a.column,
                        None
                        if a.roundoff is None
                        else a.roundoff.take([index]).reset_index(drop=True),
                    ),
                    _Value(
                        b.values.take([index]).reset_index(drop=True),
                        b.column,
                        None
                        if b.roundoff is None
                        else b.roundoff.take([index]).reset_index(drop=True),
                    ),
                    output=output,
                )
            except SemanticExpressionError:
                error.details["attribute_context"] = captured
                break
        raise


def _mapped_source(
    program: AttributeContributionProgram,
    inputs: tuple[StageOutput, ...],
    item: AttributeMatrixColumn,
) -> tuple[pd.DataFrame, ...]:
    from marivo.analysis.methods.frame_attribute import attribute_source_values

    prepared, _, left_projection, right_projection = inputs
    projection = left_projection if item.side == "left" else right_projection
    frames: list[pd.DataFrame] = []
    for source in item.sources:
        from marivo.analysis.materialization.query_components import select_attribute_root

        root = select_attribute_root(prepared.attribute_inputs, item.scope, source)
        frames.append(
            attribute_source_values(program.input_program, root, projection, source, item.scope)
        )
    return tuple(frames)


def _original(
    program: AttributeContributionProgram,
    inputs: tuple[StageOutput, ...],
    side: Literal["left", "right"],
    scope: Literal["parent", "fine"],
) -> pd.DataFrame:
    formula = program.originals[0 if side == "left" else 1]
    keys = tuple(
        c
        for axis in (
            program.input_program.parent_schema.axes
            if scope == "parent"
            else program.output_schema.axes
        )
        for c in axis.columns
    )
    frames: list[pd.DataFrame] = []
    bindings = []
    for value in formula.values:
        item = next(
            item
            for item in program.input_program.matrix
            if item.side == side
            and item.scope == scope
            and item.role in ("value", "native_value")
            and any(source.source == value.column.slot_id for source in item.sources)
        )
        source = item.sources[0]
        frame = _mapped_source(program, inputs, item)[0]
        if scope == "fine":
            # Preserve explicit output candidates. Activation uses the structural
            # parent/window mapping, never the original value's NA state.
            parent_item = next(
                m
                for m in program.input_program.matrix
                if m.side == side
                and m.scope == "parent"
                and m.role in ("value", "native_value")
                and m.sources[0].source == source.source
            )
            active = _mapped_source(program, inputs, parent_item)[0]
            parent_columns = tuple(
                c for a in program.input_program.parent_schema.axes for c in a.columns
            )
            active_parents = set(frozen_key_rows(active, parent_columns))
            domain = inputs[0].table.loc[:, [c.slot_id for c in keys]]
            selected = [
                i
                for i, key in enumerate(frozen_key_rows(domain, parent_columns))
                if key in active_parents
            ]
            domain = domain.take(selected).reset_index(drop=True)
            actual_keys = tuple(frozen_key_rows(frame, keys))
            by_key = dict(zip(actual_keys, range(len(frame)), strict=True))
            wanted = frozen_key_rows(domain, keys)
            domain[source.source] = pd.Series(
                [
                    frame[source.source].iloc[by_key[key]] if key in by_key else None
                    for key in wanted
                ],
                dtype=frame[source.source].dtype,
            )
            domain["__numeric_present"] = pd.Series([key in by_key for key in wanted], dtype=bool)
            frame = domain
        frames.append(frame)
        bindings.append(
            replace(
                value,
                column=replace(value.column, dtype=item.target.dtype),
                keys=tuple(c.slot_id for c in keys),
                coverage=(),
                key_groups=(),
                checks=(),
            )
        )
    formula = replace(
        formula,
        calendar=None,
        populations=(),
        output_population=None,
        counts=(),
        complete_calendar=False,
        parent_scope=None,
        parent_slots=(),
        values=tuple(bindings),
    )
    outputs = formula_columns(formula)
    if scope == "parent" and not len(inputs[1].table):
        empty = frozen_key_table(keys, ())
        for column in outputs:
            empty[column.slot_id] = pd.Series(
                pd.arrays.ArrowExtensionArray(pa.array([], type=stage_arrow_type(column.dtype)))
            )
        return empty
    stage = LocalCompute(
        "attribute.original", (), (*keys, *outputs), tuple(c.slot_id for c in keys), formula
    )
    parent_columns = tuple(c for a in program.input_program.parent_schema.axes for c in a.columns)
    uses = tuple(
        dict.fromkeys(
            source.use
            for item in program.input_program.matrix
            if item.side == side
            for source in item.sources
        )
    )
    parents = frozen_key_rows(inputs[1].table, parent_columns)
    return query_combine(
        stage,
        tuple(frames),
        attribute=AttributeFormulaContext(
            uses,
            keys,
            parent_columns,
            parent_columns,
            tuple(c for c in keys if c.slot_id not in {p.slot_id for p in parent_columns}),
            tuple((key, key) for key in parents),
        ),
    )


def attribute_contribution_data(
    program: AttributeContributionProgram, inputs: tuple[StageOutput, ...]
) -> tuple[pd.DataFrame, AttributeMethodPayload]:
    """Consume all originals, required parent facts and checks before returning."""
    from marivo.analysis.core.frame_method_facts import (
        FrameAdditiveMethod,
        FrameDivisionMethod,
        FrameLinearMethod,
        FrameLinearTerm,
        FrameMethodCoordinate,
        FrameMethodSupport,
        FrameMethodValue,
        FrameRatioMethod,
    )

    report_operation = program.operation
    assert report_operation.target is not None
    report_output = program.output_schema
    level = report_operation.target is not None and report_operation.target.mode == "level"
    if report_operation.target is not None:
        from marivo.analysis.core.attribute import contribution_schema

        program = replace(
            program,
            output_schema=contribution_schema(
                program.input_program, replace(program.operation, target=None)
            ),
        )
    prepared, parent, _, _ = inputs
    sides: tuple[Literal["left", "right"], ...] = ("left",) if level else ("left", "right")
    parent_columns = tuple(c for a in program.input_program.parent_schema.axes for c in a.columns)
    fine_columns = tuple(
        c
        for a in program.output_schema.axes[len(program.input_program.parent_schema.axes) :]
        for c in a.columns
    )
    full_columns = (*parent_columns, *fine_columns)
    parent_rows = tuple(frozen_key_rows(parent.table, parent_columns))
    fine_parents = tuple(frozen_key_rows(prepared.table, parent_columns))
    parent_indices = {key: i for i, key in enumerate(parent_rows)}
    fine_to_parent = [parent_indices[key] for key in fine_parents]
    originals: dict[tuple[str, str], pd.DataFrame] = {
        (side, scope): _original(program, inputs, side, scope)
        for side in sides
        for scope in ("parent", "fine")
    }
    values: dict[tuple[str, str, tuple[int, ...], str], _Value] = {}
    for item in program.input_program.matrix:
        if item.scope == "fine":
            series = prepared.table[item.target.slot_id]
        else:
            frames = _mapped_source(program, inputs, item)
            cells: dict[FrozenKey, object] = {}
            for source, frame in zip(item.sources, frames, strict=True):
                for i, key in enumerate(frozen_key_rows(frame, parent_columns)):
                    if key in cells:
                        raise AttributeInputError(
                            reason="missing_component_proof",
                            received="multiple structurally active inputs for one complete parent",
                        )
                    cells[key] = frame[source.source].iloc[i]
            series = pd.Series([cells.get(key) for key in parent_rows], dtype=object)
        values[(item.side, item.scope, item.occurrence, item.role)] = _Value(series, item.target)

    def broadcast(value: _Value) -> _Value:
        return _Value(
            value.values.take(fine_to_parent).reset_index(drop=True),
            value.column,
            None
            if value.roundoff is None
            else value.roundoff.take(fine_to_parent).reset_index(drop=True),
        )

    def context(
        name: str,
        rule: AttributeRule,
        side: str,
        scope: str,
        index: int,
        operation: Literal["original", "add", "sub", "mul", "div", "denominator", "contribution"],
        observed: tuple[FrameMethodValue, ...] = (),
    ) -> AttributeFailureContext:
        occurrences = {_logical(request) for request in component_requests(rule)}
        uses = tuple(
            leaf.use
            for root in prepared.attribute_inputs
            for leaf in root.layout.leaves
            if (
                side == "both"
                or any(
                    item.side == side and any(source.use == leaf.use for source in item.sources)
                    for item in program.input_program.matrix
                )
            )
            and leaf.use.logical_occurrence in occurrences
        )
        if scope == "parent":
            fiber: AttributeParentFiber | AttributeFineFiber = AttributeParentFiber(
                parent_rows[index], parent_columns
            )
        else:
            fiber = AttributeFineFiber(
                fine_parents[index],
                parent_columns,
                frozen_key_rows(prepared.table, fine_columns)[index],
                fine_columns,
            )
        return AttributeFailureContext(
            uses,
            fiber,
            AttributeNumericWitness(
                uses[0].logical_occurrence if uses else (), operation, observed
            ),
        )

    def arithmetic(
        operation: Literal["add", "sub", "mul", "div"],
        a: _Value,
        b: _Value,
        rule: AttributeRule,
        side: str,
        scope: str,
        *,
        output: DataType | None = None,
    ) -> _Value:
        from marivo.analysis.core.model import CoreRuleError
        from marivo.semantic.errors import SemanticExpressionError

        captured_contexts: list[AttributeFailureContext] = []
        for index in range(len(a.values)):
            try:
                observed = (fact(a, index), fact(b, index))
            except CoreRuleError as error:
                assert error.expected is not None and error.received is not None
                try:
                    numeric_failure(error.expected, error.received)
                except SemanticExpressionError as failure:
                    failure.details["attribute_context"] = context(
                        current_name, rule, side, scope, index, operation
                    )
                    raise
            captured_contexts.append(
                context(current_name, rule, side, scope, index, operation, observed)
            )
        contexts = tuple(captured_contexts)
        return _binary(operation, a, b, output=output, contexts=contexts)

    def native_value(
        rule: AttributeRule, side: str, scope: str, path: tuple[int, ...]
    ) -> _Value | None:
        if program.operation.source_indices is not None:
            path = (program.operation.source_indices[0 if side == "left" else 1], *path[1:])
        native = values.get((side, scope, path, "native_value"))
        if native is None:
            return None
        column = _rule_column(program.input_program, rule, side)
        return _Value(native.values, replace(column, dtype=native.column.dtype))

    def evaluate(
        rule: AttributeRule,
        side: str,
        scope: str,
        *,
        original: bool = False,
        path: tuple[int, ...] | None = None,
    ) -> _Value:
        if path is None:
            path = (metric_index,)
        native = native_value(rule, side, scope, path)
        if original and native is not None:
            return native
        output = None if native is None else native.column.dtype
        operation = rule.operation
        if isinstance(operation, AdditiveSource):
            role = "value" if original else "additive_value"
            if original and operation.source.window is not None:
                # Original expanded leaves are already composed by FormulaProgram;
                # additive child facts use their mathematically identical mass.
                role = "additive_value"
            return values[(side, scope, _logical(operation.source), role)]
        if isinstance(rule, RatioComponentRule):
            if isinstance(operation, (NativeMean, NativeWeightedMean)) and original:
                return values[(side, scope, _logical(operation.source), "value")]
            if isinstance(operation, DirectRatio):
                numerator = evaluate(
                    operation.numerator, side, scope, original=original, path=(*path, 0)
                )
                denominator = evaluate(
                    operation.denominator,
                    side,
                    scope if original else "parent",
                    original=original,
                    path=(*path, 1),
                )
            else:
                assert isinstance(operation, (NativeMean, NativeWeightedMean))
                roles = (
                    ("valid_value_sum", "valid_value_count")
                    if isinstance(operation, NativeMean)
                    else ("pair_product_sum", "pair_weight_sum")
                )
                numerator = values[(side, scope, _logical(operation.source), roles[0])]
                denominator = values[(side, "parent", _logical(operation.source), roles[1])]
            if not original:
                invalid = denominator.values.isna() | (denominator.values == 0).fillna(False)
                if invalid.any():
                    index = int(invalid.to_numpy().nonzero()[0][0])
                    raise AttributeInputError(
                        reason="root_denominator_zero"
                        if pd.notna(denominator.values.iloc[index])
                        and denominator.values.iloc[index] == 0
                        else "undefined_input",
                        received="a required complete-parent denominator is zero or undefined",
                        context=context(
                            current_name,
                            rule,
                            side,
                            "parent",
                            index,
                            "denominator",
                            (fact(denominator, index),),
                        ),
                    )
                if scope == "fine":
                    denominator = broadcast(denominator)
            return arithmetic(
                "div",
                numerator,
                denominator,
                rule,
                side,
                scope,
                output=output or _rule_column(program.input_program, rule, side).dtype,
            )
        if isinstance(operation, (RuleAdd, RuleSubtract)):
            return arithmetic(
                "add" if isinstance(operation, RuleAdd) else "sub",
                evaluate(operation.left, side, scope, original=original, path=(*path, 0)),
                evaluate(operation.right, side, scope, original=original, path=(*path, 1)),
                rule,
                side,
                scope,
                output=output,
            )
        if isinstance(operation, (RuleDivide, RuleRescale)):
            value = evaluate(operation.operand, side, scope, original=original, path=(*path, 0))
            divisor = _Value(
                pd.Series([operation.scalar.value] * len(value.values), dtype=object),
                _coefficient_column(operation.scalar.value),
            )
            if (output or _type("div", value.column, divisor.column)) == ScalarDataType("float64"):
                # Native Metric division rounds operands into Float64 before
                # dividing, including exact integer/Decimal additive masses.
                value, divisor = (
                    _Value(
                        finish_float64(item.values, "Attribute division operand"),
                        replace(item.column, dtype=ScalarDataType("float64")),
                    )
                    for item in (value, divisor)
                )
            divided = arithmetic(
                "div",
                value,
                divisor,
                rule,
                side,
                scope,
                output=output,
            )
            return _Value(
                divided.values,
                replace(
                    divided.column,
                    unit=operation.unit
                    if isinstance(operation, RuleRescale)
                    else value.column.unit,
                ),
            )
        assert isinstance(operation, (RuleNegate, RuleScale))
        value = evaluate(operation.operand, side, scope, original=original, path=(*path, 0))
        scalar = -1 if isinstance(operation, RuleNegate) else operation.scalar.value
        return arithmetic(
            "mul",
            value,
            _Value(
                pd.Series([scalar] * len(value.values), dtype=object),
                _coefficient_column(scalar),
            ),
            rule,
            side,
            scope,
            output=output,
        )

    def fact(value: _Value, index: int) -> FrameMethodValue:
        if isinstance(value.column.dtype, DurationDataType):
            scalar = (
                pa.array(value.values, type=stage_arrow_type(value.column.dtype), from_pandas=True)
                .cast(pa.int64())[index]
                .as_py()
            )
        else:
            scalar = pa.array(
                value.values, type=stage_arrow_type(value.column.dtype), from_pandas=True
            )[index].as_py()
        return FrameMethodValue(value.column, scalar)

    def components(
        rule: RatioComponentRule, side: str, scope: str, path: tuple[int, ...]
    ) -> tuple[_Value, _Value]:
        operation = rule.operation
        if isinstance(operation, DirectRatio):
            return evaluate(operation.numerator, side, scope, path=(*path, 0)), evaluate(
                operation.denominator, side, scope, path=(*path, 1)
            )
        roles = (
            ("valid_value_sum", "valid_value_count")
            if isinstance(operation, NativeMean)
            else ("pair_product_sum", "pair_weight_sum")
        )
        return (
            values[(side, scope, _logical(operation.source), roles[0])],
            values[(side, scope, _logical(operation.source), roles[1])],
        )

    def assert_same(
        actual: _Value,
        expected: _Value,
        rule: AttributeRule,
        side: str,
        scope: str,
        *,
        grouped: bool = False,
        rounded: bool = False,
    ) -> None:
        for index in range(len(expected.values)):
            indices = (
                [i for i, parent_index in enumerate(fine_to_parent) if parent_index == index]
                if grouped
                else [index]
            )
            captured = tuple(fact(actual, i) for i in indices)
            wanted_value = fact(expected, index)
            numeric = tuple(item.value for item in captured)
            wanted = wanted_value.value
            if not isinstance(wanted, (int, float, Decimal)) or any(
                not isinstance(item, (int, float, Decimal)) for item in numeric
            ):
                valid = False
            else:
                checked: list[int | float | Decimal] = []
                for item in numeric:
                    assert isinstance(item, (int, float, Decimal))
                    checked.append(item)
                from marivo.semantic.errors import SemanticExpressionError

                try:
                    propagated = (
                        0.0
                        if actual.roundoff is None
                        else fsum(float(actual.roundoff.iloc[i]) for i in indices)
                    )
                    if expected.roundoff is not None:
                        propagated += float(expected.roundoff.iloc[index])
                    valid = _consistent(
                        checked, wanted, expected.column, rounded=rounded, roundoff=propagated
                    )
                except SemanticExpressionError as failure:
                    failure.details["attribute_context"] = context(
                        current_name,
                        rule,
                        side,
                        scope,
                        index,
                        "original",
                        (*captured[:4], wanted_value),
                    )
                    raise
            if not valid:
                raise AttributeInputError(
                    reason="component_mismatch",
                    received=f"{current_name}: {side} {scope} components or original N/D disagree under attribute_components/v1",
                    context=context(
                        current_name,
                        rule,
                        side,
                        scope,
                        index,
                        "original",
                        (*captured[:4], wanted_value),
                    ),
                )

    def ratio_effects(
        rule: RatioComponentRule, other: RatioComponentRule, path: tuple[int, ...]
    ) -> dict[str, _Value]:
        endpoint: list[tuple[_Value, _Value, _Value]] = []
        dtype = _type(
            "sub",
            _rule_column(program.input_program, rule, "left"),
            _rule_column(program.input_program, other, "right"),
        )
        for side, current in (("left", rule), ("right", other)):
            for request in component_requests(current):
                occurrence = _logical(request)
                for role in (*request.roles, "input_count", "effective_count"):
                    assert_same(
                        values[(side, "fine", occurrence, role)],
                        values[(side, "parent", occurrence, role)],
                        current,
                        side,
                        "parent",
                        grouped=True,
                    )
            n, d = components(current, side, "fine", path)
            parent_n, parent_d = components(current, side, "parent", path)
            invalid_parent = parent_d.values.isna() | (parent_d.values <= 0).fillna(False)
            if invalid_parent.any():
                index = int(invalid_parent.to_numpy().nonzero()[0][0])
                raise AttributeInputError(
                    reason="invalid_rate_support",
                    received=f"{current_name}: {side} parent denominator must be positive",
                    context=context(
                        current_name,
                        current,
                        side,
                        "parent",
                        index,
                        "denominator",
                        (fact(parent_d, index),),
                    ),
                )
            invalid = (
                d.values.isna()
                | n.values.isna()
                | (d.values < 0).fillna(False)
                | ((d.values == 0) & (n.values != 0)).fillna(False)
            )
            operation = current.operation
            if isinstance(operation, NativeWeightedMean):
                negative = values[(side, "fine", _logical(operation.source), "pair_negative_count")]
                invalid = invalid | (negative.values > 0).fillna(False)
            if invalid.any():
                index = int(invalid.to_numpy().nonzero()[0][0])
                raise AttributeInputError(
                    reason="invalid_rate_support",
                    received=f"{current_name}: {side} requires nonnegative denominators and effective weights, and zero numerator mass at zero support",
                    context=context(
                        current_name,
                        current,
                        side,
                        "fine",
                        index,
                        "denominator",
                        (fact(n, index), fact(d, index)),
                    ),
                )
            assert_same(n, parent_n, current, side, "parent", grouped=True)
            assert_same(d, parent_d, current, side, "parent", grouped=True)
            endpoint_dtype = _rule_column(program.input_program, current, side).dtype
            quotient = arithmetic("div", n, d, current, side, "fine", output=endpoint_dtype)
            original = evaluate(current, side, "fine", original=True, path=path)
            positive = [i for i, cell in enumerate(d.values) if pd.notna(cell) and cell > 0]
            # Keep the original nullable diagnostics intact. Only supported groups
            # participate in this identity check; zero support has no group rate.
            for index in positive:
                actual_value, original_value = fact(quotient, index), fact(original, index)
                actual_scalar, original_scalar = actual_value.value, original_value.value
                if (
                    not isinstance(actual_scalar, (int, float, Decimal))
                    or not isinstance(original_scalar, (int, float, Decimal))
                    or not _consistent(
                        [actual_scalar], original_scalar, original.column, rounded=True
                    )
                ):
                    raise AttributeInputError(
                        reason="component_mismatch",
                        received=f"{current_name}: {side} positive-support original group Metric is undefined or differs from N/D",
                        context=context(
                            current_name,
                            current,
                            side,
                            "fine",
                            index,
                            "original",
                            (actual_value, original_value),
                        ),
                    )
            parent_quotient = arithmetic(
                "div", parent_n, parent_d, current, side, "parent", output=endpoint_dtype
            )
            assert_same(
                parent_quotient,
                evaluate(current, side, "parent", original=True, path=path),
                current,
                side,
                "parent",
                rounded=True,
            )
            weight = arithmetic("div", d, broadcast(parent_d), current, side, "fine")
            endpoint.append((quotient, weight, d))
        r1, w1, d1 = endpoint[0]
        r0, w0, d0 = endpoint[1]
        r1, w1, r0, w0 = (_with_roundoff(value) for value in (r1, w1, r0, w0))
        surviving = ((d1.values > 0) & (d0.values > 0)).fillna(False)
        # Undefined rates stay undefined in original diagnostics. The masked
        # working values prevent undefined arithmetic outside surviving support.
        r1 = replace(
            r1,
            values=r1.values.where(surviving, 0),
            roundoff=None if r1.roundoff is None else r1.roundoff.where(surviving, 0),
        )
        r0 = replace(
            r0,
            values=r0.values.where(surviving, 0),
            roundoff=None if r0.roundoff is None else r0.roundoff.where(surviving, 0),
        )
        two = _Value(pd.Series([2] * len(r1.values), dtype=object), _coefficient_column(2))
        within = arithmetic(
            "mul",
            arithmetic(
                "div", arithmetic("add", w0, w1, rule, "both", "fine"), two, rule, "both", "fine"
            ),
            arithmetic("sub", r1, r0, rule, "both", "fine"),
            rule,
            "both",
            "fine",
            output=dtype,
        )
        mix = arithmetic(
            "mul",
            arithmetic(
                "div", arithmetic("add", r0, r1, rule, "both", "fine"), two, rule, "both", "fine"
            ),
            arithmetic("sub", w1, w0, rule, "both", "fine"),
            rule,
            "both",
            "fine",
            output=dtype,
        )
        allocation = arithmetic(
            "sub",
            _with_roundoff(evaluate(rule, "left", "fine", path=path)),
            _with_roundoff(evaluate(other, "right", "fine", path=path)),
            rule,
            "both",
            "fine",
            output=dtype,
        )
        entry = replace(
            allocation,
            values=allocation.values.where(~surviving, 0),
            roundoff=None
            if allocation.roundoff is None
            else allocation.roundoff.where(~surviving, 0),
        )
        result = {"within": within, "mix": mix, "entry_exit": entry}
        combined = arithmetic(
            "add",
            arithmetic("add", within, mix, rule, "both", "fine"),
            entry,
            rule,
            "both",
            "fine",
            output=dtype,
        )
        assert_same(combined, allocation, rule, "both", "fine", rounded=True)
        return result

    def effects(
        rule: AttributeRule, other: AttributeRule, path: tuple[int, ...]
    ) -> dict[str, _Value]:
        if isinstance(rule, RatioComponentRule):
            assert isinstance(other, RatioComponentRule)
            return ratio_effects(rule, other, path)
        if isinstance(rule, AdditiveRule):
            return {
                "additive": arithmetic(
                    "sub",
                    _with_roundoff(evaluate(rule, "left", "fine", path=path)),
                    _with_roundoff(evaluate(other, "right", "fine", path=path)),
                    rule,
                    "both",
                    "fine",
                )
            }
        operation, other_operation = rule.operation, other.operation
        if isinstance(operation, (RuleAdd, RuleSubtract)):
            assert isinstance(other_operation, type(operation))
            left = effects(operation.left, other_operation.left, (*path, 0))
            right = effects(operation.right, other_operation.right, (*path, 1))
            result: dict[str, _Value] = {}
            for role in dict.fromkeys((*left, *right)):
                a, b = left.get(role), right.get(role)
                if a is not None and b is not None:
                    result[role] = arithmetic(
                        "add" if isinstance(operation, RuleAdd) else "sub",
                        a,
                        b,
                        rule,
                        "both",
                        "fine",
                    )
                elif a is not None:
                    result[role] = a
                else:
                    assert b is not None
                    result[role] = (
                        arithmetic(
                            "mul",
                            b,
                            _Value(pd.Series([-1] * len(b.values)), _coefficient_column(-1)),
                            rule,
                            "both",
                            "fine",
                        )
                        if isinstance(operation, RuleSubtract)
                        else b
                    )
            return result
        assert isinstance(operation, (RuleNegate, RuleScale, RuleDivide, RuleRescale))
        assert isinstance(other_operation, type(operation))
        children = effects(operation.operand, other_operation.operand, (*path, 0))
        scalar = -1 if isinstance(operation, RuleNegate) else operation.scalar.value
        scaled: dict[str, _Value] = {}
        for role, value in children.items():
            coefficient = _Value(
                pd.Series([scalar] * len(value.values), dtype=object), _coefficient_column(scalar)
            )
            dividing = isinstance(operation, (RuleDivide, RuleRescale))
            if dividing and _type("div", value.column, coefficient.column) == ScalarDataType(
                "float64"
            ):
                value, coefficient = (
                    _Value(
                        finish_float64(item.values, "Attribute effect division operand"),
                        replace(item.column, dtype=ScalarDataType("float64")),
                        item.roundoff,
                    )
                    for item in (value, coefficient)
                )
            scaled[role] = arithmetic(
                "div" if isinstance(operation, (RuleDivide, RuleRescale)) else "mul",
                value,
                coefficient,
                rule,
                "both",
                "fine",
            )
        return scaled

    def supports(rule: AttributeRule, index: int) -> tuple[FrameMethodSupport, ...]:
        result: list[FrameMethodSupport] = []
        seen: set[tuple[int, ...]] = set()
        for request in component_requests(rule):
            occurrence = _logical(request)
            if occurrence in seen:
                continue
            seen.add(occurrence)
            counts = [
                values[(side, "parent", occurrence, role)].values.iloc[index]
                for side in ("left", "right")
                for role in ("input_count", "effective_count")
            ]
            result.append(
                FrameMethodSupport(
                    "root." + ".".join(map(str, occurrence)),
                    request.support,
                    int(counts[0]),
                    int(counts[2]),
                    int(counts[1]),
                    int(counts[3]),
                )
            )
        return tuple(result)

    def method(
        rule: AttributeRule,
        other_rule: AttributeRule,
        index: int,
        totals: tuple[_Value, _Value] | None = None,
        path: tuple[int, ...] | None = None,
    ) -> FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod | FrameDivisionMethod:
        if path is None:
            path = (metric_index,)
        a, b = totals or (
            evaluate(rule, "left", "parent", original=True, path=path),
            evaluate(other_rule, "right", "parent", original=True, path=path),
        )
        change = _binary("sub", a, b)
        common = (fact(a, index), fact(b, index), fact(change, index))
        if isinstance(rule.operation, (RuleDivide, RuleRescale)):
            division = rule.operation
            assert isinstance(other_rule.operation, (RuleDivide, RuleRescale))
            return FrameDivisionMethod(
                "division",
                *common,
                division.scalar.value,
                "rescale" if isinstance(division, RuleRescale) else "divide",
                method(division.operand, other_rule.operation.operand, index, path=(*path, 0)),
            )
        if isinstance(rule, AdditiveRule) and not _has_division(rule):
            return FrameAdditiveMethod("additive", *common, supports(rule, index))
        if isinstance(rule, RatioComponentRule):
            components: list[FrameMethodValue] = []
            ns: list[FrameMethodValue] = []
            ds: list[FrameMethodValue] = []
            for side, current_rule in (("left", rule), ("right", other_rule)):
                assert isinstance(current_rule, RatioComponentRule)
                operation = current_rule.operation
                if isinstance(operation, DirectRatio):
                    n, d = (
                        evaluate(operation.numerator, side, "parent"),
                        evaluate(operation.denominator, side, "parent"),
                    )
                else:
                    roles = (
                        ("valid_value_sum", "valid_value_count")
                        if isinstance(operation, NativeMean)
                        else ("pair_product_sum", "pair_weight_sum")
                    )
                    n, d = (
                        values[(side, "parent", _logical(operation.source), role)] for role in roles
                    )
                ns.append(fact(n, index))
                ds.append(fact(d, index))
            components = [*ns, *ds]
            return FrameRatioMethod(
                "ratio_component",
                *common,
                components[0],
                components[1],
                components[2],
                components[3],
                supports(rule, index),
            )
        linear_operation, other_operation = rule.operation, other_rule.operation
        terms: list[FrameLinearTerm] = []
        if isinstance(linear_operation, (RuleAdd, RuleSubtract)):
            assert isinstance(other_operation, type(linear_operation))
            terms.append(
                FrameLinearTerm(
                    1, method(linear_operation.left, other_operation.left, index, path=(*path, 0))
                )
            )
            terms.append(
                FrameLinearTerm(
                    1 if isinstance(linear_operation, RuleAdd) else -1,
                    method(linear_operation.right, other_operation.right, index, path=(*path, 1)),
                )
            )
        else:
            assert isinstance(linear_operation, (RuleNegate, RuleScale)) and isinstance(
                other_operation, (RuleNegate, RuleScale)
            )
            terms.append(
                FrameLinearTerm(
                    -1
                    if isinstance(linear_operation, RuleNegate)
                    else linear_operation.scalar.value,
                    method(
                        linear_operation.operand, other_operation.operand, index, path=(*path, 0)
                    ),
                )
            )
        return FrameLinearMethod("linear", *common, tuple(terms))

    from marivo.analysis.core.frame_method_facts import (
        FrameLevelAdditiveMethod,
        FrameLevelDivisionMethod,
        FrameLevelLinearMethod,
        FrameLevelLinearTerm,
        FrameLevelRatioMethod,
        FrameLevelSupport,
    )

    level_methods: list[
        FrameLevelAdditiveMethod
        | FrameLevelRatioMethod
        | FrameLevelLinearMethod
        | FrameLevelDivisionMethod
    ] = []

    def level_method(
        rule: AttributeRule, index: int, total: _Value | None = None, path: tuple[int, ...] = (0,)
    ) -> (
        FrameLevelAdditiveMethod
        | FrameLevelRatioMethod
        | FrameLevelLinearMethod
        | FrameLevelDivisionMethod
    ):
        value = fact(
            total
            if total is not None
            else evaluate(rule, "left", "parent", original=True, path=path),
            index,
        )
        supporting = []
        for request in component_requests(rule):
            occurrence = _logical(request)
            count = values[("left", "parent", occurrence, "input_count")].values.iloc[index]
            effective = values[("left", "parent", occurrence, "effective_count")].values.iloc[index]
            supporting.append(
                FrameLevelSupport(
                    "root." + ".".join(map(str, occurrence)),
                    request.support,
                    int(count),
                    int(effective),
                )
            )
        support = tuple(dict.fromkeys(supporting))
        operation = rule.operation
        if isinstance(operation, (RuleDivide, RuleRescale)):
            return FrameLevelDivisionMethod(
                "level_division",
                value,
                operation.scalar.value,
                "rescale" if isinstance(operation, RuleRescale) else "divide",
                level_method(operation.operand, index, path=(*path, 0)),
            )
        if isinstance(rule, AdditiveRule) and not _has_division(rule):
            return FrameLevelAdditiveMethod("level_additive", value, support)
        if isinstance(rule, RatioComponentRule):
            if isinstance(operation, DirectRatio):
                n, d = (
                    evaluate(operation.numerator, "left", "parent"),
                    evaluate(operation.denominator, "left", "parent"),
                )
            else:
                assert isinstance(operation, (NativeMean, NativeWeightedMean))
                roles = (
                    ("valid_value_sum", "valid_value_count")
                    if isinstance(operation, NativeMean)
                    else ("pair_product_sum", "pair_weight_sum")
                )
                n, d = (
                    values[("left", "parent", _logical(operation.source), role)] for role in roles
                )
            return FrameLevelRatioMethod(
                "level_ratio_component", value, fact(n, index), fact(d, index), support
            )
        if isinstance(operation, (RuleAdd, RuleSubtract)):
            terms: tuple[FrameLevelLinearTerm, ...] = (
                FrameLevelLinearTerm(1, level_method(operation.left, index, path=(*path, 0))),
                FrameLevelLinearTerm(
                    1 if isinstance(operation, RuleAdd) else -1,
                    level_method(operation.right, index, path=(*path, 1)),
                ),
            )
        else:
            assert isinstance(operation, (RuleNegate, RuleScale))
            terms = (
                FrameLevelLinearTerm(
                    -1 if isinstance(operation, RuleNegate) else operation.scalar.value,
                    level_method(operation.operand, index, path=(*path, 0)),
                ),
            )
        return FrameLevelLinearMethod("level_linear", value, terms)

    rows = prepared.table.loc[:, list(program.output_schema.key_fields)].copy()
    wanted = frozen_key_rows(rows, full_columns)
    paired_parent_methods: list[
        FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod | FrameDivisionMethod
    ] = []
    for metric_index, ((name, left_rule), (_, right_rule)) in enumerate(
        zip(*program.operation.rules, strict=True)
    ):
        current_name = ".".join(report_operation.target.path)
        # Original error-policy formulas have already run above. A nullable
        # original parent caused by D=0 now gets the contribution-specific reason.
        evaluate(left_rule, "left", "parent")
        if not level:
            evaluate(right_rule, "right", "parent")
        endpoint_values: list[_Value] = []
        total_values: list[_Value] = []
        for side, rule in (
            (("left", left_rule),) if level else (("left", left_rule), ("right", right_rule))
        ):
            column = (
                program.input_program.left_schema
                if side == "left"
                else program.input_program.right_schema
            ).values[metric_index]
            for scope, keys, destination in (
                ("fine", full_columns, endpoint_values),
                ("parent", parent_columns, total_values),
            ):
                frame = originals[(side, scope)]
                by_key = dict(zip(frozen_key_rows(frame, keys), range(len(frame)), strict=True))
                target_keys = wanted if scope == "fine" else parent_rows
                source_index = (
                    program.operation.source_indices[0 if side == "left" else 1]
                    if program.operation.source_indices is not None
                    else metric_index
                )
                series = pd.Series(
                    [
                        frame[f"value.{source_index}"].iloc[by_key[key]] if key in by_key else None
                        for key in target_keys
                    ],
                    dtype=object,
                )
                if scope == "parent" and series.isna().any():
                    index = int(series.isna().to_numpy().nonzero()[0][0])
                    raise AttributeInputError(
                        reason="undefined_input",
                        received=f"{name}: undefined required original parent total",
                        context=context(name, rule, side, scope, index, "original"),
                    )
                from marivo.analysis.materialization.graph_exchange import source_columns

                actual = source_columns(
                    frame,
                    (replace(column, slot_id=f"value.{source_index}"),),
                )[0]
                destination.append(_Value(series, replace(column, dtype=actual.dtype)))
        if level:
            endpoint_values.append(endpoint_values[0])
            total_values.append(total_values[0])
        # Evaluate parent masses even when there are no fine rows. This retains
        # every required denominator/child check and complete parent facts.
        directory = dict(
            zip(program.output_schema.value_paths, program.output_schema.values, strict=True)
        )
        total_change = (
            total_values[0]
            if level
            else arithmetic(
                "sub",
                total_values[0],
                total_values[1],
                left_rule,
                "both",
                "parent",
            )
        )
        contribution = (
            evaluate(left_rule, "left", "fine")
            if level
            else arithmetic(
                "sub",
                evaluate(left_rule, "left", "fine"),
                evaluate(right_rule, "right", "fine"),
                left_rule,
                "both",
                "fine",
                output=program.output_schema.values[
                    program.output_schema.value_paths.index(("contribution", name))
                ].dtype,
            )
        )
        if contribution.values.isna().any():
            index = int(contribution.values.isna().to_numpy().nonzero()[0][0])
            raise AttributeInputError(
                reason="undefined_input",
                received=f"{name}: undefined required contribution",
                context=context(name, left_rule, "left", "fine", index, "contribution"),
            )
        change = (
            endpoint_values[0]
            if level
            else arithmetic(
                "sub",
                endpoint_values[0],
                endpoint_values[1],
                left_rule,
                "both",
                "fine",
                output=directory[("absolute", name)].dtype,
            )
        )
        share = arithmetic(
            "div",
            contribution,
            broadcast(total_change),
            left_rule,
            "both",
            "fine",
            output=directory[("contribution_share", name)].dtype,
        )
        emitted = {
            "left": endpoint_values[0],
            "right": endpoint_values[1],
            "absolute": change,
            "contribution": contribution,
            "contribution_share": share,
        }
        if not level and rule_effects(left_rule):
            calculated = effects(left_rule, right_rule, (metric_index,))
            combined_effect: _Value | None = None
            for role in rule_effects(left_rule):
                value = calculated[role]
                zero = _Value(pd.Series([0] * len(value.values)), value.column)
                value = arithmetic(
                    "add",
                    value,
                    zero,
                    left_rule,
                    "both",
                    "fine",
                    output=directory[("effect_" + role, name)].dtype,
                )
                emitted["effect_" + role] = value
                combined_effect = (
                    value
                    if combined_effect is None
                    else arithmetic("add", combined_effect, value, left_rule, "both", "fine")
                )
            assert combined_effect is not None
            assert_same(combined_effect, contribution, left_rule, "both", "fine", rounded=True)
        if isinstance(left_rule, RatioComponentRule):
            for side, rule in (
                (("left", left_rule),) if level else (("left", left_rule), ("right", right_rule))
            ):
                assert isinstance(rule, RatioComponentRule)
                operation = rule.operation
                if isinstance(operation, DirectRatio):
                    n, d = (
                        evaluate(operation.numerator, side, "fine"),
                        evaluate(operation.denominator, side, "fine"),
                    )
                else:
                    roles = (
                        ("valid_value_sum", "valid_value_count")
                        if isinstance(operation, NativeMean)
                        else ("pair_product_sum", "pair_weight_sum")
                    )
                    n, d = (
                        values[(side, "fine", _logical(operation.source), role)] for role in roles
                    )
                emitted["numerator_" + side], emitted["denominator_" + side] = n, d
                if isinstance(operation, NativeWeightedMean):
                    emitted["valid_count_" + side] = values[
                        (side, "fine", _logical(operation.source), "pair_count")
                    ]
        for prefix, value in emitted.items():
            rows[
                program.output_schema.values[
                    program.output_schema.value_paths.index((prefix, name))
                ].slot_id
            ] = value.values
        for index in range(len(parent_rows)):
            if level:
                level_methods.append(level_method(left_rule, index, total_values[0]))
                continue
            root_method = method(left_rule, right_rule, index, (total_values[0], total_values[1]))
            root_method = replace(
                root_method,
                left=FrameMethodValue(
                    replace(directory[("left", name)], dtype=root_method.left.column.dtype),
                    root_method.left.value,
                ),
                right=FrameMethodValue(
                    replace(directory[("right", name)], dtype=root_method.right.column.dtype),
                    root_method.right.value,
                ),
                change=FrameMethodValue(
                    replace(directory[("absolute", name)], dtype=root_method.change.column.dtype),
                    root_method.change.value,
                ),
            )
            if isinstance(root_method, FrameRatioMethod):
                root_method = replace(
                    root_method,
                    numerator_left=FrameMethodValue(
                        replace(
                            directory[("numerator_left", name)],
                            dtype=root_method.numerator_left.column.dtype,
                        ),
                        root_method.numerator_left.value,
                    ),
                    numerator_right=FrameMethodValue(
                        replace(
                            directory[("numerator_right", name)],
                            dtype=root_method.numerator_right.column.dtype,
                        ),
                        root_method.numerator_right.value,
                    ),
                    denominator_left=FrameMethodValue(
                        replace(
                            directory[("denominator_left", name)],
                            dtype=root_method.denominator_left.column.dtype,
                        ),
                        root_method.denominator_left.value,
                    ),
                    denominator_right=FrameMethodValue(
                        replace(
                            directory[("denominator_right", name)],
                            dtype=root_method.denominator_right.column.dtype,
                        ),
                        root_method.denominator_right.value,
                    ),
                )
            paired_parent_methods.append(root_method)
    for column in program.output_schema.presence:
        rows[column.slot_id] = prepared.table[column.slot_id]
    # Filling value columns preserves the prepared canonical key-domain order.
    coordinates = tuple(
        FrameMethodCoordinate(
            a.name,
            a.schema,
            tuple(tuple(v for _, v in key) for key in frozen_key_rows(a.rows, a.schema.columns)),
        )
        for a in sorted(parent.frame_attachments, key=lambda a: a.name)
    )
    payload = AttributeMethodPayload(
        program.output_schema,
        len(program.input_program.parent_schema.axes),
        (),
        coordinates,
        parent_schema=program.input_program.parent_schema,
    )
    from marivo.analysis.methods.frame_attribute_report import report_attribute

    return report_attribute(
        replace(program, operation=report_operation),
        rows,
        payload,
        parent,
        report_output,
        level_methods=tuple(level_methods),
        paired_parent_methods=tuple(paired_parent_methods),
    )
