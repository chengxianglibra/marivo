"""Resolved semantic values that expressions are built from (private, R1.2).

A bare ``Ref`` is only a lookup key: resolution against one loaded Catalog
returns an immutable value describing its consumed definition, together
with the facts later expression operations check (contribution root, unit,
sum permission, default time role, relationship endpoints and cardinality).
Resolution reads compiled Catalog state only; it never touches a datasource.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias, overload

from marivo._definition.expressions import (
    CompareOp,
    ContributionFilter,
    EntityCount,
    FieldCompare,
    FieldIsNull,
    FieldPredicate,
    MeasureAssertion,
    MeasureDeclaration,
    MetricAdd,
    MetricConstant,
    MetricDeclaration,
    MetricDivide,
    MetricMultiply,
    MetricNegate,
    MetricNode,
    MetricSubtract,
    PerReduce,
    PerWeightedMean,
    PredAnd,
    PredNot,
    PredOr,
    Reducer,
    RowAdd,
    RowCompare,
    RowConstant,
    RowDivide,
    RowField,
    RowMultiply,
    RowNegate,
    RowNode,
    RowSubtract,
    SourceAggregate,
    SourceWeightedMean,
    TimeBinding,
)
from marivo._definition.graph import _encode, computation_fingerprint, fingerprint, walk
from marivo._definition.types import (
    Authorization,
    AxisBinding,
    DefinitionGraphError,
    ExactRef,
    FiniteNumber,
    NullPolicy,
    SumAll,
    SumAllExcept,
    SumAxes,
    SumNone,
    SumPermission,
    ZeroDenominator,
)
from marivo._definition.types import (
    SourceLocation as DefinitionSourceLocation,
)
from marivo.refs import (
    DimensionKind,
    EntityKind,
    MeasureKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    SemanticKindTag,
    TimeDimensionKind,
)
from marivo.refs import ref as ref_factory
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._dsl_authoring import (
    AdditiveAllV1,
    AdditiveOverV1,
    AdditivityPolicy,
    NonAdditiveV1,
)
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.ir import SemiAdditive, SourceLocation, TargetRelationshipContract
from marivo.semantic.typing import AiContextValue
from marivo.semantic.unit_algebra import (
    divide_unit_states,
    linear_unit,
    linear_units_conflict,
    multiply_unit_states,
    render_unit,
    unit_state,
)
from marivo.semantic.validator import Registry, normalize_target_relationship

if TYPE_CHECKING:
    from marivo.datasource.ir import DatasourceIR
    from marivo.semantic.loader import LoaderContext

Cardinality: TypeAlias = Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]
RowOperator: TypeAlias = Literal["add", "subtract", "multiply", "divide"]
_SYMBOLS: dict[RowOperator, str] = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/"}


def _fail(kind: ErrorKind, message: str, *, expected: str, received: str, action: str) -> NoReturn:
    raise SemanticExpressionError(
        kind=kind, message=message, expected=expected, received=received, action=action
    )


def _received(value: object) -> str:
    if isinstance(value, int) and value.bit_length() > 192:
        # Describe huge ints by size; decimal conversion is slow and can exceed the str limit.
        return f"{type(value).__name__} with {value.bit_length()} bits"
    return f"{type(value).__name__} {repr(value)[:60]}"


class _ExpressionDisplay:
    """Bounded metadata-only inspection shared by constructed expression values."""

    __slots__ = ()

    def show(self) -> None:
        """Print this expression's stage, roots and facts without reading source data.

        Returns: None.
        Example: ``amount.show()``.
        Constraints: This is construction metadata, not publication or execution evidence.
        """
        _show_expression(self)


@dataclass(frozen=True, slots=True)
class _Source:
    """Row operand of one source reduction on its contribution root."""

    node: RowNode
    root: ExactRef
    unit: str | None
    default_time: ExactRef | None


class _Countable(_ExpressionDisplay, ABC):
    """Source count reductions shared by every field and numeric row expression."""

    __slots__ = ()

    @abstractmethod
    def _source(self) -> _Source: ...

    def count(self, *, time: TimeField | None = None) -> Metric:
        """Count non-NA values on the contribution root; the result unit is ``1``."""
        return _reduce(self._source(), "count", time=time)

    def count_distinct(self, *, time: TimeField | None = None) -> Metric:
        """Count distinct non-NA values on the contribution root; the result unit is ``1``."""
        return _reduce(self._source(), "count_distinct", time=time)


class _RowArithmetic(_Countable):
    """Numeric row operators shared by declared Measures and derived row expressions."""

    __slots__ = ()

    @abstractmethod
    def _as_row(self) -> NumericRowExpr: ...

    def _source(self) -> _Source:
        row = self._as_row()
        return _Source(row.node, row.root, row.unit, row.default_time)

    def __add__(self, other: RowOperand | FiniteNumber) -> NumericRowExpr:
        return _combine("add", self._as_row(), _operand(other, operation="row add"))

    def __radd__(self, other: FiniteNumber) -> NumericRowExpr:
        return _combine("add", _operand(other, operation="row add"), self._as_row())

    def __sub__(self, other: RowOperand | FiniteNumber) -> NumericRowExpr:
        return _combine("subtract", self._as_row(), _operand(other, operation="row subtract"))

    def __rsub__(self, other: FiniteNumber) -> NumericRowExpr:
        return _combine("subtract", _operand(other, operation="row subtract"), self._as_row())

    def __mul__(self, other: RowOperand | FiniteNumber) -> NumericRowExpr:
        return _combine("multiply", self._as_row(), _operand(other, operation="row multiply"))

    def __rmul__(self, other: FiniteNumber) -> NumericRowExpr:
        return _combine("multiply", _operand(other, operation="row multiply"), self._as_row())

    def __truediv__(self, other: RowOperand | FiniteNumber) -> NumericRowExpr:
        return self.divide(other)

    def __rtruediv__(self, other: FiniteNumber) -> NumericRowExpr:
        return _combine("divide", _operand(other, operation="row divide"), self._as_row())

    def __neg__(self) -> NumericRowExpr:
        row = self._as_row()
        return NumericRowExpr(
            RowNegate(row.node),
            row.root,
            row.unit,
            row.permission,
            row.default_time,
            row.coordinates,
        )

    def sum(self, *, time: TimeField | None = None, nulls: NullPolicy = "ignore") -> Metric:
        """Sum over the contribution root; requires a sum permission on the row expression."""
        row = self._as_row()
        if isinstance(row.permission, SumNone):
            _fail(
                ErrorKind.MISSING_SUM_PERMISSION,
                f"sum is not permitted for this row expression on {row.root.ref.path}.",
                expected="a row expression whose Measures grant a sum permission",
                received=f"{type(row.node).__name__} with no sum permission",
                action=(
                    "Declare a Measure with an explicit additivity for this business quantity "
                    "and sum it, or use a non-additive reducer such as mean."
                ),
            )
        return _reduce(self._source(), "sum", time=time, nulls=nulls)

    def mean(self, *, time: TimeField | None = None, nulls: NullPolicy = "ignore") -> Metric:
        """Mean of non-NA values; empty or all-NA input yields NA."""
        return _reduce(self._source(), "mean", time=time, nulls=nulls)

    def min(self, *, time: TimeField | None = None, nulls: NullPolicy = "ignore") -> Metric:
        """Minimum of non-NA values; empty or all-NA input yields NA."""
        return _reduce(self._source(), "min", time=time, nulls=nulls)

    def max(self, *, time: TimeField | None = None, nulls: NullPolicy = "ignore") -> Metric:
        """Maximum of non-NA values; empty or all-NA input yields NA."""
        return _reduce(self._source(), "max", time=time, nulls=nulls)

    def median(self, *, time: TimeField | None = None, nulls: NullPolicy = "ignore") -> Metric:
        """Exact median (quantile 0.5 with linear interpolation)."""
        return _reduce(self._source(), "median", time=time, nulls=nulls)

    def quantile(
        self, q: float, *, time: TimeField | None = None, nulls: NullPolicy = "ignore"
    ) -> Metric:
        """Exact linear-interpolation quantile with finite ``0 <= q <= 1``."""
        return _reduce(self._source(), "quantile", time=time, nulls=nulls, q=q)

    def weighted_mean(
        self,
        *,
        weight: RowOperand,
        time: TimeField | None = None,
        nulls: NullPolicy = "ignore",
        zero_denominator: ZeroDenominator = "null",
    ) -> Metric:
        """Weighted mean over common non-NA value/weight pairs of one contribution root."""
        value = self._as_row()
        if not isinstance(weight, _RowArithmetic):
            _fail(
                ErrorKind.INVALID_EXPRESSION_STAGE,
                "weighted_mean weight= expects a numeric row expression.",
                expected="a Measure or numeric row expression on the same contribution root",
                received=_received(weight),
                action="Pass a Measure or numeric row expression as weight=.",
            )
        weights = weight._as_row()
        if weights.root.ref != value.root.ref:
            _fail(
                ErrorKind.CROSS_ROOT_ROW_EXPRESSION,
                "weighted_mean pairs values and weights from different contribution roots.",
                expected="value and weight on one contribution root",
                received=f"{value.root.ref.path} weighted by {weights.root.ref.path}",
                action="Use a weight declared on the same Entity as the value.",
            )
        binding = _time_binding(time, value.root, value.default_time, operation="weighted_mean")
        policy = _nulls(nulls, operation="weighted_mean")
        zero = _zero_denominator(zero_denominator)
        try:
            node = SourceWeightedMean(
                value.node, weights.node, binding, nulls=policy, zero_denominator=zero
            )
        except DefinitionGraphError as error:
            raise SemanticExpressionError.from_definition(
                error, operation="weighted_mean"
            ) from error
        return Metric(node, (value.root,), value.unit)

    def divide(
        self, other: RowOperand | FiniteNumber, *, zero_denominator: ZeroDenominator = "null"
    ) -> NumericRowExpr:
        """Divide row-wise; ``/`` is this call with the default ``"null"`` policy."""
        policy = _zero_denominator(zero_denominator)
        return _combine(
            "divide",
            self._as_row(),
            _operand(other, operation="row divide"),
            zero_denominator=policy,
        )


class _FieldComparisons:
    """Typed field predicates; comparisons build Predicate values, never Python booleans."""

    __slots__ = ()
    ref: ExactRef
    root: ExactRef
    __hash__ = object.__hash__

    def __eq__(self, value: object) -> Predicate:  # type: ignore[override]  # builds a predicate
        return self._compare("eq", value)

    def __ne__(self, value: object) -> Predicate:  # type: ignore[override]  # builds a predicate
        return self._compare("ne", value)

    def __lt__(self, value: object) -> Predicate:
        return self._compare("lt", value)

    def __le__(self, value: object) -> Predicate:
        return self._compare("le", value)

    def __gt__(self, value: object) -> Predicate:
        return self._compare("gt", value)

    def __ge__(self, value: object) -> Predicate:
        return self._compare("ge", value)

    def is_null(self) -> Predicate:
        """Test this native field for a missing value on its contribution root.

        Returns: A Predicate retaining the exact field and contribution root.
        Example: ``revenue.where(region.is_null())``.
        Constraints: Only native fields qualify; a routed TimeField is a time role.
        """
        self._require_native(operation="is_null")
        return Predicate(FieldIsNull(self.ref), (self.root,))

    def _require_native(self, *, operation: str) -> None:
        """Hook for fields that are only valid as a time role; native fields pass."""

    def _compare(self, op: CompareOp, value: object) -> Predicate:
        self._require_native(operation="comparison")
        if value is None:
            _fail(
                ErrorKind.INVALID_EXPRESSION_VALUE,
                f"Field {self.ref.ref.path} is compared with None.",
                expected="a non-null literal",
                received="None",
                action="Use field.is_null() to test for missing values.",
            )
        if not isinstance(value, (str, int, float, Decimal, date)):
            _fail(
                ErrorKind.INVALID_EXPRESSION_VALUE,
                f"Field {self.ref.ref.path} is compared with a non-literal value.",
                expected="str, int, float, Decimal, bool, date, or datetime literal",
                received=_received(value),
                action="Compare the field with a literal; combine predicates with &, |, ~.",
            )
        try:
            return Predicate(FieldCompare(self.ref, op, value), (self.root,))
        except DefinitionGraphError as error:
            raise SemanticExpressionError.from_definition(
                error, operation="field comparison"
            ) from error


@dataclass(frozen=True, slots=True)
class Entity(_ExpressionDisplay):
    """Resolved Entity: one contribution root with its complete primary key."""

    ref: ExactRef
    primary_key: tuple[str, ...]
    default_time: ExactRef | None

    def count(self, *, time: TimeField | None = None) -> Metric:
        """Count complete contribution rows of this Entity; the result unit is ``1``."""
        binding = _time_binding(time, self.ref, self.default_time, operation="count")
        return Metric(EntityCount(self.ref, binding), (self.ref,), "1")

    def __repr__(self) -> str:
        return f"Entity {self.ref.ref.path}@{self.ref.version[:19]} (use .show())"


@dataclass(frozen=True, slots=True, eq=False)
class Dimension(_FieldComparisons, _Countable):
    """Resolved categorical field: typed predicates and count reductions, no arithmetic."""

    ref: ExactRef
    root: ExactRef
    default_time: ExactRef | None

    def _source(self) -> _Source:
        return _Source(
            RowField(self.ref, self.root, None, SumNone()), self.root, None, self.default_time
        )

    def __repr__(self) -> str:
        return f"Dimension {self.ref.ref.path}@{self.ref.version[:19]} (use .show())"


@dataclass(frozen=True, slots=True, eq=False)
class TimeField(_FieldComparisons, _Countable):
    """Resolved time field: typed predicates and count reductions, no arithmetic.

    ``path`` is empty for a field read on its own Entity; ``via`` returns the same
    field bound to an ordered single-valued relationship path from another root,
    usable only as a reduction's ``time=`` role.
    """

    ref: ExactRef
    root: ExactRef
    default_time: ExactRef | None
    path: tuple[Relationship, ...] = ()

    def via(self, path: tuple[Relationship, ...]) -> TimeField:
        """Bind this time field through a declared single-valued relationship path.

        ``path`` is a non-empty tuple of resolved Relationships in declared
        direction, each one_to_one or many_to_one, ending at this field's Entity.
        Example: ``customer_created_at.via((orders_to_customers,))``.
        """
        if self.path:
            _fail(
                ErrorKind.INVALID_TIME_PATH,
                f"TimeField {self.ref.ref.path} is already bound through a path.",
                expected="a native TimeField",
                received=repr(self),
                action="Call via once on the resolved field with the complete ordered path.",
            )
        if not isinstance(path, tuple) or not path:
            _fail(
                ErrorKind.INVALID_TIME_PATH,
                "TimeField.via expects a non-empty tuple of relationships.",
                expected="(relationship,) or (first, second, ...)",
                received=_received(path),
                action="Pass resolved Relationships as a tuple, e.g. via((orders_to_customers,)).",
            )
        for step in path:
            if not isinstance(step, Relationship):
                _fail(
                    ErrorKind.INVALID_TIME_PATH,
                    "TimeField.via path contains a value that is not a resolved Relationship.",
                    expected="Relationship values resolved from the Catalog",
                    received=_received(step),
                    action="Resolve each relationship first; a bare Ref is only a lookup key.",
                )
            if step.cardinality not in ("many_to_one", "one_to_one"):
                _fail(
                    ErrorKind.INVALID_TIME_PATH,
                    f"Relationship {step.ref.ref.path} can reach several rows of its target.",
                    expected="one_to_one or many_to_one steps in declared direction",
                    received=step.cardinality,
                    action=(
                        "Use a single-valued relationship path; a time role cannot fan out "
                        "a contribution to several time values."
                    ),
                )
        for first, second in pairwise(path):
            if first.to_entity.ref != second.from_entity.ref:
                _fail(
                    ErrorKind.INVALID_TIME_PATH,
                    "TimeField.via path steps are not connected.",
                    expected=f"a step starting at {first.to_entity.ref.path}",
                    received=f"{second.ref.ref.path} from {second.from_entity.ref.path}",
                    action="Order the relationships so each step starts where the previous ends.",
                )
        if path[-1].to_entity.ref != self.root.ref:
            _fail(
                ErrorKind.INVALID_TIME_PATH,
                f"TimeField.via path does not end at {self.root.ref.path}.",
                expected=f"a last step ending at {self.root.ref.path}",
                received=f"{path[-1].ref.ref.path} to {path[-1].to_entity.ref.path}",
                action="End the path at the Entity that declares this time field.",
            )
        return TimeField(self.ref, self.root, self.default_time, path)

    def _require_native(self, *, operation: str) -> None:
        if self.path:
            _fail(
                ErrorKind.INVALID_TIME_PATH,
                f"A via-bound time field cannot be used in {operation}.",
                expected="a native field for predicates, counts, and sample axes",
                received=repr(self),
                action="Use the via-bound field only as time= of a source reduction.",
            )

    def _source(self) -> _Source:
        self._require_native(operation="a count reduction")
        return _Source(
            RowField(self.ref, self.root, None, SumNone()), self.root, None, self.default_time
        )

    def __repr__(self) -> str:
        via = f" via {len(self.path)} step(s)" if self.path else ""
        return f"TimeField {self.ref.ref.path}@{self.ref.version[:19]}{via} (use .show())"


@dataclass(frozen=True, slots=True, eq=False)
class Measure(_FieldComparisons, _RowArithmetic):
    """Resolved numeric field with its declared unit and sum permission."""

    ref: ExactRef
    root: ExactRef
    unit: str | None
    permission: SumPermission
    default_time: ExactRef | None
    coordinates: tuple[ExactRef, ...] = ()

    def _as_row(self) -> NumericRowExpr:
        field = RowField(self.ref, self.root, self.unit, self.permission)
        return NumericRowExpr(
            field, self.root, self.unit, self.permission, self.default_time, self.coordinates
        )

    def __repr__(self) -> str:
        return f"Measure {self.ref.ref.path}@{self.ref.version[:19]} (use .show())"


@dataclass(frozen=True, slots=True)
class Relationship(_ExpressionDisplay):
    """Resolved directed relationship between two Entities."""

    ref: ExactRef
    from_entity: ExactRef
    to_entity: ExactRef
    cardinality: Cardinality

    def __repr__(self) -> str:
        return f"Relationship {self.ref.ref.path}@{self.ref.version[:19]} (use .show())"


@dataclass(frozen=True, slots=True, eq=False)
class NumericRowExpr(_RowArithmetic):
    """Derived numeric expression evaluated on each record of one contribution root.

    ``unit`` and ``permission`` are derived from the operands; a derived
    expression never gains a sum permission its inputs did not grant.
    """

    node: RowNode
    root: ExactRef
    unit: str | None
    permission: SumPermission
    default_time: ExactRef | None
    coordinates: tuple[ExactRef, ...] = ()

    __hash__ = object.__hash__

    def _as_row(self) -> NumericRowExpr:
        return self

    def __eq__(self, value: object) -> Predicate:  # type: ignore[override]  # builds a predicate
        return self._compare("eq", value)

    def __ne__(self, value: object) -> Predicate:  # type: ignore[override]  # builds a predicate
        return self._compare("ne", value)

    def __lt__(self, value: object) -> Predicate:
        return self._compare("lt", value)

    def __le__(self, value: object) -> Predicate:
        return self._compare("le", value)

    def __gt__(self, value: object) -> Predicate:
        return self._compare("gt", value)

    def __ge__(self, value: object) -> Predicate:
        return self._compare("ge", value)

    def _compare(self, op: CompareOp, value: object) -> Predicate:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            _fail(
                ErrorKind.INVALID_EXPRESSION_VALUE,
                "A numeric row expression is compared with a non-numeric value.",
                expected="a finite int, float, or Decimal constant",
                received=_received(value),
                action="Compare the row expression with a numeric constant, e.g. margin > 0.",
            )
        try:
            return Predicate(RowCompare(self.node, op, value), (self.root,))
        except DefinitionGraphError as error:
            raise SemanticExpressionError.from_definition(
                error, operation="row comparison"
            ) from error

    def __repr__(self) -> str:
        fingerprint = computation_fingerprint(self.node)[:12]
        return f"NumericRowExpr {type(self.node).__name__} on {self.root.ref.path} {fingerprint} (use .show())"


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class DeclaredMeasure(NumericRowExpr):
    """A named row expression; declaration metadata is separate from its computation."""

    declaration: MeasureDeclaration = field(kw_only=True)

    def __repr__(self) -> str:
        return (
            f"Measure {self.root.ref.path}.{self.declaration.name} (local declaration; use .show())"
        )


def declare_measure(
    row: RowOperand,
    *,
    name: str,
    unit: str | None,
    additivity: AdditivityPolicy | None,
    ai_context: AiContextValue | None,
    location: SourceLocation,
) -> DeclaredMeasure:
    """Attach local metadata and only genuinely new, compatible business assertions."""
    value = row._as_row()
    declared_unit = render_unit(unit_state(unit)) if unit is not None else value.unit
    if unit is not None and value.unit is not None:
        if linear_units_conflict((unit, value.unit)):
            _fail(
                ErrorKind.INCOMMENSURABLE_LINEAR_UNITS,
                f"Measure {name!r} cannot relabel its inferred unit.",
                expected=f"a unit compatible with {value.unit}",
                received=unit,
                action=f"Keep unit={value.unit!r}, or change the row expression explicitly.",
            )
        declared_unit = value.unit
    permission = value.permission
    if additivity is not None:
        axes = {axis.ref.path: axis for axis in value.coordinates}
        paths = (
            additivity.coordinates
            if isinstance(additivity, AdditiveOverV1)
            else additivity.exceptions
            if isinstance(additivity, AdditiveAllV1)
            else ()
        )
        missing = tuple(path for path in paths if path not in axes)
        if missing:
            _fail(
                ErrorKind.INVALID_EXPRESSION_VALUE,
                f"Measure {name!r} grants permission on unknown or foreign axes.",
                expected=f"declared coordinates on {value.root.ref.path}",
                received=", ".join(missing),
                action=f"Choose existing coordinates: {', '.join(sorted(axes)) or '(none)'}.",
            )
        if isinstance(additivity, AdditiveOverV1):
            permission = SumAxes(tuple(axes[path] for path in paths))
        elif isinstance(additivity, AdditiveAllV1):
            permission = SumAllExcept(tuple(axes[path] for path in paths)) if paths else SumAll()
        elif isinstance(additivity, NonAdditiveV1):
            permission = SumNone()
        else:
            _fail(
                ErrorKind.INVALID_EXPRESSION_VALUE,
                "Measure additivity requires a closed additivity policy.",
                expected="ms.additive(...), ms.additive_all(...), or ms.non_additive()",
                received=type(additivity).__name__,
                action="Construct additivity with the public policy helper before declaring the Measure.",
            )
        # Arithmetic can lose sum permission, but cannot lose a leaf's fixed time axis.
        closure = walk((value.node,), resolve=lambda _ref: None)
        fixed = tuple(
            dict.fromkeys(
                axis
                for node in closure.order
                if isinstance(node, (RowField, MeasureAssertion))
                if isinstance(node.permission, SumAllExcept)
                for axis in node.permission.axes
                if axis.ref.kind is SemanticKind.TIME_DIMENSION
            )
        )
        erased = tuple(
            axis
            for axis in fixed
            if (
                isinstance(permission, SumAll)
                or (
                    isinstance(permission, SumAxes)
                    and axis.ref in {item.ref for item in permission.axes}
                )
                or (
                    isinstance(permission, SumAllExcept)
                    and axis.ref not in {item.ref for item in permission.axes}
                )
            )
        )
        if erased:
            _fail(
                ErrorKind.INVALID_EXPRESSION_VALUE,
                f"Measure {name!r} cannot erase fixed time constraints.",
                expected="sum permission that keeps the dependency's time axes fixed",
                received=", ".join(axis.ref.path for axis in erased),
                action="Keep these time axes in additive_all(except_=...), or use non_additive().",
            )
    asserted_unit = declared_unit if declared_unit != value.unit else None
    if (
        isinstance(permission, (SumAxes, SumAllExcept))
        and type(permission) is type(value.permission)
        and isinstance(value.permission, (SumAxes, SumAllExcept))
        and frozenset(permission.axes) == frozenset(value.permission.axes)
    ):
        permission = value.permission
    asserted_permission = permission if permission != value.permission else None
    node = value.node
    if asserted_unit is not None or asserted_permission is not None:
        version = fingerprint(_encode((node, asserted_unit, asserted_permission), {}))
        authorization = Authorization(
            ExactRef(ref_factory.measure(f"{value.root.ref.path}.{name}"), f"sha256:{version}")
        )
        node = MeasureAssertion(node, asserted_unit, asserted_permission, authorization)
    return DeclaredMeasure(
        node,
        value.root,
        declared_unit,
        permission,
        value.default_time,
        value.coordinates,
        declaration=MeasureDeclaration(
            name, node, ai_context, DefinitionSourceLocation(location.file, location.line)
        ),
    )


@dataclass(frozen=True, slots=True)
class Predicate(_ExpressionDisplay):
    """Closed typed predicate over field values of the contribution roots it reads.

    Combine with ``&``, ``|`` and ``~``; a predicate has no Python truth value.
    """

    node: FieldPredicate
    roots: tuple[ExactRef, ...]

    def __and__(self, other: Predicate) -> Predicate:
        right = _require_predicate(other, symbol="&")
        return Predicate(PredAnd((self.node, right.node)), _merge_roots(self, right))

    def __or__(self, other: Predicate) -> Predicate:
        right = _require_predicate(other, symbol="|")
        return Predicate(PredOr((self.node, right.node)), _merge_roots(self, right))

    def __invert__(self) -> Predicate:
        return Predicate(PredNot(self.node), self.roots)

    def __bool__(self) -> NoReturn:
        _fail(
            ErrorKind.INVALID_EXPRESSION_STAGE,
            "A semantic predicate has no Python truth value.",
            expected="a predicate combined with &, |, ~ and passed to a where condition",
            received="bool(), and, or, not, or if applied to a Predicate",
            action="Combine predicates with &, |, ~ instead of Python boolean operators.",
        )

    def __repr__(self) -> str:
        roots = ", ".join(root.ref.path for root in self.roots[:3])
        extra = f" +{len(self.roots) - 3}" if len(self.roots) > 3 else ""
        return f"Predicate {type(self.node).__name__} on {roots}{extra} (use .show())"


Field: TypeAlias = Dimension | TimeField | Measure
RowOperand: TypeAlias = Measure | NumericRowExpr
ResolvedValue: TypeAlias = Entity | Field | Relationship


@dataclass(frozen=True, slots=True)
class _Constant:
    """Finite numeric literal at one usage position; it carries no root of its own."""

    node: RowConstant


def _numeric_literal(value: object, *, operation: str, expected: str) -> FiniteNumber:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        _fail(
            ErrorKind.INVALID_EXPRESSION_VALUE,
            f"{operation} received a value that is not a numeric constant.",
            expected=expected,
            received=_received(value),
            action="Pass a finite numeric constant; bool is not a number in expressions.",
        )
    return value


def _operand(value: object, *, operation: str) -> NumericRowExpr | _Constant:
    if isinstance(value, _RowArithmetic):
        return value._as_row()
    if isinstance(value, Metric):
        _fail(
            ErrorKind.INVALID_EXPRESSION_STAGE,
            f"{operation} mixes a completed Metric into a row expression.",
            expected="a Measure, numeric row expression, or finite numeric constant",
            received=repr(value),
            action=(
                "Finish the row expression and reduce it first, e.g. (amount - cost).sum(), "
                "then combine Metrics."
            ),
        )
    if isinstance(value, (Entity, Dimension, TimeField, Relationship, Predicate, MetricSamples)):
        _fail(
            ErrorKind.INVALID_EXPRESSION_STAGE,
            f"{operation} received a {type(value).__name__}, which has no numeric row value.",
            expected="a Measure, numeric row expression, or finite numeric constant",
            received=repr(value),
            action="Use a Measure or numeric row expression; count fields through reducers.",
        )
    literal = _numeric_literal(
        value,
        operation=operation,
        expected="a Measure, numeric row expression, or finite int, float, or Decimal",
    )
    try:
        return _Constant(RowConstant(literal))
    except DefinitionGraphError as error:
        raise SemanticExpressionError.from_definition(error, operation=operation) from error


def _zero_denominator(value: object) -> ZeroDenominator:
    if value == "null":
        return "null"
    if value == "error":
        return "error"
    _fail(
        ErrorKind.INVALID_EXPRESSION_VALUE,
        "divide received an unsupported zero_denominator policy.",
        expected='zero_denominator="null" or zero_denominator="error"',
        received=_received(value),
        action='Use "null" (default) or "error" for this division.',
    )


def _intersect(left: SumPermission, right: SumPermission) -> SumPermission:
    """Sum permission granted by both operands; never wider than either input."""
    if isinstance(left, SumNone) or isinstance(right, SumNone):
        return SumNone()
    if isinstance(left, SumAll):
        return right
    if isinstance(right, SumAll):
        return left
    left_refs = {axis.ref for axis in left.axes}
    right_refs = {axis.ref for axis in right.axes}
    if isinstance(left, SumAllExcept) and isinstance(right, SumAllExcept):
        return SumAllExcept(
            (*left.axes, *(axis for axis in right.axes if axis.ref not in left_refs))
        )
    if isinstance(left, SumAxes) and isinstance(right, SumAxes):
        axes = tuple(axis for axis in left.axes if axis.ref in right_refs)
    elif isinstance(left, SumAxes) and isinstance(right, SumAllExcept):
        axes = tuple(axis for axis in left.axes if axis.ref not in right_refs)
    else:
        assert isinstance(left, SumAllExcept) and isinstance(right, SumAxes)
        axes = tuple(axis for axis in right.axes if axis.ref not in left_refs)
    return SumAxes(axes) if axes else SumNone()


def _arith_unit(
    op: RowOperator,
    left: str | None,
    right: str | None,
    *,
    constant: Literal["left", "right"] | None,
    stage: Literal["Row", "Metric"],
) -> str | None:
    """Unit of one arithmetic use; a bare constant takes the other operand's unit for
    addition and subtraction and is dimensionless for multiplication and division."""
    if constant == "left":
        if op == "divide":
            return render_unit(divide_unit_states(unit_state("1"), unit_state(right)))
        return right
    if constant == "right":
        return left
    if op in ("add", "subtract"):
        if linear_units_conflict((left, right)):
            symbol = _SYMBOLS[op]
            _fail(
                ErrorKind.INCOMMENSURABLE_LINEAR_UNITS,
                f"{stage} {symbol} combines incompatible units {left} and {right}.",
                expected="operands with one commensurable unit",
                received=f"{left} {symbol} {right}",
                action=(
                    "Convert one operand to the other unit explicitly, then combine again; "
                    "units are never converted implicitly."
                ),
            )
        return linear_unit((left, right))
    states = (unit_state(left), unit_state(right))
    if op == "multiply":
        return render_unit(multiply_unit_states(*states))
    return render_unit(divide_unit_states(*states))


def _unit(
    op: RowOperator, left: NumericRowExpr | _Constant, right: NumericRowExpr | _Constant
) -> str | None:
    constant: Literal["left", "right"] | None = (
        "left" if isinstance(left, _Constant) else "right" if isinstance(right, _Constant) else None
    )
    return _arith_unit(
        op,
        left.unit if isinstance(left, NumericRowExpr) else None,
        right.unit if isinstance(right, NumericRowExpr) else None,
        constant=constant,
        stage="Row",
    )


def _permission(
    op: RowOperator, left: NumericRowExpr | _Constant, right: NumericRowExpr | _Constant
) -> SumPermission:
    if isinstance(left, _Constant):
        assert isinstance(right, NumericRowExpr)
        return SumNone() if op == "divide" else right.permission
    if isinstance(right, _Constant):
        return left.permission
    if op in ("add", "subtract"):
        return _intersect(left.permission, right.permission)
    return SumNone()


def _combine(
    op: RowOperator,
    left: NumericRowExpr | _Constant,
    right: NumericRowExpr | _Constant,
    *,
    zero_denominator: ZeroDenominator = "null",
) -> NumericRowExpr:
    rows = [operand for operand in (left, right) if isinstance(operand, NumericRowExpr)]
    roots = [row.root for row in rows]
    if any(root.ref != roots[0].ref for root in roots[1:]):
        symbol = _SYMBOLS[op]
        _fail(
            ErrorKind.CROSS_ROOT_ROW_EXPRESSION,
            f"Row {symbol} combines records of different contribution roots.",
            expected="operands on one contribution root",
            received=f"{roots[0].ref.path} {symbol} {roots[1].ref.path}",
            action="Reduce each root separately, then combine the resulting Metrics.",
        )
    unit = _unit(op, left, right)
    permission = _permission(op, left, right)
    node: RowNode
    match op:
        case "add":
            node = RowAdd(left.node, right.node)
        case "subtract":
            node = RowSubtract(left.node, right.node)
        case "multiply":
            node = RowMultiply(left.node, right.node)
        case "divide":
            node = RowDivide(left.node, right.node, zero_denominator=zero_denominator)
    coordinates = tuple(dict.fromkeys(axis for row in rows for axis in row.coordinates))
    return NumericRowExpr(node, rows[0].root, unit, permission, rows[0].default_time, coordinates)


def _require_predicate(value: object, *, symbol: str) -> Predicate:
    if isinstance(value, Predicate):
        return value
    _fail(
        ErrorKind.INVALID_EXPRESSION_STAGE,
        f"Predicate {symbol} received a value that is not a predicate.",
        expected="a Predicate built from a field or row comparison",
        received=_received(value),
        action="Build both sides from comparisons such as status == 'paid' or margin > 0.",
    )


def _merge_roots(left: Predicate, right: Predicate) -> tuple[ExactRef, ...]:
    return tuple(dict.fromkeys((*left.roots, *right.roots)))


@dataclass(frozen=True, slots=True)
class Metric(_ExpressionDisplay):
    """Immutable completed reduction over the contribution roots it reads.

    Construction resolves every policy onto the node; it never reads a source.
    """

    node: MetricNode
    roots: tuple[ExactRef, ...]
    unit: str | None

    def __add__(self, other: Metric | FiniteNumber) -> Metric:
        return _metric_combine("add", self, _metric_operand(other, operation="metric add"))

    def __radd__(self, other: FiniteNumber) -> Metric:
        return _metric_combine("add", _metric_operand(other, operation="metric add"), self)

    def __sub__(self, other: Metric | FiniteNumber) -> Metric:
        return _metric_combine(
            "subtract", self, _metric_operand(other, operation="metric subtract")
        )

    def __rsub__(self, other: FiniteNumber) -> Metric:
        return _metric_combine(
            "subtract", _metric_operand(other, operation="metric subtract"), self
        )

    def __mul__(self, other: Metric | FiniteNumber) -> Metric:
        return _metric_combine(
            "multiply", self, _metric_operand(other, operation="metric multiply")
        )

    def __rmul__(self, other: FiniteNumber) -> Metric:
        return _metric_combine(
            "multiply", _metric_operand(other, operation="metric multiply"), self
        )

    def __truediv__(self, other: Metric | FiniteNumber) -> Metric:
        return self.divide(other)

    def __rtruediv__(self, other: FiniteNumber) -> Metric:
        return _metric_combine("divide", _metric_operand(other, operation="metric divide"), self)

    def __neg__(self) -> Metric:
        return Metric(MetricNegate(self.node), self.roots, self.unit)

    def __pos__(self) -> Metric:
        """Unary plus is the identity: the same graph, policies, roots and unit."""
        return self

    def divide(
        self, other: Metric | FiniteNumber, *, zero_denominator: ZeroDenominator = "null"
    ) -> Metric:
        """Divide completed Metrics; ``/`` is this call with the default ``"null"`` policy."""
        policy = _zero_denominator(zero_denominator)
        return _metric_combine(
            "divide",
            self,
            _metric_operand(other, operation="metric divide"),
            zero_denominator=policy,
        )

    def where(self, predicate: Predicate) -> Metric:
        """Restrict the contributions of one source reduction by a typed predicate.

        Only a single source reduction, or one already filtered, accepts ``where``;
        filter the source children of a composite Metric before combining them.
        """
        if not isinstance(predicate, Predicate):
            _fail(
                ErrorKind.INVALID_EXPRESSION_STAGE,
                "Metric.where expects a typed field or row predicate.",
                expected="a Predicate such as status == 'paid' or (amount - cost) > 0",
                received=_received(predicate),
                action="Build the condition from resolved fields or numeric row expressions.",
            )
        if not _is_source_scope(self.node):
            _fail(
                ErrorKind.INVALID_WHERE_SCOPE,
                f"Metric.where cannot filter a composite {type(self.node).__name__}.",
                expected="a single source reduction such as amount.sum() or orders.count()",
                received=repr(self),
                action=(
                    "Apply where to each source child Metric first, then combine them, "
                    "e.g. amount.sum().where(paid) / orders.count().where(paid)."
                ),
            )
        if any(root.ref not in {item.ref for item in self.roots} for root in predicate.roots):
            _fail(
                ErrorKind.INVALID_WHERE_SCOPE,
                "Metric.where reads fields from another contribution root.",
                expected=f"a predicate on {self.roots[0].ref.path}",
                received=", ".join(root.ref.path for root in predicate.roots),
                action=(
                    "Use fields declared on the reduced Entity; a condition on another "
                    "Entity needs an explicit relationship path."
                ),
            )
        return Metric(ContributionFilter(self.node, predicate.node), self.roots, self.unit)

    def per(self, samples: SampleAxis | tuple[SampleAxis, ...]) -> MetricSamples:
        """Complete this Metric once per internal sample of the given field axes.

        ``per(field)`` is the single-axis form of ``per((field,))``; reduce the
        returned samples, e.g. ``revenue.per(customer_id).mean()``.
        """
        axes = samples if isinstance(samples, tuple) else (samples,)
        if not axes:
            _fail(
                ErrorKind.INVALID_EXPRESSION_VALUE,
                "Metric.per received no sample axes.",
                expected="a Dimension or TimeField, or a non-empty tuple of them",
                received="()",
                action="Pass at least one field axis, e.g. revenue.per(customer_id).",
            )
        bindings: list[AxisBinding] = []
        for axis in axes:
            if not isinstance(axis, (Dimension, TimeField)):
                _fail(
                    ErrorKind.INVALID_EXPRESSION_STAGE,
                    "Metric.per received a value that is not a field sample axis.",
                    expected="a resolved Dimension or TimeField, or a tuple of them",
                    received=_received(axis),
                    action=(
                        "Pass resolved field axes as one field or a tuple; Measures are "
                        "quantities, not sample axes."
                    ),
                )
            if isinstance(axis, TimeField):
                axis._require_native(operation="a per sample axis")
            bindings.append(AxisBinding(entity=axis.root, field=axis.ref, path=(), role="field"))
        if len({binding.field.ref for binding in bindings if binding.field is not None}) != len(
            bindings
        ):
            _fail(
                ErrorKind.INVALID_EXPRESSION_VALUE,
                "Metric.per received the same sample axis more than once.",
                expected="distinct field axes",
                received=", ".join(binding.field.ref.path for binding in bindings if binding.field),
                action="List each sample axis once.",
            )
        return MetricSamples(self, tuple(bindings))

    def __repr__(self) -> str:
        roots = ", ".join(root.ref.path for root in self.roots[:3])
        extra = f" +{len(self.roots) - 3}" if len(self.roots) > 3 else ""
        fingerprint = computation_fingerprint(self.node)[:12]
        return f"Metric {type(self.node).__name__} on {roots}{extra} {fingerprint} (use .show())"


@dataclass(frozen=True, slots=True, repr=False)
class DeclaredMetric(Metric):
    """A local Metric declaration over an unchanged computation graph."""

    declaration: MetricDeclaration

    def __repr__(self) -> str:
        domain = f"{self.declaration.domain.path}." if self.declaration.domain else ""
        return f"Metric {domain}{self.declaration.name} (local declaration; use .show())"


SampleAxis: TypeAlias = Dimension | TimeField


@dataclass(frozen=True, slots=True)
class MetricSamples(_ExpressionDisplay):
    """A Metric bound to internal field samples, awaiting one sample reducer.

    It is not a Metric: it cannot be observed, combined or declared until reduced.
    """

    metric: Metric
    samples: tuple[AxisBinding, ...]

    def sum(self, *, nulls: NullPolicy = "ignore") -> Metric:
        """Sum the completed per-sample values."""
        return self._reduce("sum", nulls=nulls)

    def mean(self, *, nulls: NullPolicy = "ignore") -> Metric:
        """Mean of the completed per-sample values; no valid sample yields NA."""
        return self._reduce("mean", nulls=nulls)

    def min(self, *, nulls: NullPolicy = "ignore") -> Metric:
        """Minimum of the completed per-sample values."""
        return self._reduce("min", nulls=nulls)

    def max(self, *, nulls: NullPolicy = "ignore") -> Metric:
        """Maximum of the completed per-sample values."""
        return self._reduce("max", nulls=nulls)

    def median(self, *, nulls: NullPolicy = "ignore") -> Metric:
        """Exact median of the completed per-sample values."""
        return self._reduce("median", nulls=nulls)

    def quantile(self, q: float, *, nulls: NullPolicy = "ignore") -> Metric:
        """Exact linear-interpolation quantile with finite ``0 <= q <= 1``."""
        return self._reduce("quantile", nulls=nulls, q=q)

    def count(self) -> Metric:
        """Count samples with a non-NA completed value; the result unit is ``1``."""
        return self._reduce("count")

    def count_distinct(self) -> Metric:
        """Count distinct non-NA completed values; the result unit is ``1``."""
        return self._reduce("count_distinct")

    def weighted_mean(
        self,
        *,
        weight: Metric,
        nulls: NullPolicy = "ignore",
        zero_denominator: ZeroDenominator = "null",
    ) -> Metric:
        """Weighted mean of per-sample values over common non-NA value/weight pairs."""
        if not isinstance(weight, Metric):
            _fail(
                ErrorKind.INVALID_EXPRESSION_STAGE,
                "per(...).weighted_mean weight= expects a Metric.",
                expected="a Metric completed on the same samples, e.g. orders.count()",
                received=_received(weight),
                action="Reduce the weight to a Metric first; row fields cannot weight samples.",
            )
        policy = _nulls(nulls, operation="per weighted_mean")
        zero = _zero_denominator(zero_denominator)
        try:
            node = PerWeightedMean(
                self.metric.node, weight.node, self.samples, nulls=policy, zero_denominator=zero
            )
        except DefinitionGraphError as error:
            raise SemanticExpressionError.from_definition(
                error, operation="per weighted_mean"
            ) from error
        roots = tuple(dict.fromkeys((*self.metric.roots, *weight.roots)))
        return Metric(node, roots, self.metric.unit)

    def _reduce(self, reducer: Reducer, *, nulls: object = "ignore", q: object = None) -> Metric:
        policy = _nulls(nulls, operation=f"per {reducer}")
        quantile = _quantile(q) if reducer == "quantile" else None
        try:
            node = PerReduce(self.metric.node, self.samples, reducer, q=quantile, nulls=policy)
        except DefinitionGraphError as error:
            raise SemanticExpressionError.from_definition(
                error, operation=f"per {reducer}"
            ) from error
        unit = "1" if reducer in _COUNT_REDUCERS else self.metric.unit
        return Metric(node, self.metric.roots, unit)

    def __repr__(self) -> str:
        axes = ", ".join(axis.field.ref.path for axis in self.samples if axis.field is not None)
        return f"MetricSamples {type(self.metric.node).__name__} per {axes} (use .show())"


def _is_source_scope(node: MetricNode) -> bool:
    """Whether ``node`` is one source reduction, possibly already filtered."""
    if isinstance(node, ContributionFilter):
        return _is_source_scope(node.operand)
    return isinstance(node, (SourceAggregate, EntityCount, SourceWeightedMean))


def _metric_operand(value: object, *, operation: str) -> Metric | MetricConstant:
    if isinstance(value, Metric):
        return value
    if isinstance(value, MetricSamples):
        _fail(
            ErrorKind.INVALID_EXPRESSION_STAGE,
            f"{operation} received per-sample values that are not reduced yet.",
            expected="a Metric or finite numeric constant",
            received=repr(value),
            action="Reduce the samples first, e.g. revenue.per(customer_id).mean(), then combine.",
        )
    if isinstance(value, (_Countable, Entity, Relationship, Predicate)):
        _fail(
            ErrorKind.INVALID_EXPRESSION_STAGE,
            f"{operation} mixes a {type(value).__name__} with a completed Metric.",
            expected="a Metric or finite numeric constant",
            received=repr(value),
            action="Reduce row values to a Metric first, e.g. amount.sum(), then combine Metrics.",
        )
    literal = _numeric_literal(
        value, operation=operation, expected="a Metric or finite int, float, or Decimal"
    )
    try:
        return MetricConstant(literal)
    except DefinitionGraphError as error:
        raise SemanticExpressionError.from_definition(error, operation=operation) from error


def _metric_combine(
    op: RowOperator,
    left: Metric | MetricConstant,
    right: Metric | MetricConstant,
    *,
    zero_denominator: ZeroDenominator = "null",
) -> Metric:
    """Combine completed Metrics per coordinate; roots may differ, operand order is kept."""
    constant: Literal["left", "right"] | None = (
        "left"
        if isinstance(left, MetricConstant)
        else "right"
        if isinstance(right, MetricConstant)
        else None
    )
    unit = _arith_unit(
        op,
        left.unit if isinstance(left, Metric) else None,
        right.unit if isinstance(right, Metric) else None,
        constant=constant,
        stage="Metric",
    )
    left_node = left.node if isinstance(left, Metric) else left
    right_node = right.node if isinstance(right, Metric) else right
    node: MetricNode
    match op:
        case "add":
            node = MetricAdd(left_node, right_node)
        case "subtract":
            node = MetricSubtract(left_node, right_node)
        case "multiply":
            node = MetricMultiply(left_node, right_node)
        case "divide":
            node = MetricDivide(left_node, right_node, zero_denominator=zero_denominator)
    metrics = [operand for operand in (left, right) if isinstance(operand, Metric)]
    roots = tuple(dict.fromkeys(root for metric in metrics for root in metric.roots))
    return Metric(node, roots, unit)


_COUNT_REDUCERS: frozenset[Reducer] = frozenset({"count", "count_distinct"})


def _nulls(value: object, *, operation: str) -> NullPolicy:
    if value == "ignore":
        return "ignore"
    if value == "error":
        return "error"
    _fail(
        ErrorKind.INVALID_EXPRESSION_VALUE,
        f"{operation} received an unsupported nulls policy.",
        expected='nulls="ignore" or nulls="error"',
        received=_received(value),
        action='Use "ignore" (default) or "error" for this reduction.',
    )


def _quantile(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(
            ErrorKind.INVALID_EXPRESSION_VALUE,
            "quantile received a q that is not a number.",
            expected="a finite float q with 0 <= q <= 1",
            received=_received(value),
            action="Pass q such as 0.95.",
        )
    if isinstance(value, int) and not 0 <= value <= 1:
        # Range-check integers before float conversion, which overflows for huge values.
        _fail(
            ErrorKind.INVALID_EXPRESSION_VALUE,
            "quantile received a q outside [0, 1].",
            expected="a finite float q with 0 <= q <= 1",
            received=_received(value),
            action="Pass q such as 0.95.",
        )
    return float(value)


def _time_binding(
    time: object, root: ExactRef, default: ExactRef | None, *, operation: str
) -> TimeBinding | None:
    """Explicit time on the contribution root, else the root's unique default time role."""
    if time is None:
        return TimeBinding(default) if default is not None else None
    if not isinstance(time, TimeField):
        _fail(
            ErrorKind.INVALID_EXPRESSION_VALUE,
            f"{operation} time= expects a resolved time field.",
            expected="a TimeField resolved from the Catalog",
            received=_received(time),
            action="Pass a TimeField such as the resolved paid_at field; a bare Ref is only a lookup key.",
        )
    if time.path:
        start = time.path[0].from_entity
        if start.ref != root.ref:
            _fail(
                ErrorKind.INVALID_TIME_PATH,
                f"{operation} time= path starts at another Entity than the contribution root.",
                expected=f"a path starting at {root.ref.path}",
                received=f"{time.path[0].ref.ref.path} from {start.ref.path}",
                action="Start the via path at the reduced Entity's relationship.",
            )
        return TimeBinding(time.ref, tuple(step.ref for step in time.path))
    if time.root.ref != root.ref:
        _fail(
            ErrorKind.INVALID_TIME_PATH,
            f"{operation} time= reads a time field from another Entity without a path.",
            expected=f"a time field on {root.ref.path}",
            received=f"{time.ref.ref.path} on {time.root.ref.path}",
            action=(
                "Use a time field of the contribution root, or bind the cross-Entity "
                "time field with via((relationship, ...))."
            ),
        )
    return TimeBinding(time.ref)


