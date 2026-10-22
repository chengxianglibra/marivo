"""Closed Row and Metric definition nodes plus declaration records.

Row and Metric arithmetic use distinct stage types. Operator policies are
resolved at construction and stored with the rule version on the node that
owns them; declarations add names and metadata without changing computation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from ibis.common.deferred import Resolver

from marivo._definition.types import (
    NUMERIC_RULE_VERSION,
    RULE_VERSION,
    Authorization,
    AxisBinding,
    ExactRef,
    FieldRead,
    NullPolicy,
    SourceLocation,
    SumPermission,
    invalid,
)
from marivo._definition.windows import ObservationWindow
from marivo._temporal import InstantAnchor
from marivo.refs import DomainKind, Ref, SemanticKind, _validate_segment

if TYPE_CHECKING:
    from marivo._definition.relations import (
        LiteralPopulation,
        PopulationContains,
        PopulationProgram,
        SampleDomain,
    )
    from marivo.datasource.typing import AiContextValue

_FIELD_KINDS = frozenset(
    {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION, SemanticKind.MEASURE}
)

Reducer: TypeAlias = Literal[
    "sum",
    "mean",
    "min",
    "max",
    "median",
    "quantile",
    "count",
    "count_distinct",
    "approx_nunique",
    "approx_median",
    "approx_quantile",
    "first",
    "last",
]
CompareOp: TypeAlias = Literal["eq", "ne", "lt", "le", "gt", "ge"]


def _check_quantile(reducer: Reducer, q: float | None) -> None:
    if reducer in ("quantile", "approx_quantile"):
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
    read: FieldRead | None = None

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


@dataclass(frozen=True, slots=True)
class RowExpression:
    """An Ibis numeric or predicate recipe over ordered semantic input slots.

    ``slot_0``, ``slot_1``, ... bind the corresponding inputs. The recipe is
    Ibis's own Resolver structure; this record introduces no numeric opcode.
    Physical values and types are supplied only during execution preparation.
    """

    recipe: Resolver
    inputs: tuple[RowField | MeasureAssertion, ...]


RowNode: TypeAlias = RowField | MeasureAssertion | RowExpression


@dataclass(frozen=True, slots=True)
class TimeBinding:
    """Business time role of a source reduction: a time field and its single-valued path.

    An empty path reads a time field native to the contribution root; a non-empty
    path is the ordered relationship chain from the root to the field's Entity.
    """

    field: ExactRef
    path: tuple[ExactRef, ...] = ()
    at: InstantAnchor | None = None

    def __post_init__(self) -> None:
        if self.field.ref.kind is not SemanticKind.TIME_DIMENSION:
            invalid(
                f"time binding expected a time_dimension reference; received "
                f"{self.field.ref.key}. Pass a declared time field."
            )
        for step in self.path:
            if step.ref.kind is not SemanticKind.RELATIONSHIP:
                invalid(
                    f"time path on {self.field.ref.key} expected relationship references; "
                    f"received {step.ref.key}. Pass declared relationships in path order."
                )


# Source predicates -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PredicateExpression:
    """Native boolean recipe over inert field subtrees and membership leaves."""

    recipe: Resolver
    inputs: tuple[RowExpression | PopulationContains, ...]


# Metric stage ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceAggregate:
    """Reduction of one row expression over its contribution root."""

    operand: RowNode
    reducer: Reducer
    time: TimeBinding | None
    q: float | None = None
    nulls: NullPolicy = "ignore"
    rule_version: int = RULE_VERSION

    def __post_init__(self) -> None:
        _check_quantile(self.reducer, self.q)


@dataclass(frozen=True, slots=True)
class EntityCount:
    entity: ExactRef
    time: TimeBinding | None


@dataclass(frozen=True, slots=True)
class SourceWeightedMean:
    """Weighted mean over common non-NA value/weight pairs of one contribution root."""

    value: RowNode
    weight: RowNode
    time: TimeBinding | None
    nulls: NullPolicy = "ignore"
    rule_version: int = NUMERIC_RULE_VERSION


@dataclass(frozen=True, slots=True)
class ContributionFilter:
    """Contribution scope restricted by one captured source predicate."""

    operand: MetricNode
    predicate: PredicateExpression


@dataclass(frozen=True, slots=True)
class PerReduce:
    """Complete the child metric per internal sample, then reduce the samples."""

    operand: MetricNode
    samples: tuple[AxisBinding, ...] | SampleDomain | LiteralPopulation | PopulationProgram
    reducer: Reducer
    q: float | None = None
    nulls: NullPolicy = "ignore"
    rule_version: int = RULE_VERSION
    order_by: ExactRef | None = None
    include_null: bool = False

    def __post_init__(self) -> None:
        _check_quantile(self.reducer, self.q)
        if self.samples == ():
            invalid("per expected at least one field axis; received none. Pass a field or tuple.")


@dataclass(frozen=True, slots=True)
class PerWeightedMean:
    value: MetricNode
    weight: MetricNode
    samples: tuple[AxisBinding, ...] | SampleDomain | LiteralPopulation | PopulationProgram
    nulls: NullPolicy = "ignore"
    rule_version: int = NUMERIC_RULE_VERSION

    def __post_init__(self) -> None:
        if self.samples == ():
            invalid("per expected at least one field axis; received none. Pass a field or tuple.")


@dataclass(frozen=True, slots=True)
class MemberCount:
    """Count complete distinct coordinate tuples in one explicitly captured domain."""

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


@dataclass(frozen=True, slots=True)
class MetricExpression:
    """An Ibis recipe over completed Metric slots at the same output coordinate."""

    recipe: Resolver
    operands: tuple[MetricNode, ...]


@dataclass(frozen=True, slots=True)
class MetricWindow:
    """Reevaluate the entire operand in the rule bound to each output endpoint."""

    operand: MetricNode
    rule: ObservationWindow


MetricNode: TypeAlias = (
    SourceAggregate
    | EntityCount
    | SourceWeightedMean
    | ContributionFilter
    | PerReduce
    | PerWeightedMean
    | MemberCount
    | DeclaredMetricRef
    | MetricExpression
    | MetricWindow
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
