"""Closed Row and Metric definition nodes plus declaration records.

Row and Metric arithmetic use distinct stage types. Operator policies are
resolved at construction and stored with the rule version on the node that
owns them; declarations add names and metadata without changing computation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from marivo._definition.types import (
    RULE_VERSION,
    Authorization,
    AxisBinding,
    ExactRef,
    FiniteNumber,
    LiteralValue,
    NullPolicy,
    SourceLocation,
    SumPermission,
    ZeroDenominator,
    check_finite_number,
    check_literal,
    invalid,
)
from marivo.refs import DomainKind, Ref, SemanticKind, _validate_segment

if TYPE_CHECKING:
    from marivo._definition.relations import LiteralPopulation, PopulationProgram
    from marivo.datasource.typing import AiContextValue

_FIELD_KINDS = frozenset(
    {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION, SemanticKind.MEASURE}
)

Reducer: TypeAlias = Literal[
    "sum", "mean", "min", "max", "median", "quantile", "count", "count_distinct"
]
CompareOp: TypeAlias = Literal["eq", "ne", "lt", "le", "gt", "ge"]


def _check_quantile(reducer: Reducer, q: float | None) -> None:
    if reducer == "quantile":
        if q is None or isinstance(q, bool) or not 0.0 <= q <= 1.0:
            invalid(f"quantile expected q in [0, 1]; received {q!r}. Pass q such as 0.95.")
    elif q is not None:
        invalid(f"{reducer} does not accept q; received {q!r}. Remove q or use quantile.")


# Row stage ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RowField:
    """Declared field on one contribution root, with its inherited unit and sum permission."""

    ref: ExactRef
    root: ExactRef
    unit: str | None
    permission: SumPermission

    def __post_init__(self) -> None:
        if self.ref.ref.kind not in _FIELD_KINDS:
            invalid(
                f"row field expected a dimension, time_dimension, or measure reference; "
                f"received {self.ref.ref.key}. Reference a declared field."
            )
        if self.root.ref.kind is not SemanticKind.ENTITY:
            invalid(
                f"row field {self.ref.ref.key} expected an entity contribution root; "
                f"received {self.root.ref.key}. Bind the field to its owning entity."
            )


@dataclass(frozen=True, slots=True)
class RowConstant:
    """Finite numeric row constant; it never creates a contribution source."""

    value: FiniteNumber

    def __post_init__(self) -> None:
        check_finite_number(self.value, role="row constant")


@dataclass(frozen=True, slots=True)
class RowNegate:
    operand: RowNode


@dataclass(frozen=True, slots=True)
class RowAdd:
    left: RowNode
    right: RowNode


@dataclass(frozen=True, slots=True)
class RowSubtract:
    left: RowNode
    right: RowNode


@dataclass(frozen=True, slots=True)
class RowMultiply:
    left: RowNode
    right: RowNode


@dataclass(frozen=True, slots=True)
class RowDivide:
    numerator: RowNode
    denominator: RowNode
    zero_denominator: ZeroDenominator = "null"
    rule_version: int = RULE_VERSION


@dataclass(frozen=True, slots=True)
class MeasureAssertion:
    """Explicit new unit or sum permission declared on a row expression.

    ``None`` keeps the inherited fact; only asserted values enter the
    computation fingerprint, together with their authorization source.
    """

    operand: RowNode
    unit: str | None
    permission: SumPermission | None
    authorization: Authorization

    def __post_init__(self) -> None:
        if self.unit is None and self.permission is None:
            invalid(
                "measure assertion expected a unit or sum permission; received neither. "
                "Omit the assertion when only naming the expression."
            )


RowNode: TypeAlias = (
    RowField
    | RowConstant
    | RowNegate
    | RowAdd
    | RowSubtract
    | RowMultiply
    | RowDivide
    | MeasureAssertion
)


# Field predicates --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldCompare:
    field: ExactRef
    op: CompareOp
    value: LiteralValue

    def __post_init__(self) -> None:
        check_literal(self.value, role=f"predicate on {self.field.ref.key}")


@dataclass(frozen=True, slots=True)
class FieldIn:
    field: ExactRef
    values: tuple[LiteralValue, ...]

    def __post_init__(self) -> None:
        for value in self.values:
            check_literal(value, role=f"predicate on {self.field.ref.key}")


@dataclass(frozen=True, slots=True)
class FieldIsNull:
    field: ExactRef


@dataclass(frozen=True, slots=True)
class PredAnd:
    operands: tuple[FieldPredicate, ...]


@dataclass(frozen=True, slots=True)
class PredOr:
    operands: tuple[FieldPredicate, ...]


@dataclass(frozen=True, slots=True)
class PredNot:
    operand: FieldPredicate


FieldPredicate: TypeAlias = FieldCompare | FieldIn | FieldIsNull | PredAnd | PredOr | PredNot


# Metric stage ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceAggregate:
    """Reduction of one row expression over its contribution root."""

    operand: RowNode
    reducer: Reducer
    time: ExactRef | None
    q: float | None = None
    nulls: NullPolicy = "ignore"
    rule_version: int = RULE_VERSION

    def __post_init__(self) -> None:
        _check_quantile(self.reducer, self.q)


@dataclass(frozen=True, slots=True)
class EntityCount:
    entity: ExactRef
    time: ExactRef | None


@dataclass(frozen=True, slots=True)
class SourceWeightedMean:
    """Weighted mean over common non-NA value/weight pairs of one contribution root."""

    value: RowNode
    weight: RowNode
    time: ExactRef | None
    nulls: NullPolicy = "ignore"
    zero_denominator: ZeroDenominator = "null"
    rule_version: int = RULE_VERSION


@dataclass(frozen=True, slots=True)
class MetricConstant:
    value: FiniteNumber

    def __post_init__(self) -> None:
        check_finite_number(self.value, role="metric constant")


@dataclass(frozen=True, slots=True)
class MetricNegate:
    operand: MetricNode


@dataclass(frozen=True, slots=True)
class MetricAdd:
    left: MetricNode
    right: MetricNode


@dataclass(frozen=True, slots=True)
class MetricSubtract:
    left: MetricNode
    right: MetricNode


@dataclass(frozen=True, slots=True)
class MetricMultiply:
    left: MetricNode
    right: MetricNode


@dataclass(frozen=True, slots=True)
class MetricDivide:
    numerator: MetricNode
    denominator: MetricNode
    zero_denominator: ZeroDenominator = "null"
    rule_version: int = RULE_VERSION


@dataclass(frozen=True, slots=True)
class ContributionFilter:
    """Contribution scope restricted by a field predicate or an explicit Population."""

    operand: MetricNode
    predicate: FieldPredicate | LiteralPopulation | PopulationProgram


@dataclass(frozen=True, slots=True)
class PerReduce:
    """Complete the child metric per internal sample, then reduce the samples."""

    operand: MetricNode
    samples: tuple[AxisBinding, ...] | LiteralPopulation | PopulationProgram
    reducer: Reducer
    q: float | None = None
    nulls: NullPolicy = "ignore"
    rule_version: int = RULE_VERSION

    def __post_init__(self) -> None:
        _check_quantile(self.reducer, self.q)
        if self.samples == ():
            invalid("per expected at least one field axis; received none. Pass a field or tuple.")


@dataclass(frozen=True, slots=True)
class PerWeightedMean:
    value: MetricNode
    weight: MetricNode
    samples: tuple[AxisBinding, ...] | LiteralPopulation | PopulationProgram
    nulls: NullPolicy = "ignore"
    zero_denominator: ZeroDenominator = "null"
    rule_version: int = RULE_VERSION

    def __post_init__(self) -> None:
        if self.samples == ():
            invalid("per expected at least one field axis; received none. Pass a field or tuple.")


@dataclass(frozen=True, slots=True)
class MemberCount:
    population: LiteralPopulation | PopulationProgram


@dataclass(frozen=True, slots=True)
class DeclaredMetricRef:
    """Dependency on a published Metric declaration, expanded by the caller's resolver."""

    ref: ExactRef

    def __post_init__(self) -> None:
        if self.ref.ref.kind is not SemanticKind.METRIC:
            invalid(
                f"declared metric dependency expected a metric reference; received "
                f"{self.ref.ref.key}. Reference a published metric."
            )


MetricNode: TypeAlias = (
    SourceAggregate
    | EntityCount
    | SourceWeightedMean
    | MetricConstant
    | MetricNegate
    | MetricAdd
    | MetricSubtract
    | MetricMultiply
    | MetricDivide
    | ContributionFilter
    | PerReduce
    | PerWeightedMean
    | MemberCount
    | DeclaredMetricRef
)


# Declarations ------------------------------------------------------------


def _check_name(name: str) -> None:
    try:
        _validate_segment(name, role="declaration name")
    except ValueError as exc:
        invalid(str(exc))


@dataclass(frozen=True, slots=True)
class MeasureDeclaration:
    """Local Measure name and metadata; the row definition carries any assertion."""

    name: str
    definition: RowNode
    ai_context: AiContextValue | None = None
    location: SourceLocation | None = None

    def __post_init__(self) -> None:
        _check_name(self.name)


@dataclass(frozen=True, slots=True)
class MetricDeclaration:
    """Local Metric name, optional domain, and metadata over an unchanged definition."""

    name: str
    definition: MetricNode
    domain: Ref[DomainKind] | None = None
    ai_context: AiContextValue | None = None
    location: SourceLocation | None = None

    def __post_init__(self) -> None:
        _check_name(self.name)
