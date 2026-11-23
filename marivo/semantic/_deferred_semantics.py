"""Source-free semantic consumption of native Ibis Deferred recipes.

The numerical structure remains an Ibis resolver recipe. This module adds only
semantic boundaries and facts; binding supplies physical types later."""

from __future__ import annotations

import hashlib
import operator
from dataclasses import replace
from typing import Literal, cast

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

from marivo._definition.deferred import (
    CapturedDefaultTimeResolver,
    DeclarationResolver,
    DeferredInspection,
    NamedResolver,
    PopulationContainsResolver,
    PopulationCountResolver,
    RescaleResolver,
    SemanticInput,
    SemanticResolver,
    attribute_name,
    canonical_recipe,
    inspect_deferred,
)
from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MeasureAssertion,
    MemberCount,
    MetricNode,
    MetricWindow,
    PredicateExpression,
    RowExpression,
    RowField,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.graph import DefinitionNode, children, walk
from marivo._definition.population_builders import validate_path_read
from marivo._definition.relations import (
    PopulationContains,
)
from marivo._definition.types import (
    Authorization,
    DefinitionGraphError,
    ExactRef,
    FieldRead,
    NullPolicy,
    PathParameter,
    RowAttributeTime,
    SumNone,
)
from marivo._definition.windows import AllHistory, Since, ToDate, TrailingDuration, TrailingPeriods
from marivo._kernel.refs import SemanticKind
from marivo._kernel.refs import ref as ref_factory
from marivo._kernel.temporal import InstantAnchor
from marivo.semantic._deferred_composition import _compose, _row_recipe
from marivo.semantic._deferred_facts import (
    _REDUCERS,
    MetricFacts,
    PredicateFacts,
    RowFacts,
    _Facts,
    _fail,
    _Samples,
    _Scalar,
)
from marivo.semantic._deferred_sample_reduction import _reduce_samples, _ReductionCall
from marivo.semantic._deferred_time import (
    _calendar_sample_recipe,
    _capture_source_time,
    _constant,
    _field_calendar_sample,
    _sample_domain,
    _time,
)
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.expressions.inputs import RecipeInput, _as_deferred
from marivo.semantic.expressions.metric_time import (
    validate_metric_time_consumers,
)
from marivo.semantic.expressions.operations import _nulls, _quantile


def capture_source_time(
    recipe: Resolver, time: Resolver | None, *, operation: str | None = None
) -> CapturedDefaultTimeResolver | None:
    """Check explicit time or freeze a closed receiver's default selection."""
    return _capture_source_time(recipe, time, _consume, operation=operation)


def _reduce(
    receiver: RowFacts | _Samples,
    name: _ReductionCall,
    args: tuple[Resolver, ...],
    kwargs: dict[str, Resolver],
) -> MetricFacts:

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
    if isinstance(receiver, _Samples):
        return _reduce_samples(receiver, name, policy, q, kwargs, _consume)
    return _reduce_rows(receiver, name, policy, q, kwargs)


def _reduce_rows(
    row: RowFacts,
    name: _ReductionCall,
    policy: NullPolicy,
    q: float | None,
    kwargs: dict[str, Resolver],
) -> MetricFacts:
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
    time = _time(kwargs.get("time"), row, _consume)
    independent_origin: Literal["explicit_none", "no_entity_default"] = (
        "explicit_none"
        if isinstance(kwargs.get("time"), Just) and kwargs["time"].value is None
        else "no_entity_default"
    )
    if row.field is not None and row.field.reference.ref.kind is SemanticKind.ENTITY:
        if name != "count":
            _fail("Entity only supports count.", expected="count()", received=name)
        return MetricFacts(
            EntityCount(row.root, time, independent_origin=independent_origin), (row.root,), "1"
        )
    node: MetricNode
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
            contribution_root=row.root,
            independent_origin=independent_origin,
        )
    else:
        node = SourceAggregate(
            row.node,
            name,
            time,
            q=q,
            nulls=policy,
            contribution_root=row.root,
            independent_origin=independent_origin,
        )
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


