"""Deterministic display of closed semantic expressions without evaluation."""

from __future__ import annotations

from collections import Counter

from typing_extensions import assert_never

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    FieldCalendarSample,
    MeasureAssertion,
    MemberCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    PredicateExpression,
    RowExpression,
    RowField,
    RowNode,
    SampleDomainSpec,
    SourceAggregate,
    SourceWeightedMean,
    TimeBinding,
)
from marivo._definition.graph import direct_refs
from marivo._definition.relations import LiteralPopulation, PopulationDependency, SampleDomain
from marivo._definition.types import (
    AttributeTime,
    AxisBinding,
    CalendarCoordinate,
    ExactRef,
    IntervalCoordinate,
    NullPolicy,
    RowAttributeTime,
    SumAll,
    SumAllExcept,
    SumAxes,
    SumNone,
)
from marivo._definition.windows import (
    AllHistory,
    ObservationWindow,
    Since,
    ToDate,
    TrailingDuration,
    TrailingPeriods,
)
from marivo._temporal import Grain
from marivo.refs import Ref, SemanticKindTag


def _recipe_text(recipe: object, operands: tuple[str, ...]) -> str:
    """Render admitted native operations without exposing Marivo resolver records."""
    from ibis.common.deferred import (
        Attr,
        BinaryOperator,
        Call,
        Item,
        Just,
        Sequence,
        UnaryOperator,
        Variable,
    )

    from marivo._definition.deferred import RescaleResolver

    def render(value: object) -> str:
        if isinstance(value, RescaleResolver):
            return (
                f"rescale({render(value.recipe)}, divisor={value.divisor!r}, unit={value.unit!r})"
            )
        if isinstance(value, Variable):
            name = str(value.name)
            if name.startswith("slot_"):
                return operands[int(name.removeprefix("slot_"))]
            return name
        if isinstance(value, Just):
            literal = value.value
            return (
                getattr(literal, "__name__", type(literal).__name__)
                if callable(literal)
                else repr(literal)
            )
        if isinstance(value, BinaryOperator):
            operators = {
                "add": "+",
                "sub": "-",
                "mul": "*",
                "truediv": "/",
                "floordiv": "//",
                "pow": "**",
                "mod": "%",
                "eq": "==",
                "ne": "!=",
                "lt": "<",
                "le": "<=",
                "gt": ">",
                "ge": ">=",
                "and_": "&",
                "or_": "|",
                "xor": "^",
            }
            return f"({render(value.left)} {operators[value.func.__name__]} {render(value.right)})"
        if isinstance(value, UnaryOperator):
            operators = {"neg": "-", "pos": "+", "invert": "~"}
            return f"({operators[value.func.__name__]}{render(value.arg)})"
        if isinstance(value, Attr):
            attribute = value.name.value if isinstance(value.name, Just) else render(value.name)
            return f"{render(value.obj)}.{attribute}"
        if isinstance(value, Item):
            return f"{render(value.obj)}[{render(value.indexer)}]"
        if isinstance(value, Call):
            arguments = [
                *(render(item) for item in value.args),
                *(f"{name}={render(item)}" for name, item in value.kwargs.items()),
            ]
            return f"{render(value.func)}({', '.join(arguments)})"
        if isinstance(value, Sequence):
            return _tuple_text(tuple(render(item) for item in value.values))
        raise TypeError(f"Unsupported compiled formula node: {type(value).__name__}")

    return render(recipe)


def _suffixes(ref: Ref[SemanticKindTag]) -> tuple[str, ...]:
    parts = ref.path.split(".")
    return tuple(".".join(parts[index:]) for index in reversed(range(len(parts))))


def _tuple_text(items: tuple[str, ...]) -> str:
    return f"({', '.join(items)}{',' if len(items) == 1 else ''})"


def _null_argument(nulls: NullPolicy) -> tuple[str, ...]:
    return ('nulls="error"',) if nulls == "error" else ()


def _window_text(window: ObservationWindow) -> str:
    if isinstance(window, AllHistory):
        return "all_history()"
    if isinstance(window, Since):
        return f"since(start={window.start.isoformat()!r})"
    if isinstance(window, ToDate):
        return f"to_date(grain={window.grain._to_token()!r})"
    if isinstance(window, TrailingPeriods):
        return f"trailing_periods(count={window.count})"
    if isinstance(window, TrailingDuration):
        return f"trailing_duration(duration={str(window.duration)!r})"
    assert_never(window)


def _reduction_name(reducer: str, q: float | None = None) -> str:
    if q is not None:
        prefix = "approximate " if reducer == "approx_quantile" else ""
        return f"{prefix}quantile (q={q})"
    return {
        "mean": "arithmetic mean",
        "min": "minimum",
        "max": "maximum",
        "count": "non-NULL count",
        "count_distinct": "distinct non-NULL count",
        "approx_nunique": "approximate distinct non-NULL count",
        "approx_median": "approximate median",
        "first": "first value",
        "last": "last value",
    }.get(reducer, reducer)