def _reduce(
    source: _Source,
    reducer: Reducer,
    *,
    time: object,
    nulls: object = "ignore",
    q: object = None,
) -> Metric:
    binding = _time_binding(time, source.root, source.default_time, operation=reducer)
    policy = _nulls(nulls, operation=reducer)
    quantile = _quantile(q) if reducer == "quantile" else None
    try:
        node = SourceAggregate(source.node, reducer, binding, q=quantile, nulls=policy)
    except DefinitionGraphError as error:
        raise SemanticExpressionError.from_definition(error, operation=reducer) from error
    unit = "1" if reducer in _COUNT_REDUCERS else source.unit
    return Metric(node, (source.root,), unit)


_SUPPORTED_KINDS = (
    SemanticKind.ENTITY,
    SemanticKind.DIMENSION,
    SemanticKind.TIME_DIMENSION,
    SemanticKind.MEASURE,
    SemanticKind.RELATIONSHIP,
)


@dataclass(frozen=True, slots=True)
class FinalizedDeclarations:
    """Validated, immutable physical definitions; resolving never reads source data."""

    _state: CompiledSemanticState = field(repr=False)

    def __repr__(self) -> str:
        return f"FinalizedDeclarations {self._state.definition_fingerprint[:26]} (use .show())"

    def show(self) -> None:
        """Print the frozen definition count and available operation.

        Returns: None.
        Example: ``finalized.show()``.
        Constraints: No Catalog Metric publication or execution is implied.
        """
        print(repr(self))
        print(f"  Physical definitions: {len(self._state.definitions)}")
        print("  Resolve: finalized.resolve(captured_ref)")

    @overload
    def resolve(self, target: Ref[EntityKind]) -> Entity: ...
    @overload
    def resolve(self, target: Ref[DimensionKind]) -> Dimension: ...
    @overload
    def resolve(self, target: Ref[TimeDimensionKind]) -> TimeField: ...
    @overload
    def resolve(self, target: Ref[MeasureKind]) -> Measure: ...
    @overload
    def resolve(self, target: Ref[RelationshipKind]) -> Relationship: ...
    @overload
    def resolve(self, target: Ref[SemanticKindTag]) -> ResolvedValue: ...
    def resolve(self, target: Ref[SemanticKindTag]) -> ResolvedValue:
        """Resolve a captured lookup key into a closed immutable expression value.

        Args: target: An Entity, Dimension, TimeField, Measure or Relationship Ref.
        Returns: The corresponding immutable value with complete scope facts.
        Example: ``amount = finalized.resolve(amount_ref)``.
        Constraints: Only this frozen scope is read; bare Refs cannot be composed.
        """
        return resolve(self, target)

    def require(self, target: Ref[SemanticKindTag]) -> None:
        if target not in self._state.definitions:
            raise SemanticExpressionError(
                kind=ErrorKind.NOT_FOUND,
                message=f"Ref {target.key!r} is not among the finalized declarations.",
                expected=f"a captured {target.kind.value} Ref in this finalized scope",
                received=target.key,
                action="Capture this declaration and its dependencies before ms.finalize(), then resolve its Ref from that result.",
                refs=(target.key,),
            )


