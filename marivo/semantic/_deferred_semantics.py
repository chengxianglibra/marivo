"""Source-free semantic consumption of native Ibis Deferred recipes.

The numerical structure remains an Ibis resolver recipe. This module adds only
semantic boundaries and facts; binding supplies physical types later.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias, cast

from ibis.common.deferred import (
    Attr,
    BinaryOperator,
    Call,
    Deferred,
    Just,
    Resolver,
    Sequence,
    UnaryOperator,
    Variable,
)

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.deferred import (
    DeclarationResolver,
    DeferredInspection,
    PopulationContainsResolver,
    PopulationCountResolver,
    RelationshipParameter,
    SemanticInput,
    SemanticResolver,
    attribute_name,
    inspect_deferred,
)
from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MeasureAssertion,
    MemberCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    PredicateExpression,
    Reducer,
    RowExpression,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
    TimeBinding,
)
from marivo._definition.grouping import normalize_groupings
from marivo._definition.relations import (
    LiteralPopulation,
    MembershipBinding,
    PopulationContains,
    PopulationParameter,
    PopulationProgram,
    SampleDomain,
)
from marivo._definition.types import (
    Authorization,
    AxisBinding,
    DefinitionGraphError,
    ExactRef,
    FieldRead,
    SumNone,
    SumPermission,
)
from marivo._grouping import Grouping
from marivo.refs import SemanticKind
from marivo.refs import ref as ref_factory
from marivo.semantic.errors import ErrorKind, SemanticExpressionError

if TYPE_CHECKING:
    from marivo.semantic._compiled_state import CompiledSemanticState


@dataclass(frozen=True, slots=True)
class RowFacts:
    node: RowNode
    root: ExactRef
    unit: str | None
    permission: SumPermission
    default_time: ExactRef | None
    coordinates: tuple[ExactRef, ...] = ()
    numeric: bool = True
    boolean: bool = False
    field: SemanticInput | None = None


@dataclass(frozen=True, slots=True)
class MetricFacts:
    node: MetricNode
    roots: tuple[ExactRef, ...]
    unit: str | None
    _declared: tuple[tuple[ExactRef, MetricNode], ...] = ()
    boolean: bool = False


@dataclass(frozen=True, slots=True)
class PredicateFacts:
    node: PredicateExpression
    roots: tuple[ExactRef, ...]


@dataclass(frozen=True, slots=True)
class _Samples:
    metric: MetricFacts
    axes: tuple[AxisBinding, ...] | LiteralPopulation | PopulationProgram | SampleDomain


@dataclass(frozen=True, slots=True)
class _Scalar:
    recipe: Resolver
    value: object


_Facts: TypeAlias = RowFacts | MetricFacts | _Samples | _Scalar
_REDUCERS = frozenset(
    {
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
    }
)


def _fail(
    message: str,
    *,
    expected: str,
    received: str,
    kind: ErrorKind = ErrorKind.INVALID_EXPRESSION_STAGE,
    action: str | None = None,
    help_target: str = "expressions",
) -> NoReturn:
    raise SemanticExpressionError(
        kind=kind,
        message=message,
        expected=expected,
        received=received,
        help_target=help_target,
        action=action
        or (
            "Declare a Measure with an explicit additivity grant before summing this expression."
            if kind is ErrorKind.MISSING_SUM_PERMISSION
            else "Use resolved semantic fields at the same stage and reduce rows before combining Metrics."
        ),
    )


def _rename_slots(recipe: Resolver, offset: int) -> Resolver:
    if isinstance(recipe, Variable):
        name = str(recipe.name)
        return Variable(f"slot_{int(name.removeprefix('slot_')) + offset}")
    if isinstance(recipe, BinaryOperator):
        return BinaryOperator(
            recipe.func, _rename_slots(recipe.left, offset), _rename_slots(recipe.right, offset)
        )
    if isinstance(recipe, UnaryOperator):
        return UnaryOperator(recipe.func, _rename_slots(recipe.arg, offset))
    if isinstance(recipe, Call):
        return Call(
            _rename_slots(recipe.func, offset),
            *(_rename_slots(arg, offset) for arg in recipe.args),
            **{key: _rename_slots(arg, offset) for key, arg in recipe.kwargs.items()},
        )
    if isinstance(recipe, Attr):
        return Attr(_rename_slots(recipe.obj, offset), recipe.name)
    if isinstance(recipe, Sequence):
        return Sequence(tuple(_rename_slots(item, offset) for item in recipe.values))
    return recipe


def _row_recipe(value: RowFacts, inputs: list[RowField | MeasureAssertion]) -> Resolver:
    if isinstance(value.node, RowExpression):
        recipe = _rename_slots(value.node.recipe, len(inputs))
        inputs.extend(value.node.inputs)
        return recipe
    if isinstance(value.node, (RowField, MeasureAssertion)):
        recipe = Variable(f"slot_{len(inputs)}")
        inputs.append(value.node)
        return recipe
    raise AssertionError("native row consumption produced an obsolete numeric node")


def _compose(
    recipe: Resolver,
    children: tuple[_Facts, ...],
    rebuild: Callable[[tuple[Resolver, ...]], Resolver],
    *,
    operation: str,
) -> _Facts:
    construct = rebuild
    rows = tuple(item for item in children if isinstance(item, RowFacts))
    metrics = tuple(item for item in children if isinstance(item, MetricFacts))
    if any(isinstance(item, _Samples) for item in children) or (rows and metrics):
        _fail(
            "Expression combines incompatible stages.",
            expected="only row values or only completed Metrics",
            received=operation,
        )
    if not rows and not metrics:
        return _Scalar(recipe, None)
    boolean = operation in {
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "and_",
        "or_",
        "invert",
        "isnull",
        "notnull",
        "is_null",
        "isin",
    }
    numerical = operation not in {
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "and_",
        "or_",
        "invert",
        "isnull",
        "notnull",
        "is_null",
        "isin",
        "ifelse",
        "coalesce",
        "cases",
        "name",
    }
    arithmetic = operation in {"add", "sub", "mul", "truediv", "divide", "pow", "mod", "neg", "abs"}
    comparison = operation in {"eq", "ne", "lt", "le", "gt", "ge"}
    for child in children[:2] if operation == "divide" else children:
        if not isinstance(child, _Scalar):
            continue
        if arithmetic and (
            isinstance(child.value, bool) or not isinstance(child.value, (int, float, Decimal))
        ):
            _fail(
                "Numeric arithmetic requires numeric scalar literals.",
                expected="a finite number",
                received=type(child.value).__name__,
                kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            )
        if comparison and isinstance(child.recipe, Sequence):
            _fail(
                "Scalar comparison cannot consume a sequence.",
                expected="one scalar or isin(sequence)",
                received="sequence",
                kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            )
        if comparison and any(row.numeric for row in rows) and isinstance(child.value, bool):
            _fail(
                "Numeric comparison cannot consume a boolean.",
                expected="a numeric scalar",
                received="bool",
                kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            )
        if operation in {"and_", "or_"} and not isinstance(child.value, bool):
            _fail(
                "Boolean composition requires predicates.",
                expected="boolean predicates",
                received=type(child.value).__name__,
            )
    if numerical and any(
        row.field is not None
        and row.field.field_read is not None
        and (row.field.field_read.path or row.field.field_read.at is not None)
        for row in rows
    ):
        _fail(
            "Scalar path bindings do not authorize row arithmetic.",
            expected="native fields for numerical row computation",
            received=operation,
        )
    if numerical and any(not row.numeric or row.boolean for row in rows):
        _fail(
            "A categorical or time field is not a Measure.",
            expected="numeric Measure inputs",
            received=operation,
        )
    units = tuple(item.unit for item in children if isinstance(item, (RowFacts, MetricFacts)))
    from marivo.semantic.expressions import _arith_unit, _intersect

    unit = units[0] if units else None
    if operation in {"eq", "ne", "lt", "le", "gt", "ge", "least", "greatest", "clip"}:
        from marivo.semantic.unit_algebra import linear_unit, linear_units_conflict

        if linear_units_conflict(units):
            _fail(
                "Native inputs have incompatible units.",
                expected="commensurable comparison, bound, or selection units",
                received=f"{operation}{units}",
            )
        unit = linear_unit(units)
    if operation in {"add", "sub", "mul", "truediv", "divide"} and len(children) >= 2:
        left, right = children[:2]
        unit = _arith_unit(
            cast(
                "Literal['add', 'subtract', 'multiply', 'divide']",
                {
                    "add": "add",
                    "sub": "subtract",
                    "mul": "multiply",
                    "truediv": "divide",
                    "divide": "divide",
                }[operation],
            ),
            left.unit if isinstance(left, (RowFacts, MetricFacts)) else None,
            right.unit if isinstance(right, (RowFacts, MetricFacts)) else None,
            constant="left"
            if isinstance(left, _Scalar)
            else "right"
            if isinstance(right, _Scalar)
            else None,
            stage="Row" if rows else "Metric",
        )
    if operation in {"ifelse", "coalesce", "nullif", "cases"}:
        from marivo.semantic.unit_algebra import linear_unit, linear_units_conflict

        branches = children[1:] if operation == "ifelse" else children
        branch_units = tuple(
            item.unit
            for item in branches
            if isinstance(item, (RowFacts, MetricFacts)) and not item.boolean
        )
        if linear_units_conflict(branch_units):
            _fail(
                "Native branches have incompatible units.",
                expected="commensurable branch units",
                received=str(branch_units),
            )
        unit = linear_unit(branch_units)
    if operation == "sqrt" and unit not in (None, "1"):
        from collections import Counter

        from marivo.semantic.unit_algebra import FactorizedUnitV2, unit_state

        state = unit_state(unit)
        if not isinstance(state, FactorizedUnitV2) or any(
            count % 2
            for factors in (state.numerator, state.denominator)
            for count in Counter(factors).values()
        ):
            _fail(
                "Square root requires even unit exponents.",
                expected="a factorable unit with even exponents",
                received=str(unit),
            )
        assert isinstance(state, FactorizedUnitV2)
        unit = FactorizedUnitV2(
            "metric-unit-algebra/v2",
            tuple(
                atom for atom, count in Counter(state.numerator).items() for _ in range(count // 2)
            ),
            tuple(
                atom
                for atom, count in Counter(state.denominator).items()
                for _ in range(count // 2)
            ),
        ).render()
    if operation in {"log", "ln", "exp"} and unit not in (None, "1"):
        _fail(
            "This native function requires a dimensionless argument.",
            expected="unit 1",
            received=f"{operation}({unit})",
        )
    if boolean:
        unit = None
    if rows:
        root = rows[0].root
        if any(row.root.ref != root.ref for row in rows):
            _fail(
                "Row expression crosses contribution roots.",
                kind=ErrorKind.CROSS_ROOT_ROW_EXPRESSION,
                expected=root.ref.key,
                received=", ".join(row.root.ref.key for row in rows),
            )
        inputs: list[RowField | MeasureAssertion] = []
        captured = tuple(
            _row_recipe(item, inputs) if isinstance(item, RowFacts) else item.recipe
            for item in children
            if isinstance(item, (RowFacts, _Scalar))
        )
        permission: SumPermission = rows[0].permission
        if operation in {"add", "sub"}:
            for row in rows[1:]:
                permission = _intersect(permission, row.permission)
        elif operation not in {"neg", "name"} and not (
            len(rows) == 1
            and (
                operation == "mul"
                or (operation in {"truediv", "divide"} and isinstance(children[0], RowFacts))
            )
        ):
            permission = SumNone()
        return RowFacts(
            RowExpression(construct(captured), tuple(inputs)),
            root,
            unit,
            permission,
            rows[0].default_time,
            tuple(dict.fromkeys(axis for row in rows for axis in row.coordinates)),
            numeric=all(
                row.numeric
                for row in (
                    tuple(item for item in children[1:] if isinstance(item, RowFacts))
                    if operation == "ifelse"
                    else rows
                )
            ),
            boolean=boolean,
        )
    operands: list[MetricNode] = []
    captured_list: list[Resolver] = []
    for child in children:
        if isinstance(child, MetricFacts):
            captured_list.append(Variable(f"slot_{len(operands)}"))
            operands.append(child.node)
        elif isinstance(child, _Scalar):
            captured_list.append(child.recipe)
    return MetricFacts(
        MetricExpression(construct(tuple(captured_list)), tuple(operands)),
        tuple(dict.fromkeys(root for metric in metrics for root in metric.roots)),
        unit,
        tuple(pair for metric in metrics for pair in metric._declared),
        boolean,
    )


def _constant(recipe: Resolver, default: object = None) -> object:
    if isinstance(recipe, Just):
        from marivo.semantic.expressions import Relationship

        return recipe.value._parameter if isinstance(recipe.value, Relationship) else recipe.value
    return default


def _native_sample_axis(axis: ComparisonAxis) -> AxisBinding:
    if (
        not isinstance(axis, AxisBinding)
        or axis.role != "field"
        or axis.path
        or axis.field is None
        or axis.field.ref.kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}
    ):
        _fail(
            "Internal samples require native Dimension or TimeField groupings.",
            expected="native field or explicit Population",
            received=repr(axis),
        )
    assert isinstance(axis, AxisBinding)
    return axis


def _sample_domain(
    recipe: Resolver,
) -> tuple[AxisBinding, ...] | LiteralPopulation | PopulationProgram | SampleDomain:
    domain = _constant(recipe)
    if isinstance(domain, PopulationParameter):
        return domain._definition
    if isinstance(recipe, Sequence) and recipe.typ is not tuple:
        _fail(
            "Internal sample tuples must use a built-in tuple.",
            expected="a field, membership, nonempty tuple or Population",
            received=repr(recipe),
        )
    members = recipe.values if isinstance(recipe, Sequence) else (recipe,)
    population = _constant(members[0]) if members else None
    domain = population if isinstance(population, PopulationParameter) else None
    items: list[ComparisonAxis | MembershipBinding] = []
    for member in members[1:] if domain is not None else members:
        value = _constant(member)
        if isinstance(value, Grouping) and isinstance(value._binding, MembershipBinding):
            binding = value._binding
            if domain is None:
                for read in binding.fields:
                    _native_sample_axis(
                        AxisBinding(read.entity, read.field, read.path, "field", read.at)
                    )
            items.append(binding)
            continue
        if domain is not None or isinstance(value, PopulationParameter):
            _fail(
                "A sample Population must be first and followed only by membership rules.",
                expected="per(P) or per((P, rule, ...))",
                received=type(value).__name__,
            )
        if isinstance(value, Grouping):
            coordinate = value._binding
            assert not isinstance(coordinate, MembershipBinding)
            items.append(_native_sample_axis(coordinate))
            continue
        axis = _consume(member)
        if (
            not isinstance(axis, RowFacts)
            or axis.field is None
            or axis.field.reference.ref.kind
            not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}
        ):
            _fail(
                "Internal samples require semantic fields.",
                expected="resolved field",
                received=type(axis).__name__,
            )
        assert isinstance(axis, RowFacts) and axis.field is not None
        axis_read = axis.field.field_read
        if axis_read is not None and axis_read.path:
            _fail(
                "A scalar via field is not an internal sample domain.",
                kind=ErrorKind.INVALID_TIME_PATH,
                expected="native field or explicit Population",
                received=axis.field.reference.ref.key,
            )
        items.append(
            AxisBinding(
                axis.root,
                axis.field.reference,
                axis_read.path if axis_read else (),
                "field",
                axis_read.at if axis_read else None,
            )
        )
    try:
        axes, memberships = normalize_groupings(
            tuple(items), population_axes=domain._definition.axes if domain is not None else None
        )
    except DefinitionGraphError as error:
        _fail(
            "Sample coordinates and membership rules must be unique and retain captured order.",
            expected="distinct sample coordinates with one allocation per field",
            received=str(error),
            kind=ErrorKind.INVALID_EXPRESSION_VALUE,
        )
    if domain is not None:
        return SampleDomain(axes, memberships, domain._definition)
    if not axes:
        _fail(
            "Internal sample axes must be nonempty and unique.",
            expected="distinct semantic axes",
            received="empty axes",
            kind=ErrorKind.INVALID_EXPRESSION_VALUE,
        )
    native_axes = tuple(_native_sample_axis(axis) for axis in axes)
    return SampleDomain(native_axes, memberships) if memberships else native_axes


def _time(recipe: Resolver | None, row: RowFacts) -> TimeBinding | None:
    if recipe is None or (isinstance(recipe, Just) and recipe.value is None):
        return TimeBinding(row.default_time) if row.default_time is not None else None
    value = _consume(recipe)
    if (
        not isinstance(value, RowFacts)
        or value.field is None
        or value.field.reference.ref.kind is not SemanticKind.TIME_DIMENSION
    ):
        _fail(
            "Reduction time must be a semantic time field.",
            expected="a resolved TimeField",
            received=type(value).__name__,
        )
    assert isinstance(value, RowFacts) and value.field is not None
    read = value.field.field_read
    if value.root.ref != row.root.ref:
        _fail(
            "Time field belongs to another root.",
            kind=ErrorKind.INVALID_TIME_PATH,
            expected=row.root.ref.key,
            received=value.root.ref.key,
        )
    return TimeBinding(
        value.field.reference,
        read.path if read is not None else (),
        read.at if read is not None else None,
    )


def _reduce(
    receiver: RowFacts | _Samples,
    name: str,
    args: tuple[Resolver, ...],
    kwargs: dict[str, Resolver],
) -> MetricFacts:
    from marivo.semantic.expressions import _nulls, _quantile

    allowed = {"time", "nulls", "where"} if isinstance(receiver, RowFacts) else {"nulls"}
    if name in {"quantile", "approx_quantile"}:
        allowed.add("q")
    if isinstance(receiver, _Samples) and name in {"first", "last"}:
        allowed.update(("order_by", "include_null"))
    if name == "weighted_mean":
        allowed.add("weight")
    unexpected = set(kwargs) - allowed
    if unexpected or (args and name not in {"quantile", "approx_quantile"}) or len(args) > 1:
        _fail(
            "Reduction received unsupported arguments.",
            expected=f"{name} arguments: {sorted(allowed)}",
            received=str(sorted(unexpected) or "positional arguments"),
        )
    policy = _nulls(_constant(kwargs.get("nulls", Just("ignore"))), operation=name)
    q = (
        _quantile(_constant(args[0] if args else kwargs.get("q", Just(None))))
        if name in {"quantile", "approx_quantile"}
        else None
    )
    reducer = cast("Reducer", name)
    if isinstance(receiver, _Samples):
        metric = receiver.metric
        if name == "weighted_mean":
            weight = _consume(kwargs.get("weight", Just(None)))
            if not isinstance(weight, MetricFacts):
                _fail(
                    "Sample weight must be a Metric.",
                    expected="completed Metric",
                    received=type(weight).__name__,
                )
            assert isinstance(weight, MetricFacts)
            node: MetricNode = PerWeightedMean(
                metric.node,
                weight.node,
                receiver.axes,
                nulls=policy,
            )
            return MetricFacts(
                node,
                tuple(dict.fromkeys((*metric.roots, *weight.roots))),
                metric.unit,
                tuple(dict((*metric._declared, *weight._declared)).items()),
            )
        order_by: ExactRef | None = None
        include_null = _constant(kwargs.get("include_null", Just(False)))
        if type(include_null) is not bool:
            _fail(
                "include_null must be boolean.",
                expected="True or False",
                received=str(include_null),
            )
        sample_axes = receiver.axes if isinstance(receiver.axes, tuple) else receiver.axes.axes
        if name in {"first", "last"}:
            if "order_by" in kwargs:
                ordering = _consume(kwargs["order_by"])
                if not isinstance(ordering, RowFacts) or ordering.field is None:
                    _fail(
                        "order_by must name an internal time axis.",
                        expected="resolved TimeField",
                        received=type(ordering).__name__,
                    )
                assert isinstance(ordering, RowFacts) and ordering.field is not None
                order_by = ordering.field.reference
            else:
                times = tuple(
                    axis.field
                    for axis in sample_axes
                    if isinstance(axis, AxisBinding)
                    and axis.field is not None
                    and axis.field.ref.kind is SemanticKind.TIME_DIMENSION
                )
                if len(times) == 1:
                    order_by = times[0]
            if (
                order_by is None
                or order_by.ref.kind is not SemanticKind.TIME_DIMENSION
                or order_by
                not in tuple(axis.field for axis in sample_axes if isinstance(axis, AxisBinding))
            ):
                _fail(
                    "Ordered sample reduction requires one internal time axis.",
                    expected="order_by naming an internal TimeField",
                    received=str(order_by),
                )
        node = PerReduce(
            metric.node,
            receiver.axes,
            reducer,
            q=q,
            nulls=policy,
            order_by=order_by,
            include_null=include_null,
        )
        return MetricFacts(
            node,
            metric.roots,
            "1" if name in {"count", "count_distinct", "approx_nunique"} else metric.unit,
            metric._declared,
        )
    row = receiver
    if (
        row.field is not None
        and row.field.field_read is not None
        and (row.field.field_read.path or row.field.field_read.at is not None)
    ):
        _fail(
            "Source reductions require native fields.",
            kind=ErrorKind.INVALID_TIME_PATH,
            expected="an unbound Measure or native row expression",
            received=name,
        )
    if name in {"first", "last"}:
        _fail(
            "Ordered reductions require internal time samples.",
            expected="Metric.per(TimeField).first() or .last()",
            received=f"row.{name}()",
        )
    if name not in {"count", "count_distinct", "approx_nunique"} and not row.numeric:
        _fail("Reduction requires a numeric Measure.", expected="numeric row", received=name)
    if name == "sum" and isinstance(row.permission, SumNone):
        _fail(
            "This expression has no sum authorization.",
            kind=ErrorKind.MISSING_SUM_PERMISSION,
            expected="an additive Measure or linear expression",
            received="non-additive expression",
        )
    time = _time(kwargs.get("time"), row)
    if row.field is not None and row.field.reference.ref.kind is SemanticKind.ENTITY:
        if name != "count":
            _fail("Entity only supports count.", expected="count()", received=name)
        return MetricFacts(EntityCount(row.root, time), (row.root,), "1")
    if name == "weighted_mean":
        weight = _consume(kwargs.get("weight", Just(None)))
        if (
            not isinstance(weight, RowFacts)
            or weight.root.ref != row.root.ref
            or not weight.numeric
        ):
            _fail(
                "Source weight must be a numeric row on the same root.",
                kind=ErrorKind.CROSS_ROOT_ROW_EXPRESSION
                if isinstance(weight, RowFacts)
                else ErrorKind.INVALID_EXPRESSION_STAGE,
                expected=row.root.ref.key,
                received=(
                    f"{row.root.ref.path} weighted by {weight.root.ref.path}"
                    if isinstance(weight, RowFacts)
                    else type(weight).__name__
                ),
            )
        assert isinstance(weight, RowFacts)
        node = SourceWeightedMean(
            row.node,
            weight.node,
            time,
            nulls=policy,
        )
    else:
        node = SourceAggregate(row.node, reducer, time, q=q, nulls=policy)
    if "where" in kwargs:
        predicate = _consume_predicate_recipe(kwargs["where"])
        if any(isinstance(item, PopulationContains) for item in predicate.node.inputs):
            _fail(
                "Source reduction where accepts only same-row field conditions.",
                expected="a boolean field condition without Population membership",
                received="a membership leaf",
                action="Apply .where(predicate) to the completed source Metric for membership conditions.",
                help_target="predicate",
            )
        _require_condition_roots(predicate, (row.root,))
        node = ContributionFilter(node, predicate.node)
    return MetricFacts(
        node,
        (row.root,),
        "1" if name in {"count", "count_distinct", "approx_nunique"} else row.unit,
    )


def _consume(recipe: Resolver) -> _Facts:
    if isinstance(recipe, PopulationContainsResolver):
        _fail(
            "Membership leaves require an admitted source predicate position.",
            expected="membership combined only with &, |, ~ and boolean constants",
            received="a membership leaf used as a numeric or method receiver",
            action="Combine in_population(P) with boolean conditions and pass it to where.",
            help_target="predicate",
        )
    if isinstance(recipe, PopulationCountResolver):
        return MetricFacts(MemberCount(recipe.population._definition), (), "1")
    if isinstance(recipe, SemanticResolver):
        item = recipe.input
        if item.reference.ref.kind is SemanticKind.ENTITY:
            # Entity count consumes the identity directly; it never uses this placeholder as a field.
            return RowFacts(
                RowExpression(Just(0), ()),
                item.root,
                None,
                SumNone(),
                item.default_time,
                item.coordinates,
                False,
                field=item,
            )
        return RowFacts(
            RowField(item.reference, item.root, item.unit, item.permission, read=item.field_read),
            item.root,
            item.unit,
            item.permission,
            item.default_time,
            item.coordinates,
            item.reference.ref.kind is SemanticKind.MEASURE,
            field=item,
        )
    if isinstance(recipe, DeclarationResolver):
        value = _consume(recipe.recipe)
        declaration = recipe.declaration
        if declaration.kind == "metric":
            if not isinstance(value, MetricFacts):
                _fail(
                    "Metric declaration must complete a reduction.",
                    expected="Metric",
                    received=type(value).__name__,
                )
            assert isinstance(value, MetricFacts)
            if declaration.reference is not None:
                return replace(
                    value,
                    node=DeclaredMetricRef(declaration.reference),
                    _declared=(*value._declared, (declaration.reference, value.node)),
                )
            return value
        if not isinstance(value, RowFacts) or not value.numeric:
            _fail(
                "Measure declaration must return numeric rows.",
                expected="numeric row",
                received=type(value).__name__,
            )
        assert isinstance(value, RowFacts)
        unit = value.unit if declaration.unit is None else declaration.unit
        permission = value.permission if declaration.permission is None else declaration.permission
        if unit == value.unit and permission == value.permission:
            return value
        import hashlib

        from marivo._definition.deferred import canonical_recipe

        identity = hashlib.sha256(str(canonical_recipe(recipe.recipe)).encode()).hexdigest()
        authorization = Authorization(
            ExactRef(
                ref_factory.measure(f"{value.root.ref.path}.{declaration.name}"),
                f"sha256:{identity}",
            )
        )
        return replace(
            value,
            node=MeasureAssertion(
                value.node, declaration.unit, declaration.permission, authorization
            ),
            unit=unit,
            permission=permission,
            field=None,
        )
    if isinstance(recipe, Just):
        return _Scalar(recipe, recipe.value)
    if isinstance(recipe, Sequence):
        return _compose(
            recipe,
            tuple(_consume(item) for item in recipe.values),
            lambda parts: Sequence(parts),
            operation="cases",
        )
    if isinstance(recipe, BinaryOperator):
        return _compose(
            recipe,
            (_consume(recipe.left), _consume(recipe.right)),
            lambda parts: BinaryOperator(recipe.func, *parts),
            operation=recipe.func.__name__,
        )
    if isinstance(recipe, UnaryOperator):
        return _compose(
            recipe,
            (_consume(recipe.arg),),
            lambda parts: UnaryOperator(recipe.func, parts[0]),
            operation=recipe.func.__name__,
        )
    if isinstance(recipe, Call):
        if isinstance(recipe.func, Attr):
            name = attribute_name(recipe.func)
            receiver = _consume(recipe.func.obj)
            if name == "window":
                from marivo._definition.graph import DefinitionNode, children, walk
                from marivo._definition.windows import (
                    AllHistory,
                    Since,
                    ToDate,
                    TrailingDuration,
                    TrailingPeriods,
                )

                rule = _constant(recipe.args[0]) if len(recipe.args) == 1 else None
                if (
                    not isinstance(receiver, MetricFacts)
                    or recipe.kwargs
                    or type(rule)
                    not in (AllHistory, Since, ToDate, TrailingDuration, TrailingPeriods)
                ):
                    _fail(
                        "window requires one source-free observation rule.",
                        expected="Metric.window(mv.all_history() or another window rule)",
                        received="invalid receiver or rule",
                    )
                assert isinstance(receiver, MetricFacts) and isinstance(
                    rule, (AllHistory, Since, ToDate, TrailingDuration, TrailingPeriods)
                )
                definitions = dict(receiver._declared)
                walk((receiver.node,), resolve=definitions.get)
                pending: list[DefinitionNode] = [receiver.node]
                seen: set[int] = set()
                while pending:
                    node = pending.pop()
                    if id(node) in seen:
                        continue
                    seen.add(id(node))
                    if isinstance(node, MetricWindow):
                        _fail(
                            "Windows cannot be nested on one dependency path.",
                            expected="a Metric with no existing window below this call",
                            received="nested window",
                        )
                    if isinstance(node, DeclaredMetricRef):
                        pending.append(definitions[node.ref])
                    else:
                        pending.extend(edge.child for edge in children(node))
                return replace(receiver, node=MetricWindow(receiver.node, rule))
            if name == "at":
                from marivo._temporal import InstantAnchor

                anchor = _constant(recipe.args[0]) if len(recipe.args) == 1 else None
                if (
                    not isinstance(receiver, RowFacts)
                    or receiver.field is None
                    or not receiver.field.versioned
                    or not isinstance(anchor, InstantAnchor)
                    or anchor.utcoffset() is None
                    or recipe.kwargs
                ):
                    _fail(
                        "at requires a versioned semantic field and exact anchor.",
                        expected="Field.at(session.instant(...)) on a versioned owner",
                        received="invalid field or anchor",
                    )
                assert (
                    isinstance(receiver, RowFacts)
                    and receiver.field is not None
                    and isinstance(anchor, InstantAnchor)
                )
                native = receiver.field.field_read or FieldRead(
                    receiver.field.reference, receiver.field.root
                )
                read = replace(native, at=anchor)
                field = replace(receiver.field, field_read=read)
                return replace(
                    receiver,
                    field=field,
                    node=RowField(
                        field.reference, field.root, field.unit, field.permission, read=read
                    ),
                )
            if name == "within":
                from marivo._temporal import TimeScope

                scope = _constant(recipe.args[0]) if len(recipe.args) == 1 else None
                if (
                    not isinstance(receiver, RowFacts)
                    or receiver.field is None
                    or receiver.field.reference.ref.kind is not SemanticKind.TIME_DIMENSION
                    or not isinstance(scope, TimeScope)
                    or recipe.kwargs
                ):
                    _fail(
                        "within requires a TimeField and immutable time scope.",
                        expected="TimeField.within(TimeScope)",
                        received="invalid field or scope",
                    )
                assert isinstance(scope, TimeScope)
                return _consume(
                    BinaryOperator(
                        operator.and_,
                        BinaryOperator(operator.ge, recipe.func.obj, Just(scope.start)),
                        BinaryOperator(operator.lt, recipe.func.obj, Just(scope.end)),
                    )
                )
            if name == "via":
                if (
                    not isinstance(receiver, RowFacts)
                    or receiver.field is None
                    or len(recipe.args) != 1
                    or recipe.kwargs
                ):
                    _fail(
                        "via requires a semantic field and one path tuple.",
                        kind=ErrorKind.INVALID_TIME_PATH,
                        expected="Field.via((relationship, ...))",
                        received="invalid receiver or arguments",
                    )
                assert isinstance(receiver, RowFacts) and receiver.field is not None
                if receiver.field.field_read is not None and receiver.field.field_read.path:
                    _fail(
                        "via is already bound on this field.",
                        kind=ErrorKind.INVALID_TIME_PATH,
                        expected="one explicit complete path",
                        received="repeated via",
                    )
                path_recipe = recipe.args[0]
                if not isinstance(path_recipe, Sequence) or path_recipe.typ is not tuple:
                    _fail(
                        "via requires an ordered tuple of relationships.",
                        kind=ErrorKind.INVALID_TIME_PATH,
                        expected="a relationship tuple",
                        received=type(path_recipe).__name__,
                    )
                parts = path_recipe.values if isinstance(path_recipe, Sequence) else (path_recipe,)
                relationships: list[RelationshipParameter] = []
                for part in parts:
                    relation = _constant(part)
                    if not isinstance(
                        relation, RelationshipParameter
                    ) or relation.cardinality not in ("one_to_one", "many_to_one"):
                        _fail(
                            "via requires a directed single-valued path.",
                            kind=ErrorKind.INVALID_TIME_PATH,
                            expected="one_to_one or many_to_one relationships",
                            received=type(relation).__name__,
                        )
                    assert isinstance(relation, RelationshipParameter)
                    relationships.append(relation)
                if (
                    not relationships
                    or relationships[-1].to_entity.ref != receiver.root.ref
                    or any(a.to_entity.ref != b.from_entity.ref for a, b in pairwise(relationships))
                ):
                    _fail(
                        "via path does not connect to the field owner.",
                        kind=ErrorKind.INVALID_TIME_PATH,
                        expected=receiver.root.ref.key,
                        received="disconnected or empty path",
                    )
                read = FieldRead(
                    receiver.field.reference,
                    receiver.root,
                    tuple(item.ref for item in relationships),
                    receiver.field.field_read.at if receiver.field.field_read is not None else None,
                )
                field = replace(receiver.field, field_read=read)
                return replace(
                    receiver,
                    root=relationships[0].from_entity,
                    field=field,
                    node=RowField(
                        field.reference, field.root, field.unit, field.permission, read=read
                    ),
                )
            if name in _REDUCERS | {"weighted_mean"}:
                if not isinstance(receiver, (RowFacts, _Samples)):
                    _fail(
                        "Completed Metrics need per(...) before another reduction.",
                        expected="row or internal samples",
                        received=name,
                    )
                assert isinstance(receiver, (RowFacts, _Samples))
                return _reduce(receiver, name, recipe.args, dict(recipe.kwargs))
            if name == "per":
                if not isinstance(receiver, MetricFacts) or len(recipe.args) != 1 or recipe.kwargs:
                    _fail(
                        "per requires a Metric and one sample specification.",
                        expected="Metric.per(field, membership, tuple or Population)",
                        received="invalid receiver or arguments",
                    )
                assert isinstance(receiver, MetricFacts)
                return _Samples(receiver, _sample_domain(recipe.args[0]))
            if name == "where":
                if not isinstance(receiver, MetricFacts) or len(recipe.args) != 1:
                    _fail(
                        "where requires a Metric and predicate.",
                        expected="Metric.where(predicate)",
                        received="invalid receiver or arguments",
                        help_target="Metric.where",
                        action=(
                            "Reduce the row quantity with a business-authorized reducer, then "
                            "apply .where(predicate) to that source Metric. Do not infer sum permission."
                        ),
                    )
                assert isinstance(receiver, MetricFacts)
                source = receiver.node
                declared = dict(receiver._declared)
                while isinstance(source, (DeclaredMetricRef, ContributionFilter)):
                    if isinstance(source, DeclaredMetricRef):
                        source = declared[source.ref]
                    else:
                        source = source.operand
                if not isinstance(source, (SourceAggregate, SourceWeightedMean, EntityCount)):
                    raise SemanticExpressionError(
                        kind=ErrorKind.INVALID_WHERE_SCOPE,
                        message="where requires one source reduction or a paired source scope.",
                        refs=(receiver.node.ref.ref.key,)
                        if isinstance(receiver.node, DeclaredMetricRef)
                        else (),
                        expected="one source Metric",
                        received=type(source).__name__,
                        action="Filter each source child Metric before combining or sampling.",
                    )
                predicate = _consume_predicate_recipe(recipe.args[0])
                _require_condition_roots(predicate, receiver.roots)
                return replace(receiver, node=ContributionFilter(receiver.node, predicate.node))
            values = (receiver, *(_consume(arg) for arg in recipe.args))
            keyword_keys = tuple(recipe.kwargs)
            values += tuple(_consume(recipe.kwargs[key]) for key in keyword_keys)
            positional_count = len(recipe.args)
            return _compose(
                recipe,
                values,
                lambda parts: Call(
                    Attr(parts[0], name),
                    *parts[1 : 1 + positional_count],
                    **dict(zip(keyword_keys, parts[1 + positional_count :], strict=True)),
                ),
                operation=name,
            )
        function = _constant(recipe.func)
        name = getattr(function, "__name__", "")
        values = tuple(_consume(arg) for arg in recipe.args)
        keyword_keys = tuple(recipe.kwargs)
        values += tuple(_consume(recipe.kwargs[key]) for key in keyword_keys)
        positional_count = len(recipe.args)
        return _compose(
            recipe,
            values,
            lambda parts: Call(
                recipe.func,
                *parts[:positional_count],
                **dict(zip(keyword_keys, parts[positional_count:], strict=True)),
            ),
            operation=name,
        )
    _fail(
        "Unsupported Deferred expression shape.",
        expected="an admitted native numerical recipe",
        received=type(recipe).__name__,
    )
    raise AssertionError("unreachable")


def _inspect(value: Deferred) -> DeferredInspection:
    try:
        result: DeferredInspection = inspect_deferred(value)
        if result.symbols:
            raise SemanticExpressionError(
                kind=ErrorKind.INVALID_EXPRESSION_VALUE,
                message="Analysis requires closed semantic inputs; this recipe contains unbound lookup symbols.",
                refs=tuple(reference.key for reference in result.symbols),
                expected="closed values returned by a loaded Catalog .get()",
                received="an authoring recipe with unbound semantic lookups",
                action="Load the model, then obtain closed fields, Entities, or Metrics through the Catalog before analysis.",
            )
        return result
    except DefinitionGraphError as error:
        raise SemanticExpressionError.from_definition(
            error, operation="Deferred consumption"
        ) from error


def consume_row(value: Deferred) -> RowFacts:
    inspection = _inspect(value)
    result = _consume(inspection.recipe)
    if not isinstance(result, RowFacts) or not result.numeric or result.boolean:
        _fail(
            "Expected a numeric row expression.",
            expected="a controlled Measure expression",
            received=type(result).__name__,
        )
    assert isinstance(result, RowFacts)
    return result


def consume_metric(value: Deferred) -> MetricFacts:
    inspection = _inspect(value)
    result = _consume(inspection.recipe)
    if not isinstance(result, MetricFacts) or result.boolean:
        _fail(
            "Expected a completed Metric.",
            expected="a source or sample reduction and Metric arithmetic",
            received=type(result).__name__,
        )
    assert isinstance(result, MetricFacts)
    return result


def consume_field(value: Deferred) -> SemanticInput:
    result = _consume(_inspect(value).recipe)
    if not isinstance(result, RowFacts) or result.field is None:
        _fail(
            "Expected a semantic field.",
            expected="a resolved field or valid via path",
            received=type(result).__name__,
        )
    assert isinstance(result, RowFacts) and result.field is not None
    return result.field


def _contains_population(recipe: Resolver) -> bool:
    if isinstance(recipe, PopulationContainsResolver):
        return True
    if isinstance(recipe, DeclarationResolver):
        return _contains_population(recipe.recipe)
    if isinstance(recipe, BinaryOperator):
        return _contains_population(recipe.left) or _contains_population(recipe.right)
    if isinstance(recipe, UnaryOperator):
        return _contains_population(recipe.arg)
    if isinstance(recipe, Attr):
        return _contains_population(recipe.obj)
    if isinstance(recipe, Call):
        return any(
            _contains_population(item)
            for item in (recipe.func, *recipe.args, *recipe.kwargs.values())
        )
    if isinstance(recipe, Sequence):
        return any(_contains_population(item) for item in recipe.values)
    return False


def _require_condition_roots(predicate: PredicateFacts, roots: tuple[ExactRef, ...]) -> None:
    allowed = {root.ref for root in roots}
    for root in predicate.roots:
        if root.ref not in allowed:
            _fail(
                "The predicate belongs to another contribution root.",
                kind=ErrorKind.INVALID_WHERE_SCOPE,
                expected=", ".join(item.ref.key for item in roots),
                received=root.ref.key,
                action="Use a predicate on a contribution root, or bind its field through a declared relationship.",
                help_target="predicate",
            )


def _consume_predicate_recipe(recipe: Resolver) -> PredicateFacts:
    inputs: list[RowExpression | PopulationContains] = []
    roots: dict[str, ExactRef] = {}

    def capture(item: Resolver) -> Resolver:
        if isinstance(item, PopulationContainsResolver):
            slot = Variable(f"slot_{len(inputs)}")
            inputs.append(PopulationContains(item.population._definition))
            return slot
        if not _contains_population(item):
            if isinstance(item, Just) and type(item.value) is bool:
                return item
            value = _consume(item)
            if not isinstance(value, RowFacts) or not value.boolean:
                _fail(
                    "Expected a source boolean condition.",
                    expected="a boolean field expression or Population membership leaf",
                    received=type(value).__name__,
                    help_target="predicate",
                )
            assert isinstance(value, RowFacts)
            row_inputs: list[RowField | MeasureAssertion] = []
            row_recipe = _row_recipe(value, row_inputs)
            slot = Variable(f"slot_{len(inputs)}")
            inputs.append(RowExpression(row_recipe, tuple(row_inputs)))
            roots.setdefault(value.root.ref.key, value.root)
            return slot
        if isinstance(item, BinaryOperator) and item.func in (operator.and_, operator.or_):
            return BinaryOperator(item.func, capture(item.left), capture(item.right))
        if isinstance(item, UnaryOperator) and item.func is operator.invert:
            return UnaryOperator(item.func, capture(item.arg))
        _fail(
            "Population membership supports only boolean combination.",
            expected="&, |, ~ and boolean constants around membership leaves",
            received=type(item).__name__,
            action="Keep field methods inside a pure field condition, then combine it with in_population(P).",
            help_target="predicate",
        )

    normalized = capture(recipe)
    if not inputs:
        _fail(
            "A source predicate requires a captured semantic leaf.",
            expected="a boolean field expression or Population membership leaf",
            received="a bare boolean constant",
            help_target="predicate",
        )
    if len(roots) > 1:
        _fail(
            "Predicate field leaves belong to different contribution roots.",
            kind=ErrorKind.INVALID_WHERE_SCOPE,
            expected="field conditions on one contribution root",
            received=", ".join(roots),
            action="Bind fields through an explicit single-valued relationship to one root.",
            help_target="predicate",
        )
    return PredicateFacts(PredicateExpression(normalized, tuple(inputs)), tuple(roots.values()))


def consume_predicate(value: Deferred) -> PredicateFacts:
    """Capture the native boolean structure without reading or evaluating sources."""
    return _consume_predicate_recipe(_inspect(value).recipe)


def consume_metric_in_state(value: Deferred, state: CompiledSemanticState) -> MetricFacts:
    """Consume published references through the consumer's frozen catalog scope.

    Captured local expressions stay immutable. Only published kind/path lookups
    select the scope's completed definition; its body is not executed or imported.
    """
    from marivo._definition.deferred import resolver_of
    from marivo.semantic.expressions import _Resolver

    resolver = _Resolver(state)

    def bind(recipe: Resolver, active: frozenset[str]) -> Resolver:
        if isinstance(recipe, DeclarationResolver):
            declaration = recipe.declaration
            if declaration.reference is not None:
                target = declaration.reference.ref
                if target.path in active:
                    _fail(
                        "Published Metric dependency is cyclic.",
                        expected="an acyclic declaration closure",
                        received=target.key,
                    )
                current = state.registry.declared_metrics.get(target.path)
                if current is None:
                    _fail(
                        "Published Metric is absent from this consumption scope.",
                        kind=ErrorKind.MISSING_METRIC_REF,
                        expected="a Metric in the frozen catalog",
                        received=target.key,
                    )
                captured = resolver_of(current)
                assert isinstance(captured, DeclarationResolver)
                return DeclarationResolver(
                    bind(captured.recipe, active | {target.path}),
                    replace(
                        captured.declaration,
                        reference=resolver.exact(target),
                        domain=declaration.domain,
                    ),
                )
            return DeclarationResolver(bind(recipe.recipe, active), declaration)
        if isinstance(recipe, BinaryOperator):
            return BinaryOperator(
                recipe.func, bind(recipe.left, active), bind(recipe.right, active)
            )
        if isinstance(recipe, UnaryOperator):
            return UnaryOperator(recipe.func, bind(recipe.arg, active))
        if isinstance(recipe, Attr):
            return Attr(bind(recipe.obj, active), recipe.name)
        if isinstance(recipe, Call):
            return Call(
                bind(recipe.func, active),
                *(bind(arg, active) for arg in recipe.args),
                **{key: bind(arg, active) for key, arg in recipe.kwargs.items()},
            )
        if isinstance(recipe, Sequence):
            return Sequence(tuple(bind(item, active) for item in recipe.values))
        return recipe

    original = _inspect(value)
    return consume_metric(Deferred(bind(original.recipe, frozenset())))