def _consume_window(recipe: Call, receiver: _Facts) -> MetricFacts:
    rule = _constant(recipe.args[0]) if len(recipe.args) == 1 else None
    if (
        not isinstance(receiver, MetricFacts)
        or recipe.kwargs
        or type(rule) not in (AllHistory, Since, ToDate, TrailingDuration, TrailingPeriods)
    ):
        _fail(
            "window requires one source-free observation rule.",
            expected="Metric.window(mv.all_history() or another window rule)",
            received="invalid receiver or rule",
        )
    assert isinstance(receiver, MetricFacts) and isinstance(  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
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


def _consume_at(recipe: Call, receiver: _Facts) -> RowFacts:
    anchor = _constant(recipe.args[0]) if len(recipe.args) == 1 else None
    if anchor is None and len(recipe.args) == 1:
        control = _consume(recipe.args[0])
        if (
            not isinstance(control, RowFacts)
            or control.field is None
            or control.field.reference.ref.kind is not SemanticKind.TIME_DIMENSION
            or (
                control.field.field_read is not None
                and (control.field.field_read.path or control.field.field_read.at is not None)
            )
        ):
            _fail(
                "Row attribute time requires one native, unanchored TimeDimension.",
                expected="a named, native TimeDimension without via/at",
                received=control.field.reference.ref.key
                if isinstance(control, RowFacts) and control.field is not None
                else "a computed expression without a native field identity",
                action="Declare the derived time as a fact-root TimeDimension, without via/at, or use session.instant(...) for a fixed attribute time.",
            )
        assert isinstance(control, RowFacts) and control.field is not None  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
        anchor = RowAttributeTime(control.field.reference, control.field.root)
    if (
        not isinstance(receiver, RowFacts)
        or receiver.field is None
        or not receiver.field.versioned
        or not isinstance(anchor, (InstantAnchor, RowAttributeTime))
        or (isinstance(anchor, InstantAnchor) and anchor.utcoffset() is None)
        or (
            isinstance(anchor, RowAttributeTime)
            and receiver.field.reference.ref.kind is not SemanticKind.DIMENSION
        )
        or recipe.kwargs
    ):
        _fail(
            "at requires a versioned field and a fixed instant or fact-row time.",
            expected="Dimension.at(session.instant(...)) or Dimension.at(fact_time)",
            received="invalid field or anchor",
        )
    assert (
        isinstance(receiver, RowFacts)  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
        and receiver.field is not None  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
        and isinstance(anchor, (InstantAnchor, RowAttributeTime))
    )
    native = receiver.field.field_read or FieldRead(
        receiver.field.reference,
        receiver.field.root,
        versioned=receiver.field.versioned,
        unit=receiver.field.unit,
    )
    if native.at is not None:
        _fail(
            "Two time authorities bind the same field node.",
            kind=ErrorKind.INVALID_TIME_PATH,
            expected="one version anchor at the terminal path node",
            received=receiver.field.reference.ref.key,
            action="Keep either the terminal relationship.at(...) or the field.at(...) anchor.",
        )
    read = replace(native, at=anchor)
    field = replace(receiver.field, field_read=read)
    return replace(
        receiver,
        root=anchor.root if isinstance(anchor, RowAttributeTime) else receiver.root,
        field=field,
        node=RowField(
            field.reference,
            field.root,
            field.unit,
            field.permission,
            read=read,
            origins=receiver.node.origins,
        ),
    )


def _consume_via(recipe: Call, receiver: _Facts) -> RowFacts:
    if (
        not isinstance(receiver, RowFacts)
        or receiver.field is None
        or len(recipe.args) != 1
        or recipe.kwargs
    ):
        _fail(
            "via requires a semantic field and a Path.",
            kind=ErrorKind.INVALID_TIME_PATH,
            expected="Field.via(mv.path(relationship, ...))",
            received="invalid receiver or arguments",
        )
    assert isinstance(receiver, RowFacts) and receiver.field is not None  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
    if receiver.field.field_read is not None and receiver.field.field_read.path:
        _fail(
            "via is already bound on this field.",
            kind=ErrorKind.INVALID_TIME_PATH,
            expected="one explicit complete path",
            received="repeated via",
        )
    path = _constant(recipe.args[0])
    if not isinstance(path, PathParameter):
        _fail(
            "via requires a bound Path.",
            kind=ErrorKind.INVALID_TIME_PATH,
            expected="mv.path(relationship, ...)",
            received=type(path).__name__,
        )
    if path.target.ref != receiver.field.root.ref:
        _fail(
            "via path does not connect to the field owner.",
            kind=ErrorKind.INVALID_TIME_PATH,
            expected=receiver.field.root.ref.key,
            received=path.target.ref.key,
        )
    at = receiver.field.field_read.at if receiver.field.field_read is not None else None
    terminal_at = path.steps[-1].target_at
    if at is not None and terminal_at is not None:
        _fail(
            "Two time authorities bind the same field node.",
            kind=ErrorKind.INVALID_TIME_PATH,
            expected=f"one anchor at node {len(path.steps)} ({path.target.ref.key})",
            received="terminal relationship.at(...) and field.at(...)",
            action="Keep either the terminal relationship.at(...) or the field.at(...) anchor.",
        )
    at = at if at is not None else terminal_at
    try:
        validate_path_read(path, at, True, source_row=True, scalar=True, terminal_required=False)
    except DefinitionGraphError as error:
        _fail(
            "The field's directed Path cannot provide a scalar read.",
            kind=ErrorKind.INVALID_TIME_PATH,
            expected="a directed single-valued Path with required node anchors",
            received=str(error),
            action="Bind every required versioned node and choose a directed to-one Path.",
            help_target="field.via",
        )
    relationships = path.relationships
    read = FieldRead(
        receiver.field.reference,
        receiver.field.root,
        tuple(item.ref for item in relationships),
        at,
        source_at=path.source_at,
        step_ats=path.step_ats,
        path_root=path.source,
        path_facts=relationships,
        versioned=receiver.field.versioned,
        unit=receiver.field.unit,
    )
    field = replace(receiver.field, field_read=read)
    return replace(
        receiver,
        root=path.source,
        field=field,
        default_time=relationships[0]._from_default_time,
        node=RowField(
            field.reference,
            field.root,
            field.unit,
            field.permission,
            read=read,
            origins=receiver.node.origins,
        ),
    )


def _consume_per(recipe: Call, receiver: _Facts) -> _Samples:
    keywords = dict(recipe.kwargs)
    by = keywords.pop("by", None)
    grain = keywords.pop("grain", None)
    if (
        not isinstance(receiver, MetricFacts)
        or len(recipe.args) > 1
        or bool(recipe.args) == (by is not None)
        or keywords
    ):
        _fail(
            "per requires a Metric and one sample specification.",
            expected="Metric.per(field, membership, tuple, Population or Grain)",
            received="invalid receiver or arguments",
        )
    assert isinstance(receiver, MetricFacts)
    sample = recipe.args[0] if recipe.args else by
    assert sample is not None
    return _Samples(
        receiver,
        _sample_domain(sample, _consume)
        if grain is None
        else _field_calendar_sample(sample, grain, receiver.roots, _consume),
    )


def _consume_where(recipe: Call, receiver: _Facts) -> MetricFacts:
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
        source = declared[source.ref] if isinstance(source, DeclaredMetricRef) else source.operand
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


def _consume_method(recipe: Call, method: Attr) -> _Facts:
    name = attribute_name(method)
    receiver = _consume(method.obj)
    if name == "window":
        return _consume_window(recipe, receiver)
    if name == "at":
        return _consume_at(recipe, receiver)
    if name == "via":
        return _consume_via(recipe, receiver)
    if name in _REDUCERS | {"weighted_mean"}:
        if not isinstance(receiver, (RowFacts, _Samples)):
            _fail(
                "The value is already a completed Metric; choose internal samples before another reduction.",
                expected="row or internal samples",
                received=f"{type(receiver).__name__}.{name}",
                action=f"Use m.per(...).{name}(...) to reduce completed Metric samples.",
            )
        assert isinstance(receiver, (RowFacts, _Samples))
        return _reduce(receiver, cast("_ReductionCall", name), recipe.args, dict(recipe.kwargs))
    if name == "per":
        return _consume_per(recipe, receiver)
    if name == "where":
        return _consume_where(recipe, receiver)
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
        _consume,
        operation=name,
    )