def declaration_state(
    contexts: Sequence[LoaderContext], *, datasource_irs: tuple[DatasourceIR, ...]
) -> FinalizedDeclarations:
    """Validate and close a physical capture scope using the existing loader owners."""
    from marivo.semantic._compiled_state import build_compiled_state
    from marivo.semantic.loader import _build_registry, _semantic_duplicate_errors
    from marivo.semantic.validator import assembly_validate

    registry, sidecar, build_errors = _build_registry(list(contexts), datasource_irs=datasource_irs)
    validation_errors, _ = assembly_validate(registry, sidecar)
    errors = (*build_errors, *_semantic_duplicate_errors(contexts), *validation_errors)
    if errors:
        error = errors[0]
        raise SemanticExpressionError(
            kind=ErrorKind(error.kind),
            message="Physical declarations cannot be finalized: " + error.message,
            expected=error.expected or "a complete, valid physical dependency closure",
            received=error.received
            or f"{error.kind}: {error.message}; refs={error.semantic_refs!r}",
            action=(error.repair.action if error.repair is not None else str(error))
            + " Complete or repair the referenced declarations before calling ms.finalize() again.",
            refs=error.semantic_refs,
        ) from error
    result = FinalizedDeclarations(
        build_compiled_state(
            registry=registry,
            sidecar=sidecar,
            selected_root_roles=(),
            filtered_domains=(),
        )
    )
    for ctx in contexts:
        if ctx.finalized is None:
            ctx.finalized = result
    return result


