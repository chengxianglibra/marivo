"""Author small independent inputs through real DuckDB and public Analysis APIs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Literal, TypeAlias

import ibis
import pyarrow as pa

import marivo.analysis as mv
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.session.core import session_from_runtime
from marivo.semantic._metric_expression import MetricExpression
from marivo.semantic.spi import NumericMetric, RowPredicate
from tests.formal.reference.generators import (
    PERIOD_END,
    PERIOD_START,
    BranchSpec,
    Fact,
    ObservationCase,
    SampleCase,
    calendar_day,
)
from tests.formal.reference.model import (
    Binary,
    Coalesce,
    Expression,
    IfElse,
    LiteralValue,
    Read,
    Unary,
)
from tests.shared_fixtures import catalog_value

_MODEL = """import marivo.datasource as md
import marivo.semantic as ms

facts = ms.entity(name="facts", datasource=ms.ref.datasource("warehouse"),
    source=md.table("facts"), primary_key=["id"])
key = ms.dimension_column(name="id", entity=facts, column="id")
group = ms.dimension_column(name="group_id", entity=facts, column="group_id")
lane = ms.dimension_column(name="lane", entity=facts, column="lane")
day = ms.time_dimension_column(name="day", entity=facts, column="day", parse=ms.date(timezone="UTC"))
amount = ms.measure_column(name="amount", entity=facts, column="amount", unit="USD", additivity=ms.additive_all())
extra = ms.measure_column(name="extra", entity=facts, column="extra", unit="USD", additivity=ms.additive_all())
score = ms.measure_column(name="score", entity=facts, column="score", unit="USD", additivity=ms.additive_all())
"""


def replace_facts(root: Path, facts: tuple[Fact, ...]) -> None:
    """Reload the isolated test relation while its consuming calls are closed."""
    backend = ibis.duckdb.connect(root / "facts.duckdb", threads=1)
    try:
        backend.create_table(
            "facts",
            pa.table(
                {
                    "id": pa.array([fact.identity for fact in facts], type=pa.int64()),
                    "group_id": pa.array([fact.group for fact in facts], type=pa.int64()),
                    "lane": pa.array([fact.lane for fact in facts], type=pa.int64()),
                    "day": pa.array(
                        [
                            calendar_day(fact.day).date() if fact.day is not None else None
                            for fact in facts
                        ],
                        type=pa.date32(),
                    ),
                    "amount": pa.array([fact.amount for fact in facts], type=pa.int64()),
                    "extra": pa.array([fact.extra for fact in facts], type=pa.int64()),
                    "score": pa.array(
                        [fact.score for fact in facts], type=pa.float64(), from_pandas=False
                    ),
                }
            ),
            overwrite=True,
        )
    finally:
        backend.disconnect()


def open_source(root: Path, facts: tuple[Fact, ...]) -> mv.Session:
    """Build one isolated declared source, with exact schema even for an empty bag."""
    root.mkdir(parents=True, exist_ok=True)
    replace_facts(root, facts)
    path = root / "facts.duckdb"
    (root / "marivo.toml").write_text('[project]\nname = "algebra-reference"\n', encoding="utf-8")
    datasource = root / "models" / "datasources" / "warehouse.py"
    datasource.parent.mkdir(parents=True)
    datasource.write_text(
        "import marivo.datasource as md\n"
        f"md.duckdb(name='warehouse', path={str(path)!r}, wall_clock_timezone='UTC')\n",
        encoding="utf-8",
    )
    model = root / "models" / "semantic" / "trial"
    model.mkdir(parents=True)
    (model / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='trial', owner='Test')\n",
        encoding="utf-8",
    )
    (model / "facts.py").write_text(_MODEL, encoding="utf-8")
    return session_from_runtime(DatasetRuntime.create(root, "algebra-reference", timezone="UTC"))


def source_metric(session: mv.Session, spec: BranchSpec) -> NumericMetric:
    """Capture the specified branch without consulting a reference result."""
    catalog = session.catalog
    measure = catalog_value(catalog, f"trial.facts.{spec.field}", kind="measure")
    lane = catalog_value(catalog, "trial.facts.lane", kind="dimension")
    day = catalog_value(catalog, "trial.facts.day", kind="time_dimension")
    condition: RowPredicate | None = lane == spec.lane if spec.lane is not None else None
    time = day if spec.timed else None
    nulls: Literal["error", "ignore"] = "error" if spec.strict else "ignore"
    reducer = {
        "sum": measure.sum,
        "count": measure.count,
        "min": measure.min,
        "max": measure.max,
        "mean": measure.mean,
    }[spec.reducer]
    return reducer(where=condition, time=time, nulls=nulls)


_NumericOperand: TypeAlias = NumericMetric | int | float | Decimal
_Predicate: TypeAlias = MetricExpression[Literal["boolean"]]
_BinaryOp: TypeAlias = Callable[[NumericMetric, _NumericOperand], NumericMetric]

_ARITHMETIC: Mapping[str, tuple[_BinaryOp, _BinaryOp]] = {
    "add": (MetricExpression.__add__, MetricExpression.__radd__),
    "subtract": (MetricExpression.__sub__, MetricExpression.__rsub__),
    "multiply": (MetricExpression.__mul__, MetricExpression.__rmul__),
    "divide": (MetricExpression.__truediv__, MetricExpression.__rtruediv__),
}


def _predicate(expression: Expression, metrics: Mapping[str, NumericMetric]) -> _Predicate:
    if isinstance(expression, Unary):
        if expression.operation == "not":
            return ~_predicate(expression.operand, metrics)
        value = _numeric(expression.operand, metrics)
        assert isinstance(value, MetricExpression)
        return value.isnull()
    assert isinstance(expression, Binary), expression
    if expression.operation == "and":
        return _predicate(expression.left, metrics) & _predicate(expression.right, metrics)
    if expression.operation == "or":
        return _predicate(expression.left, metrics) | _predicate(expression.right, metrics)
    assert expression.operation in ("lt", "eq"), expression
    left, right = _numeric(expression.left, metrics), _numeric(expression.right, metrics)
    if isinstance(left, MetricExpression):
        return left < right if expression.operation == "lt" else left == right
    assert isinstance(right, MetricExpression), expression
    return right > left if expression.operation == "lt" else right == left


def _numeric(expression: Expression, metrics: Mapping[str, NumericMetric]) -> _NumericOperand:
    if isinstance(expression, Read):
        return metrics[expression.name]
    if isinstance(expression, LiteralValue):
        assert isinstance(expression.value, (int, float, Decimal)) and not isinstance(
            expression.value, bool
        )
        return expression.value
    if isinstance(expression, IfElse):
        return _predicate(expression.condition, metrics).ifelse(
            _numeric(expression.yes, metrics), _numeric(expression.otherwise, metrics)
        )
    if isinstance(expression, Coalesce):
        left = _numeric(expression.left, metrics)
        assert isinstance(left, MetricExpression)
        return left.coalesce(_numeric(expression.right, metrics))
    assert isinstance(expression, Binary)
    forward, reverse = _ARITHMETIC[expression.operation]
    left, right = _numeric(expression.left, metrics), _numeric(expression.right, metrics)
    if isinstance(left, MetricExpression):
        return forward(left, right)
    assert isinstance(right, MetricExpression), expression
    return reverse(right, left)


def observe_case(session: mv.Session, case: ObservationCase) -> mv.AnalysisFrame:
    """Compile the compact test grammar through the authored observation surface."""
    inputs = {spec.name: source_metric(session, spec) for spec in case.branches}
    outputs: dict[str, NumericMetric] = {}
    for name, expression in case.outputs:
        value = _numeric(expression, inputs)
        assert isinstance(value, MetricExpression), expression
        outputs[name] = value
    group = catalog_value(session.catalog, "trial.facts.group_id", kind="dimension")
    if case.mode == "scalar":
        return session.observe(outputs)
    if case.mode == "calendar":
        scope = session.time_scope(start=PERIOD_START, end=PERIOD_END)
        return session.observe(outputs, by=(group, scope.by(mv.grain("day"))))
    if case.mode == "explicit":
        population = mv.coordinates(by=(group,), keys=tuple((key,) for key in case.population))
        return session.observe(outputs, by=population)
    return session.observe(outputs, by=(group,))


def observe_samples(session: mv.Session, case: SampleCase) -> mv.AnalysisFrame:
    """Reduce completed lane sums over captured implicit or explicit sample keys."""
    catalog = session.catalog
    group = catalog_value(catalog, "trial.facts.group_id", kind="dimension")
    lane = catalog_value(catalog, "trial.facts.lane", kind="dimension")
    metric = source_metric(session, BranchSpec("amount", "amount"))
    domain = (
        lane
        if case.lanes is None
        else mv.coordinates(by=(lane,), keys=tuple((key,) for key in case.lanes))
    )
    sample = metric.per(domain)
    reduce = {"sum": sample.sum, "mean": sample.mean, "min": sample.min, "max": sample.max}[
        case.outer
    ]
    return session.observe({"value": reduce()}, by=(group,))


def observe_ordered_samples(
    session: mv.Session, reducer: Literal["first", "last"], *, include_null: bool = False
) -> mv.AnalysisFrame:
    """Complete each civil day's sum before choosing its ordered endpoint."""
    catalog = session.catalog
    group = catalog_value(catalog, "trial.facts.group_id", kind="dimension")
    day = catalog_value(catalog, "trial.facts.day", kind="time_dimension")
    metric = source_metric(session, BranchSpec("amount", "amount", timed=True))
    samples = metric.per(day, grain=mv.grain("day"))
    endpoint = samples.first if reducer == "first" else samples.last
    scope = session.time_scope(start=PERIOD_START, end=PERIOD_END)
    return session.observe({"value": endpoint(include_null=include_null)}, by=(group,), where=scope)


def read_scores(
    session: mv.Session,
    *,
    lane_value: int | None = None,
    field: Literal["score", "amount"] = "score",
) -> mv.AnalysisFrame:
    """Read Float64 or int64 scores through an identity Population."""
    catalog = session.catalog
    entity = catalog_value(catalog, "trial.facts", kind="entity")
    lane = catalog_value(catalog, "trial.facts.lane", kind="dimension")
    score = catalog_value(catalog, f"trial.facts.{field}", kind="measure")
    population = session.members(
        entity, where=lane == lane_value if lane_value is not None else None
    )
    return session.read(population, {"score": score})