def _consume(recipe: Resolver) -> _Facts:
    if isinstance(recipe, NamedResolver):
        return _consume(recipe.recipe)
    if isinstance(recipe, RescaleResolver):
        value = _consume(recipe.recipe)
        if (
            not isinstance(value, (RowFacts, MetricFacts))
            or value.boolean
            or (isinstance(value, RowFacts) and not value.numeric)
        ):
            _fail(
                "rescale requires a numeric Row or completed Metric expression.",
                expected="a numeric Row or Metric expression",
                received=type(value).__name__,
                help_target="rescale",
                action="Pass a Measure expression or a completed numeric reduction to ms.rescale.",
            )
        assert isinstance(value, (RowFacts, MetricFacts))
        if value.unit is None:
            _fail(
                "rescale requires a known input unit after binding.",
                kind=ErrorKind.INVALID_EXPRESSION_VALUE,
                expected="an expression with a known unit",
                received="unknown input unit",
                help_target="rescale",
                action="Declare the source quantity's unit before applying ms.rescale.",
            )
        scaled = _compose(
            recipe,
            (value, _Scalar(Just(recipe.divisor), recipe.divisor)),
            lambda parts: RescaleResolver(parts[0], recipe.divisor, recipe.unit),
            _consume,
            operation="truediv",
        )
        assert isinstance(scaled, (RowFacts, MetricFacts))
        return replace(scaled, unit=recipe.unit)
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
                item.coordinates,
                False,
                field=item,
                default_time=item.default_time,
            )
        return RowFacts(
            RowField(item.reference, item.root, item.unit, item.permission, read=item.field_read),
            item.root,
            item.unit,
            item.permission,
            item.coordinates,
            item.reference.ref.kind is SemanticKind.MEASURE,
            field=item,
            default_time=item.default_time,
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
        if declaration.reference is not None:
            value = replace(
                value,
                node=replace(
                    value.node,
                    origins=tuple(dict.fromkeys((*value.node.origins, declaration.reference))),
                ),
            )
        unit = value.unit if declaration.unit is None else declaration.unit
        permission = value.permission if declaration.permission is None else declaration.permission
        if unit == value.unit and permission == value.permission:
            return value

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
            _consume,
            operation="cases",
        )
    if isinstance(recipe, BinaryOperator):
        return _compose(
            recipe,
            (_consume(recipe.left), _consume(recipe.right)),
            lambda parts: BinaryOperator(recipe.func, *parts),
            _consume,
            operation=recipe.func.__name__,
        )
    if isinstance(recipe, UnaryOperator):
        return _compose(
            recipe,
            (_consume(recipe.arg),),
            lambda parts: UnaryOperator(recipe.func, parts[0]),
            _consume,
            operation=recipe.func.__name__,
        )
    if isinstance(recipe, Call):
        if isinstance(recipe.func, Attr):
            return _consume_method(recipe, recipe.func)
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
            _consume,
            operation=name,
        )
    _fail(
        "Unsupported Deferred expression shape.",
        expected="an admitted native numerical recipe",
        received=type(recipe).__name__,
    )
    raise AssertionError("unreachable")