def finalize() -> FinalizedDeclarations:
    """Validate and freeze all physical declarations captured in this loader scope.

    Returns: A FinalizedDeclarations result whose resolve(ref) yields exact values.
    Example:
        >>> amount_ref = ms.measure_column(name="amount", entity=orders_ref,
        ...     column="amount", additivity=ms.additive_all())
        >>> finalized = ms.finalize()
        >>> amount = finalized.resolve(amount_ref)
    Constraints: Capture every physical dependency first. Finalization performs no
        source reads, closes capture, and does not publish local Measure/Metric graphs.
    """
    from marivo.semantic._authoring_context import _require_ctx

    ctx = _require_ctx()
    if ctx.finalized is not None:
        return ctx.finalized
    return declaration_state((*ctx.prior_contexts, ctx), datasource_irs=ctx.datasource_irs)


class _Resolver:
    """Definition identity over one compiled or declared semantic state."""

    def __init__(self, state: CompiledSemanticState) -> None:
        self._state = state
        self._registry: Registry = state.registry

    def exact(self, target: Ref[SemanticKindTag]) -> ExactRef:
        from marivo.semantic._definition_identity import scoped_definition_fingerprint

        version = scoped_definition_fingerprint(
            root=target,
            definitions=self._state.definitions,
            dependencies=self._state.dependencies,
            sidecar=self._state.sidecar,
        )
        return ExactRef(target, version)

    def entity_ref(self, path: str) -> ExactRef:
        return self.exact(ref_factory.entity(path))

    def coordinate(self, path: str) -> ExactRef:
        field = self._registry.dimensions.get(path)
        if field is None:
            _fail(
                ErrorKind.INVALID_EXPRESSION_VALUE,
                "The expression references an undeclared coordinate.",
                expected="an existing Dimension or TimeField declared before its consumer",
                received=path,
                action="Declare the coordinate first; available coordinates: "
                + ", ".join(sorted(self._registry.dimensions)[:5]),
            )
        if field.is_time_dimension:
            return self.exact(ref_factory.time_dimension(path))
        return self.exact(ref_factory.dimension(path))

    def default_time(self, entity_path: str) -> ExactRef | None:
        """The Entity's unique declared default time field; none when absent."""
        defaults = sorted(
            key
            for key, field in self._registry.dimensions.items()
            if field.entity == entity_path and field.is_time_dimension and field.is_default
        )
        return self.coordinate(defaults[0]) if len(defaults) == 1 else None

    def entity(self, path: str) -> Entity:
        return Entity(
            ref=self.entity_ref(path),
            primary_key=self._registry.entities[path].primary_key,
            default_time=self.default_time(path),
        )

    def permission(self, path: str) -> SumPermission:
        measure = self._registry.measures[path]
        policy = measure.dsl_additivity

        def native_axis(path: str) -> ExactRef:
            coordinate = self.coordinate(path)
            owner = self._registry.dimensions[path].entity
            if owner != measure.entity:
                _fail(
                    ErrorKind.INVALID_EXPRESSION_VALUE,
                    "Measure sum permission references another contribution root.",
                    expected=f"coordinates on {measure.entity}",
                    received=f"{path} on {owner}",
                    action="Choose an existing coordinate on the Measure's Entity; do not infer a cross-Entity permission.",
                )
            return coordinate

        if isinstance(policy, AdditiveOverV1):
            return SumAxes(tuple(native_axis(axis) for axis in policy.coordinates))
        if isinstance(policy, AdditiveAllV1):
            if not policy.exceptions:
                return SumAll()
            return SumAllExcept(tuple(native_axis(axis) for axis in policy.exceptions))
        if isinstance(policy, NonAdditiveV1):
            return SumNone()
        additivity = measure.additivity
        if isinstance(additivity, SemiAdditive):
            return SumAllExcept((native_axis(additivity.over),))
        return SumAll() if additivity == "additive" else SumNone()

    def measure(self, path: str) -> Measure:
        measure = self._registry.measures[path]
        return Measure(
            ref=self.exact(ref_factory.measure(path)),
            root=self.entity_ref(measure.entity),
            unit=measure.unit,
            permission=self.permission(path),
            default_time=self.default_time(measure.entity),
            coordinates=tuple(
                self.coordinate(path)
                for path, field in sorted(self._registry.dimensions.items())
                if field.entity == measure.entity
            ),
        )

    def field(self, path: str) -> Dimension | TimeField:
        field = self._registry.dimensions[path]
        root = self.entity_ref(field.entity)
        default = self.default_time(field.entity)
        if field.is_time_dimension:
            return TimeField(ref=self.coordinate(path), root=root, default_time=default)
        return Dimension(ref=self.coordinate(path), root=root, default_time=default)

    def relationship(self, path: str) -> Relationship:
        contract: TargetRelationshipContract = normalize_target_relationship(self._registry, path)
        return Relationship(
            ref=self.exact(ref_factory.relationship(path)),
            from_entity=self.entity_ref(contract.from_entity_ref.path),
            to_entity=self.entity_ref(contract.to_entity_ref.path),
            cardinality=contract.cardinality,
        )