class FormulaDisplay:
    """Render formula references consistently across one captured definition."""

    def __init__(self, *roots: MetricNode) -> None:
        refs = {item.ref for root in roots for item in direct_refs(root)}
        counts = Counter(suffix for ref in refs for suffix in _suffixes(ref))
        self._names = {
            ref: next((part for part in _suffixes(ref) if counts[part] == 1), ref.key)
            for ref in refs
        }

    def _name(self, ref: ExactRef) -> str:
        return self._names.get(ref.ref, ref.ref.key)

    def _read(self, ref: ExactRef, path: tuple[ExactRef, ...], at: AttributeTime | None) -> str:
        text = self._name(ref)
        if path:
            text += f".via({_tuple_text(tuple(map(self._name, path)))})"
        if at is not None:
            anchor = (
                self._name(at.field) if isinstance(at, RowAttributeTime) else repr(at.isoformat())
            )
            text += f".at({anchor})"
        return text

    def _time(self, binding: TimeBinding | None) -> str:
        return "time=" + (
            "None" if binding is None else self._read(binding.field, binding.path, binding.at)
        )

    def _axis(self, axis: AxisBinding | CalendarCoordinate | IntervalCoordinate) -> str:
        if isinstance(axis, CalendarCoordinate):
            return f"calendar({axis.grain!r}, timezone={axis.timezone!r})"
        if isinstance(axis, IntervalCoordinate):
            return f"interval({axis.start.isoformat()!r}, {axis.end.isoformat()!r})"
        if axis.field is None:
            return f"identity({self._read(axis.entity, axis.path, axis.at)})"
        return self._read(axis.field, axis.path, axis.at)

    def _population(self, population: PopulationDependency) -> str:
        axes = _tuple_text(tuple(map(self._axis, population.axes)))
        if isinstance(population, LiteralPopulation):
            return f"population({axes}, keys={len(population.keys)})"
        return f"population({axes})"

    def _samples(self, samples: SampleDomainSpec) -> str:
        if isinstance(samples, Grain):
            return f"grain({samples._to_token()!r})"
        if isinstance(samples, FieldCalendarSample):
            read = samples.field
            return (
                f"{self._read(read.field, read.path, read.at)}, "
                f"grain=grain({samples.grain._to_token()!r})"
            )
        if isinstance(samples, SampleDomain):
            axes = _tuple_text(tuple(map(self._axis, samples.axes)))
            parts = [axes]
            if samples.population is not None:
                parts.append(f"population={self._population(samples.population)}")
            for membership in samples.memberships:
                fields = _tuple_text(
                    tuple(
                        self._read(field.field, field.path, field.at) for field in membership.fields
                    )
                )
                paths = _tuple_text(
                    tuple(
                        _tuple_text(tuple(self._name(step.ref) for step in path))
                        for path in membership.paths
                    )
                )
                parts.append(
                    f"membership({fields}, paths={paths}, unmatched={membership.unmatched!r})"
                )
            return ", ".join(parts)
        if isinstance(samples, tuple):
            field_axes = tuple(map(self._axis, samples))
            return field_axes[0] if len(field_axes) == 1 else _tuple_text(field_axes)
        return self._population(samples)

    def _sample_scope(self, samples: SampleDomainSpec) -> str:
        if isinstance(samples, Grain):
            return f"each represented {samples._to_token()} bucket on the contribution clocks"
        if isinstance(samples, FieldCalendarSample):
            return (
                f"each represented {samples.grain._to_token()} bucket of "
                f"`{self._read(samples.field.field, samples.field.path, samples.field.at)}`"
            )
        if isinstance(samples, SampleDomain):
            if samples.population is not None:
                return f"each compatible sample in `{self._population(samples.population)}`"
            return f"each observed sample under `{self._samples(samples)}`"
        if isinstance(samples, tuple):
            if len(samples) == 1:
                return f"each observed `{self._axis(samples[0])}` value"
            return f"each observed combination of `{self._samples(samples)}`"
        return f"each compatible sample in `{self._population(samples)}`"

    def _assertion(self, node: MeasureAssertion) -> str:
        permission = node.permission
        arguments = [self.formula(node.operand)]
        if node.unit is not None:
            arguments.append(f"unit={node.unit!r}")
        if isinstance(permission, (SumAxes, SumAllExcept)):
            policy = "additive_over" if isinstance(permission, SumAxes) else "additive_except"
            arguments.append(f"{policy}{_tuple_text(tuple(map(self._name, permission.axes)))}")
        elif isinstance(permission, SumAll):
            arguments.append("additive_all()")
        elif isinstance(permission, SumNone):
            arguments.append("non_additive()")
        return f"assert_measure({', '.join(arguments)})"

    def formula(self, node: RowNode | MetricNode | PredicateExpression) -> str:
        """Render captured operators and semantic arguments as display notation."""
        if isinstance(node, RowField):
            read = node.read
            return (
                self._name(node.ref) if read is None else self._read(node.ref, read.path, read.at)
            )
        if isinstance(node, MeasureAssertion):
            return self._assertion(node)
        if isinstance(node, RowExpression):
            return _recipe_text(node.recipe, tuple(map(self.formula, node.inputs)))
        if isinstance(node, MetricExpression):
            return _recipe_text(node.recipe, tuple(map(self.formula, node.operands)))
        if isinstance(node, PredicateExpression):
            return _recipe_text(
                node.recipe,
                tuple(
                    self.formula(item)
                    if isinstance(item, RowExpression)
                    else f"contains({self._population(item.population)})"
                    for item in node.inputs
                ),
            )
        if isinstance(node, DeclaredMetricRef):
            return self._name(node.ref)
        if isinstance(node, SourceAggregate):
            arguments = (
                *((f"q={node.q}",) if node.q is not None else ()),
                self._time(node.time),
                *_null_argument(node.nulls),
            )
            return f"{self.formula(node.operand)}.{node.reducer}({', '.join(arguments)})"
        if isinstance(node, EntityCount):
            return f"{self._name(node.entity)}.count({self._time(node.time)})"
        if isinstance(node, SourceWeightedMean):
            arguments = (
                f"weight={self.formula(node.weight)}",
                self._time(node.time),
                *_null_argument(node.nulls),
            )
            return f"{self.formula(node.value)}.weighted_mean({', '.join(arguments)})"
        if isinstance(node, ContributionFilter):
            return f"{self.formula(node.operand)}.where({self.formula(node.predicate)})"
        if isinstance(node, PerReduce):
            sample_arguments = [
                *((f"q={node.q}",) if node.q is not None else ()),
                *_null_argument(node.nulls),
            ]
            if node.order_by is not None:
                sample_arguments.append(f"order_by={self._name(node.order_by)}")
            if node.include_null:
                sample_arguments.append("include_null=True")
            return (
                f"{self.formula(node.operand)}.per({self._samples(node.samples)})"
                f".{node.reducer}({', '.join(sample_arguments)})"
            )
        if isinstance(node, PerWeightedMean):
            arguments = (f"weight={self.formula(node.weight)}", *_null_argument(node.nulls))
            return (
                f"{self.formula(node.value)}.per({self._samples(node.samples)})"
                f".weighted_mean({', '.join(arguments)})"
            )
        if isinstance(node, MetricWindow):
            return f"{self.formula(node.operand)}.window({_window_text(node.rule)})"
        if isinstance(node, MemberCount):
            return f"{self._population(node.population)}.count()"
        assert_never(node)

    def _sample_reduction(self, node: PerReduce) -> str:
        reduction = _reduction_name(node.reducer, node.q)
        if node.reducer == "mean":
            reduction = f"equally weighted {reduction}"
        nulls = "non-NULL " if node.nulls == "ignore" and not node.include_null else ""
        order = f" in `{self._name(node.order_by)}` order" if node.order_by else ""
        included = (
            ", including NULL results" if node.include_null and node.nulls == "ignore" else ""
        )
        return f"the {reduction} of the {nulls}sample results{order}{included}"

    def _calculation(self, node: MetricNode) -> str:
        if isinstance(node, SourceAggregate):
            return f"the {_reduction_name(node.reducer, node.q)} of `{self.formula(node.operand)}`"
        if isinstance(node, SourceWeightedMean):
            return (
                f"the weighted mean of `{self.formula(node.value)}` "
                f"using `{self.formula(node.weight)}` as the weight for each valid row pair"
            )
        if isinstance(node, EntityCount):
            return f"the number of contributing `{self._name(node.entity)}` rows"
        if isinstance(node, ContributionFilter):
            return (
                f"{self._calculation(node.operand)} restricted to contributions "
                f"where `{self.formula(node.predicate)}` is true"
            )
        if isinstance(node, PerReduce):
            return (
                f"{self._sample_reduction(node)} obtained by evaluating "
                f"{self._calculation(node.operand)} for {self._sample_scope(node.samples)}"
            )
        if isinstance(node, PerWeightedMean):
            return (
                f"the weighted mean of `{self.formula(node.value)}` using "
                f"`{self.formula(node.weight)}` as the weight for each valid sample pair "
                f"from evaluating both Metrics for {self._sample_scope(node.samples)}"
            )
        if isinstance(node, MetricWindow):
            return (
                f"the result of reevaluating {self._calculation(node.operand)} for each "
                f"endpoint with `{_window_text(node.rule)}` on its clocked contributions"
            )
        if isinstance(node, MemberCount):
            return (
                f"the number of distinct coordinate tuples in `{self._population(node.population)}`"
            )
        if isinstance(node, DeclaredMetricRef):
            return f"the completed `{self._name(node.ref)}` Metric"
        if isinstance(node, MetricExpression):
            return f"`{self.formula(node)}` using the completed Metric operands"
        assert_never(node)

    def explanation(self, node: MetricNode) -> str:
        """Explain the calculation without inferring authored business meaning."""
        if isinstance(node, PerReduce):
            return (
                f"For {self._sample_scope(node.samples)}, evaluate {self._calculation(node.operand)}. "
                f"Then compute {self._sample_reduction(node)}."
            )
        return f"Compute {self._calculation(node)}."