def _inspect(value: Deferred | RecipeInput) -> DeferredInspection:
    try:
        result: DeferredInspection = inspect_deferred(
            _as_deferred(value) if isinstance(value, RecipeInput) else value
        )
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


def validate_closed_time_operation(recipe: Resolver) -> None:
    """Check a time operation as it is constructed from completed inputs."""
    if not isinstance(recipe, Call) or not isinstance(recipe.func, Attr):
        return

    def calendar_per(recipe: Resolver) -> bool:
        if (
            not isinstance(recipe, Call)
            or not isinstance(recipe.func, Attr)
            or attribute_name(recipe.func) != "per"
        ):
            return False
        sample = recipe.args[0] if recipe.args else recipe.kwargs.get("by", Just(None))
        return "grain" in recipe.kwargs or _calendar_sample_recipe(sample)

    name = attribute_name(recipe.func)
    if name == "per" and calendar_per(recipe):
        _consume(recipe)
    elif name == "window" or calendar_per(recipe.func.obj):
        metric = _consume(recipe)
        assert isinstance(metric, MetricFacts)
        validate_metric_time_consumers(metric.node, dict(metric._declared))


def consume_row(value: Deferred | RecipeInput) -> RowFacts:
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


def consume_metric(value: Deferred | RecipeInput, *, allow_boolean: bool = False) -> MetricFacts:
    result = _consume(_inspect(value).recipe)
    if not isinstance(result, MetricFacts) or (result.boolean and not allow_boolean):
        _fail(
            "Expected a completed Metric.",
            expected="a source or sample reduction and Metric arithmetic",
            received=type(result).__name__,
        )
    assert isinstance(result, MetricFacts)
    validate_metric_time_consumers(result.node, dict(result._declared))
    return result