@overload
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[EntityKind]
) -> Entity: ...
@overload
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[DimensionKind]
) -> Dimension: ...
@overload
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[TimeDimensionKind]
) -> TimeField: ...
@overload
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[MeasureKind]
) -> Measure: ...
@overload
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[RelationshipKind]
) -> Relationship: ...
@overload
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[SemanticKindTag]
) -> ResolvedValue: ...
def resolve(
    catalog: SemanticCatalog | FinalizedDeclarations, target: Ref[SemanticKindTag]
) -> ResolvedValue:
    """Resolve one exact ref in ``catalog`` into its immutable expression value.

    ``catalog`` is a loaded Catalog or finalized declarations; both share one
    exact-version function. Unsupported kinds fail with a structured error;
    absent refs fail through the source's own lookup error. No datasource is read.
    """
    if type(target) is not Ref:
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            message="Expression resolution requires a semantic Ref lookup key.",
            expected="Ref from ms.ref.entity/dimension/time_dimension/measure/relationship",
            received=type(target).__name__,
            action="Pass a typed ref such as ms.ref.measure('sales.orders.amount').",
        )
    if target.kind not in _SUPPORTED_KINDS:
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_EXPRESSION_STAGE,
            message=f"Ref {target.key!r} cannot be resolved into an expression value here.",
            expected="entity, dimension, time_dimension, measure, or relationship ref",
            received=target.kind.value,
            action=(
                "Resolve a physical field, Entity or Relationship here. Local Metric "
                "declarations are already constructed; Catalog Metric publication is separate."
            ),
            refs=(target.key,),
        )
    catalog.require(target)
    resolver = _Resolver(catalog._state)
    path = target.path
    match target.kind:
        case SemanticKind.ENTITY:
            return resolver.entity(path)
        case SemanticKind.RELATIONSHIP:
            return resolver.relationship(path)
        case SemanticKind.MEASURE:
            return resolver.measure(path)
        case _:
            return resolver.field(path)


