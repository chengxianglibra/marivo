"""Finite, domain-bound result expressions and unfinished value statistics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from marivo._definition.query import (
    CoordinateSlot,
    PairStatistic,
    Statistic,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValueUnary,
)
from marivo._definition.types import DefinitionGraphError, LiteralValue, check_literal
from marivo.analysis.core.frame_structure import ValueKind as ValueKind
from marivo.analysis.core.model import reject

if TYPE_CHECKING:
    from marivo.analysis.dsl.frame import AnalysisFrame


def literal_kind(value: LiteralValue) -> ValueKind:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float, Decimal)):
        return "numeric"
    if isinstance(value, datetime):
        return "timestamp"
    if isinstance(value, date):
        return "date"
    if isinstance(value, str):
        return "string"
    if isinstance(value, timedelta):
        return "duration"
    return "unresolved"


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class Column:
    """A value expression over one exact AnalysisFrame input.

    Acquire with ``frame['name']`` or ``frame.coord(axis)``. Arithmetic,
    comparisons and is_null retain the complete input domain; evaluation belongs
    to AnalysisFrame.compute. Columns from independent Frames require explicit alignment.
    """

    _input_id: str
    _expression: ValueExpression
    _unit: str | None
    _kind: ValueKind
    _frame: AnalysisFrame | None = None
    _name: str | None = None

    def __repr__(self) -> str:
        return f"<Column input={self._input_id[:16]} kind={self._kind}; use .show()>"

    def show(self) -> None:
        """Print captured Column binding facts without reading its source.

        Returns: None; prints input identity, role, position, kind and unit.
        Example: ``frame['revenue'].show()``.
        Constraints: Computed expressions may have no single name or coordinate.
        Unresolved types remain unresolved until execution.
        """
        expression = self._expression
        role = "coordinate" if isinstance(expression, CoordinateSlot) else "value"
        facts = [
            ("input", self._input_id),
            ("role", role),
            ("kind", self._kind),
            ("unit", self._unit or "undeclared"),
        ]
        if self._name is not None:
            facts.append(("name", self._name))
        elif isinstance(expression, CoordinateSlot):
            facts.append(("coordinate_index", str(expression.index)))
        else:
            facts.append(("expression", type(expression).__name__))
        print("logical Column")
        for name, value in facts:
            if len(value.encode("utf-8")) > 600:
                print(f"{name}=omitted ({len(value)} characters)")
                if name == "name":
                    print("Complete names: frame.output_names")
            else:
                print(f"{name}={value}")
        print("Help: analysis.column")

    def _binary(
        self,
        other: Column | LiteralValue,
        operation: Literal[
            "add", "sub", "mul", "div", "eq", "ne", "lt", "le", "gt", "ge", "and", "or"
        ],
        *,
        reverse: bool = False,
        zero_denominator: Literal["null", "error"] = "null",
    ) -> Column:
        if isinstance(other, Column):
            if self._input_id != other._input_id:
                reject(
                    "Columns bound to this exact Frame input",
                    "different Frame inputs",
                    "Align the Frames before combining their Columns.",
                    "Column",
                )
            expression, unit, kind = other._expression, other._unit, other._kind
        else:
            try:
                value = check_literal(other, role="Column literal")
            except DefinitionGraphError as error:
                reject(
                    "a finite immutable value literal",
                    str(error),
                    "Pass a finite typed literal or use is_null().",
                    "Column",
                )
            if value is None:
                reject(
                    "a non-null value literal",
                    "None",
                    "Use Column.is_null() for missing values.",
                    "Column",
                )
            if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
                reject(
                    "a timezone-aware timestamp literal",
                    repr(value),
                    "Pass an aware datetime; use date for date-valued Columns.",
                    "Column",
                )
            expression, unit, kind = ValueLiteral(value), None, literal_kind(value)
        comparison = operation in ("eq", "ne", "lt", "le", "gt", "ge")
        if operation in ("and", "or"):
            if self._kind != "boolean" or kind != "boolean":
                reject(
                    "boolean Columns",
                    f"{self._kind}, {kind}",
                    "Combine predicates with & or |.",
                    "Column",
                )
        elif not comparison and (
            self._kind not in ("numeric", "duration", "unresolved")
            or kind not in ("numeric", "duration", "unresolved")
        ):
            reject(
                "numeric Columns",
                f"{self._kind}, {kind}",
                "Select a numeric value for arithmetic.",
                "Column",
            )
        if comparison and self._kind != kind and "unresolved" not in (self._kind, kind):
            reject(
                "matching value kinds",
                f"{self._kind}, {kind}",
                "Compare compatible values.",
                "Column",
            )
        if operation in ("add", "sub", "eq", "ne", "lt", "le", "gt", "ge"):
            if isinstance(other, Column) and self._unit != unit:
                reject(
                    "matching units",
                    f"{self._unit!r}, {unit!r}",
                    "Use Columns with the same declared unit.",
                    "Column",
                )
            result_unit = self._unit
        elif operation == "mul":
            result_unit = (
                self._unit
                if not isinstance(other, Column)
                else None
                if self._unit is None or unit is None
                else unit
                if self._unit == "1"
                else self._unit
                if unit == "1"
                else f"{self._unit}*{unit}"
            )
        elif operation == "div":
            if not isinstance(other, Column):
                result_unit = (
                    None
                    if self._unit is None
                    else ("1" if self._unit == "1" else f"1/{self._unit}")
                    if reverse
                    else self._unit
                )
            else:
                result_unit = (
                    None
                    if self._unit is None or unit is None
                    else "1"
                    if self._unit == unit
                    else self._unit
                    if unit == "1"
                    else f"{self._unit}/{unit}"
                )
        else:
            result_unit = None
        left, right = (expression, self._expression) if reverse else (self._expression, expression)
        result_kind: ValueKind = (
            "boolean" if comparison or operation in ("and", "or") else self._kind
        )
        return Column(
            self._input_id,
            ValueBinary(operation, left, right, zero_denominator),
            None if result_kind == "boolean" else result_unit,
            result_kind,
            self._frame,
        )

    def __add__(self, other: Column | LiteralValue) -> Column:
        """Add two nullable values pointwise.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``column + 1``.
        Constraints: Compatible units are required. Construction performs no I/O.
        """
        return self._binary(other, "add")

    def __radd__(self, other: LiteralValue) -> Column:
        """Add a literal to each nullable value.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``1 + column``.
        Constraints: Compatible units are required. Construction performs no I/O.
        """
        return self._binary(other, "add", reverse=True)

    def __sub__(self, other: Column | LiteralValue) -> Column:
        """Subtract two nullable values pointwise.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``column - 1``.
        Constraints: Compatible units are required. Construction performs no I/O.
        """
        return self._binary(other, "sub")

    def __rsub__(self, other: LiteralValue) -> Column:
        """Subtract each nullable value from a literal.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``1 - column``.
        Constraints: Compatible units are required. Construction performs no I/O.
        """
        return self._binary(other, "sub", reverse=True)

    def __mul__(self, other: Column | LiteralValue) -> Column:
        """Multiply nullable values pointwise.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``column * 2``.
        Constraints: Units follow numeric multiplication. Construction performs no I/O.
        """
        return self._binary(other, "mul")

    def __rmul__(self, other: LiteralValue) -> Column:
        """Multiply a literal by nullable values.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``2 * column``.
        Constraints: Units follow numeric multiplication. Construction performs no I/O.
        """
        return self._binary(other, "mul", reverse=True)

    def __truediv__(self, other: Column | LiteralValue) -> Column:
        """Divide nullable values with null at a zero denominator.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``column / 2``.
        Constraints: Use divide for an explicit error policy. Construction performs no I/O.
        """
        return self.divide(other)

    def __rtruediv__(self, other: LiteralValue) -> Column:
        """Divide a literal by nullable values, with null at zero.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``2 / column``.
        Constraints: NA values propagate. Construction performs no I/O.
        """
        return self._binary(other, "div", reverse=True)

    def __neg__(self) -> Column:
        """Negate nullable numeric values pointwise.

        Args: None. Returns: A lazy Column over this exact input.
        Example: ``-column``. Constraints: Numeric kinds and units are preserved. No I/O occurs.
        """
        return Column(
            self._input_id,
            ValueUnary("neg", self._expression),
            self._unit,
            self._kind,
            self._frame,
        )

    # DSL comparisons deliberately build predicates instead of Python booleans.
    def __eq__(self, other: object) -> Column:  # type: ignore[override]
        """Compare compatible values for equality.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``column == 1``.
        Constraints: Use is_null to test missing values. Construction performs no I/O.
        """
        return self._compare(other, "eq")

    def __ne__(self, other: object) -> Column:  # type: ignore[override]
        """Compare compatible values for inequality.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``column != 1``.
        Constraints: Use is_null to test missing values. Construction performs no I/O.
        """
        return self._compare(other, "ne")

    def _compare(
        self, other: object, operation: Literal["eq", "ne", "lt", "le", "gt", "ge"]
    ) -> Column:
        if (
            not isinstance(other, (Column, str, int, float, Decimal, date, timedelta))
            and other is not None
        ):
            reject(
                "a Column or immutable literal",
                type(other).__name__,
                "Compare a compatible value.",
                "Column",
            )
        return self._binary(other, operation)

    def __lt__(self, other: Column | LiteralValue) -> Column:
        """Compare compatible values in ascending order.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``column < 1``.
        Constraints: Value kinds and units must agree. Construction performs no I/O.
        """
        return self._binary(other, "lt")

    def __le__(self, other: Column | LiteralValue) -> Column:
        """Compare compatible values in ascending order, including equality.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``column <= 1``.
        Constraints: Value kinds and units must agree. Construction performs no I/O.
        """
        return self._binary(other, "le")

    def __gt__(self, other: Column | LiteralValue) -> Column:
        """Compare compatible values in descending order.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``column > 1``.
        Constraints: Value kinds and units must agree. Construction performs no I/O.
        """
        return self._binary(other, "gt")

    def __ge__(self, other: Column | LiteralValue) -> Column:
        """Compare compatible values in descending order, including equality.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``column >= 1``.
        Constraints: Value kinds and units must agree. Construction performs no I/O.
        """
        return self._binary(other, "ge")

    def __and__(self, other: Column) -> Column:
        """Combine two nullable boolean predicates.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``predicate & other``.
        Constraints: Both operands must be boolean Columns. Construction performs no I/O.
        """
        return self._binary(other, "and")

    def __or__(self, other: Column) -> Column:
        """Combine two nullable boolean alternatives.

        Args: other: Same-input Column or compatible immutable literal.
        Returns: A lazy Column over this exact input. Example: ``predicate | other``.
        Constraints: Both operands must be boolean Columns. Construction performs no I/O.
        """
        return self._binary(other, "or")

    def __invert__(self) -> Column:
        """Negate a nullable boolean predicate.

        Args: None. Returns: A lazy Column over this exact input.
        Example: ``~predicate``. Constraints: The operand must be a boolean Column. No I/O occurs.
        """
        if self._kind != "boolean":
            reject("a boolean Column", self._kind, "Negate a predicate with ~.", "Column")
        return Column(
            self._input_id,
            ValueUnary("not", self._expression),
            None,
            "boolean",
            self._frame,
        )

    def divide(
        self, other: Column | LiteralValue, *, zero_denominator: Literal["null", "error"] = "null"
    ) -> Column:
        """Divide pointwise, masking zero denominators by the node's policy.

        Args: other: Same-input Column or literal. zero_denominator: null or error.
        Returns: A lazy Column. Example: ``q['revenue'].divide(q['count'])``.
        Constraints: The policy affects this division only; NA propagates.
        """
        if zero_denominator not in ("null", "error"):
            reject(
                "null or error",
                repr(zero_denominator),
                "Choose the division policy.",
                "Column.divide",
            )
        return self._binary(other, "div", zero_denominator=zero_denominator)

    def is_null(self) -> Column:
        """Test nullable values; returns a same-input predicate without execution.

        Args: None. Returns: A same-input boolean Column.
        Example: ``q.where(~q['value'].is_null())``.
        Constraints: No rows are read; every value kind supports this test.
        """
        return Column(
            self._input_id,
            ValueUnary("is_null", self._expression),
            None,
            "boolean",
            self._frame,
        )

    def sum(self) -> _Statistic:
        """Request an ignore-NA sum, completed only by AnalysisFrame.reduce.

        Args: None. Returns: An unfinished statistic; empty/all-NA samples return NA.
        Constraints: Numeric or Duration values; evaluation belongs to AnalysisFrame.reduce.
        Example: ``q.reduce({'total': q['value'].sum()})``.
        """
        return self._statistic("sum")

    def mean(self) -> _Statistic:
        """Request an ignore-NA mean, completed only by AnalysisFrame.reduce.

        Args: None. Returns: An unfinished statistic; empty/all-NA samples return NA.
        Constraints: Numeric or Duration values; evaluation belongs to AnalysisFrame.reduce.
        Example: ``q.reduce({'average': q['value'].mean()})``.
        """
        return self._statistic("mean")

    def corr(self, other: Column, *, method: Literal["pearson", "spearman"]) -> _Statistic:
        """Request one paired correlation coefficient, completed by Frame.reduce.

        Args:
            other: Numeric Column from this exact Frame and Session input.
            method: Required pearson or spearman; Spearman uses average ranks.
        Returns: An unfinished scalar statistic with nullable float64 output and unit "1".
        Example: ``frame.reduce({'r': frame['x'].corr(frame['y'], method='pearson')})``.
        Constraints: Both endpoints must be numeric; Duration requires an explicit
        numeric source expression or external computation. Only common non-NA rows
        contribute, including zero; fewer than two pairs or a constant endpoint
        return NA. Self-pairing is allowed. This descriptive coefficient does not
        prove causality or support inference. Use Frame.correlate for a coefficient
        with its paired count; this statistic contributes only its named column.
        """
        if method not in ("pearson", "spearman"):
            reject(
                "pearson or spearman",
                repr(method),
                "Choose an explicit supported correlation method.",
                "Column.corr",
            )
        return self._pair_statistic(other, method, location="Column.corr")

    def pair_count(self, other: Column) -> _Statistic:
        """Count common non-NA endpoint values, completed by Frame.reduce.

        Args: other: Numeric Column from this exact Frame and Session input.
        Returns: An unfinished nonnullable int64 statistic with no declared unit.
        Example: ``frame.reduce({'n': frame['x'].pair_count(frame['x'])})``.
        Constraints: Self-pairing counts this expression's non-NA rows. Duration
        requires an explicit numeric source expression or external computation.
        Zero and constant values count; zero-pair groups remain present. Counting
        does not compute ranks or a coefficient. Frame.correlate includes this
        count, which is a row count rather than an independent sample size.
        """
        return self._pair_statistic(other, "pair_count", location="Column.pair_count")

    def _pair_statistic(
        self,
        other: Column,
        method: Literal["pearson", "spearman", "pair_count"],
        *,
        location: str,
    ) -> _Statistic:
        if method not in ("pearson", "spearman", "pair_count"):
            reject(
                "pearson, spearman or pair_count",
                repr(method),
                "Choose a supported paired statistic.",
                location,
            )
        if not isinstance(other, Column):
            reject(
                "a numeric Column from this exact Frame input",
                f"right endpoint: {type(other).__name__}",
                "Acquire both numeric Columns from the same Frame, or align Frames first.",
                location,
            )
        if (
            self._input_id != other._input_id
            or self._frame is None
            or other._frame is None
            or self._frame._local.owner != other._frame._local.owner
        ):
            reject(
                "Columns from the same exact Store/Session/Frame input",
                "left and right endpoints from different Frame inputs",
                "Acquire both numeric Columns from the same Frame, or align Frames first.",
                location,
            )
        for side, column in (("left", self), ("right", other)):
            if column._kind not in ("numeric", "unresolved"):
                from marivo.analysis.core.data_schema import DataSchema
                from marivo.analysis.core.frame_expression import expression_type

                assert column._frame is not None
                received = (
                    repr(expression_type(column._frame._schema, column._expression))
                    if isinstance(column._frame._schema, DataSchema)
                    else column._kind
                )
                reject(
                    "a numeric Column (int64, float64 or Decimal)",
                    f"{side} endpoint: {received}",
                    "Construct a source expression with an explicit numeric unit, or use external computation.",
                    location,
                )
        return _Statistic(
            self._input_id,
            PairStatistic(method, self._expression, other._expression, location),
            None if method == "pair_count" else "1",
            self._frame,
        )

    def _statistic(self, method: Literal["sum", "mean"]) -> _Statistic:
        if self._kind not in ("numeric", "duration", "unresolved"):
            reject("a numeric Column", self._kind, "Reduce a numeric value.", "Column")
        return _Statistic(
            self._input_id, Statistic(method, self._expression), self._unit, self._frame
        )

    def __bool__(self) -> bool:
        reject(
            "a lazy Column predicate",
            "Python truth conversion",
            "Use & / | and frame.where; call compute explicitly.",
            "Column",
        )


@dataclass(frozen=True, slots=True)
class _Statistic:
    input_id: str | None
    definition: Statistic | PairStatistic
    unit: str | None
    _frame: AnalysisFrame | None = None


def row_count() -> _Statistic:
    """Request the count of all current coordinates, completed by AnalysisFrame.reduce.

    Args: None. Returns: An input-independent unfinished statistic.
    Example: ``q.reduce({'rows': mv.row_count()})``.
    Constraints: Counts rows independently of every value's NA mask.
    """
    return _Statistic(None, Statistic("row_count"), None)