def _rescale_disclosure(value: Deferred) -> tuple[tuple[str, str], ...]:
    """Derive input/output units from each already-bound rescale occurrence."""
    facts: list[tuple[str, str]] = []
    for use in _inspect(value).operations:
        if isinstance(use.recipe, RescaleResolver):
            source = _consume(use.recipe.recipe)
            assert isinstance(source, (RowFacts, MetricFacts))
            facts.append(
                (
                    "rescale",
                    f"input_unit={source.unit!r}, divisor={use.recipe.divisor!r}, "
                    f"unit={use.recipe.unit!r}",
                )
            )
    return tuple(facts)


def consume_field(value: Deferred | RecipeInput) -> SemanticInput:
    result = _consume(_inspect(value).recipe)
    if not isinstance(result, RowFacts) or result.field is None:
        _fail(
            "Expected a semantic field.",
            expected="a resolved field or valid via path",
            received=type(result).__name__,
        )
    assert isinstance(result, RowFacts) and result.field is not None  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
    return result.field


def _contains_population(recipe: Resolver) -> bool:
    if isinstance(recipe, PopulationContainsResolver):
        return True
    if isinstance(recipe, (DeclarationResolver, NamedResolver, RescaleResolver)):
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


def _consume_predicate_recipe(recipe: Resolver, *, coordinates: bool = False) -> PredicateFacts:
    inputs: list[RowExpression | PopulationContains] = []
    roots: dict[str, ExactRef] = {}

    def capture(item: Resolver) -> Resolver:
        if isinstance(item, PopulationContainsResolver):
            slot = Variable(f"slot_{len(inputs)}")
            inputs.append(PopulationContains(item.population._definition))
            return slot
        if (
            coordinates
            and isinstance(item, BinaryOperator)
            and item.func
            in (
                operator.and_,
                operator.or_,
            )
        ):
            return BinaryOperator(item.func, capture(item.left), capture(item.right))
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
            inputs.append(
                RowExpression(
                    row_recipe,
                    tuple(row_inputs),
                    value.node.origins if isinstance(value.node, RowExpression) else (),
                )
            )
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
    if not coordinates and len(roots) > 1:
        _fail(
            "Predicate field leaves belong to different contribution roots.",
            kind=ErrorKind.INVALID_WHERE_SCOPE,
            expected="field conditions on one contribution root",
            received=", ".join(roots),
            action="Bind fields through an explicit single-valued relationship to one root.",
            help_target="predicate",
        )
    return PredicateFacts(PredicateExpression(normalized, tuple(inputs)), tuple(roots.values()))


def consume_predicate(
    value: Deferred | RecipeInput, *, coordinates: bool = False
) -> PredicateFacts:
    """Capture the native boolean structure without reading or evaluating sources."""
    return _consume_predicate_recipe(_inspect(value).recipe, coordinates=coordinates)