def _show_expression(value: object) -> None:
    """Render a deterministic, bounded construction card, separate from Catalog discovery."""
    lines = [repr(value)[:240]]
    if isinstance(value, Entity):
        lines.extend(
            ("  Stage: Entity contribution root", f"  Identity columns: {value.primary_key!r}")
        )
    elif isinstance(value, (Dimension, TimeField)):
        lines.extend((f"  Stage: {type(value).__name__} field", f"  Root: {value.root.ref.path}"))
    elif isinstance(value, (Measure, NumericRowExpr)):
        lines.extend(
            (
                "  Stage: numeric row",
                f"  Root: {value.root.ref.path}",
                f"  Unit: {value.unit!r}",
                f"  Sum permission: {value.permission!r}",
            )
        )
    elif isinstance(value, Metric):
        lines.extend(
            (
                "  Stage: completed Metric",
                f"  Roots: {', '.join(root.ref.path for root in value.roots[:5])}",
                f"  Unit: {value.unit!r}",
                f"  Computation: {computation_fingerprint(value.node)}",
            )
        )
    elif isinstance(value, MetricSamples):
        lines.append("  Stage: internal samples; choose a sample reducer to produce a Metric.")
    elif isinstance(value, Predicate):
        lines.append("  Stage: contribution predicate; use on a single source Metric.where(...).")
    elif isinstance(value, Relationship):
        lines.extend(
            (
                f"  From: {value.from_entity.ref.path}",
                f"  To: {value.to_entity.ref.path}",
                f"  Cardinality: {value.cardinality}",
            )
        )
    if isinstance(value, (DeclaredMeasure, DeclaredMetric)):
        lines.extend(
            (
                f"  Local declaration: {value.declaration.name}",
                f"  Site: {value.declaration.location!r}",
                f"  Business context: {value.declaration.ai_context!r}",
            )
        )
    lines.extend(
        (
            "  Evidence: definition construction only; no source read or execution.",
            "  Catalog Metric publication is not part of local declaration construction.",
            '  Help: marivo.help("semantic.expressions")',
        )
    )
    print("\n".join(line[:300] for line in lines[:18]))
