"""Check a finite semantic typing and ownership model against real capture and binding."""

import operator
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from itertools import product
from pathlib import Path
from typing import Literal, cast

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.types import DefinitionGraphError, InputContext, Owner, merge_input_contexts
from marivo.analysis.errors import AnalysisError
from marivo.semantic._deferred_semantics import consume_metric, consume_predicate, consume_row
from marivo.semantic._expression_values import _ExpressionCarrier
from marivo.semantic.errors import SemanticError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import SourceTrace

_MODEL = """import marivo.datasource as md
import marivo.semantic as ms
warehouse = ms.ref.datasource("warehouse")
alpha = ms.entity(name="alpha", datasource=warehouse, source=md.table("alpha"), primary_key=["id"])
beta = ms.entity(name="beta", datasource=warehouse, source=md.table("beta"), primary_key=["id"])
a = ms.measure_column(name="q", entity=alpha, column="q", unit="USD", additivity=ms.additive_all())
b = ms.measure_column(name="q", entity=beta, column="q", unit="USD", additivity=ms.additive_all())
euro = ms.measure_column(name="euro", entity=alpha, column="q", unit="EUR", additivity=ms.additive_all())
bucket = ms.dimension_column(name="bucket", entity=alpha, column="bucket")
alpha_time = ms.time_dimension_column(name="clock", entity=alpha, column="clock", parse=ms.timestamp())
beta_time = ms.time_dimension_column(name="clock", entity=beta, column="clock", parse=ms.timestamp())
"""


@dataclass(frozen=True)
class _Projects:
    session: mv.Session
    peer: mv.Session
    foreign: ms.SemanticCatalog


@pytest.fixture
def projects(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _Projects:
    roots = (tmp_path / "local", tmp_path / "foreign")
    for root in roots:
        root.mkdir()
        database = root / "facts.duckdb"
        with duckdb.connect(str(database)) as conn:
            conn.execute(
                """CREATE TABLE alpha(id BIGINT, q BIGINT, bucket VARCHAR, clock TIMESTAMPTZ);
                INSERT INTO alpha VALUES (1, 7, 'keep', '2026-02-02T08:00:00Z'),
                    (2, 100, 'keep', '2026-02-01T08:00:00Z'),
                    (3, 1000, 'skip', '2026-02-02T08:00:00Z');
                CREATE TABLE beta(id BIGINT, q BIGINT, clock TIMESTAMPTZ);
                INSERT INTO beta VALUES (1, 11, '2026-02-02T11:00:00Z'),
                    (2, 200, '2026-02-03T00:00:00Z')"""
            )
        semantic_project_factory(
            {
                "datasources/warehouse.py": (
                    "import marivo.datasource as md\n"
                    f"md.duckdb(name='warehouse', path={str(database)!r}, wall_clock_timezone='UTC')"
                ),
                "sales/_domain.py": (
                    "import marivo.semantic as ms\nms.domain(name='sales', owner='Test')"
                ),
                "sales/model.py": _MODEL,
            },
            workspace_dir=root,
            load=False,
        )
    monkeypatch.chdir(roots[0])
    return _Projects(
        mv.session.get_or_create("expression-formal", timezone="UTC"),
        mv.session.get_or_create("expression-peer", timezone="UTC"),
        ms.load(workspace_dir=roots[1]),
    )


_Kind = Literal["row", "metric", "row predicate", "metric predicate", "dimension", "number", "bool"]


@dataclass(frozen=True)
class _Operand:
    name: str
    value: _ExpressionCarrier | int | bool
    kind: _Kind
    root: str | None = None
    project: Path | None = None
    unit: str | None = None


def _operands(projects: _Projects) -> tuple[_Operand, ...]:
    catalog = projects.session.catalog
    values: list[_Operand] = []
    for name, other, root, unit in (
        ("alpha", catalog, "alpha", "USD"),
        ("beta", catalog, "beta", "USD"),
        ("euro", catalog, "alpha", "EUR"),
        ("foreign", projects.foreign, "alpha", "USD"),
    ):
        quantity = catalog_value(
            other, f"sales.{root}.{'euro' if name == 'euro' else 'q'}", kind="measure"
        )
        metric = quantity.sum()
        path = f"sales.{root}"
        project = other.workspace_dir
        values.extend(
            (
                _Operand(f"{name} row", quantity, "row", path, project, unit),
                _Operand(f"{name} metric", metric, "metric", path, project, unit),
                _Operand(f"{name} row predicate", quantity > 0, "row predicate", path, project),
                _Operand(f"{name} metric predicate", metric > 0, "metric predicate", path, project),
            )
        )
    dimension = catalog_value(catalog, "sales.alpha.bucket", kind="dimension")
    return (
        *values,
        _Operand(
            "categorical dimension", dimension, "dimension", "sales.alpha", catalog.workspace_dir
        ),
        _Operand("numeric literal", 2, "number"),
        _Operand("boolean literal", True, "bool"),
    )


@dataclass(frozen=True)
class _Judgment:
    kind: _Kind
    roots: set[str]
    unit: str | None
    project: Path


def _judgment(
    operands: tuple[_Operand, ...], *, stages: tuple[_Kind, ...], literal: _Kind
) -> _Judgment | None:
    """Infer the reference judgment from the finite operand descriptors alone."""
    fields = tuple(item for item in operands if item.kind != literal)
    stage_set = {item.kind for item in fields}
    projects = {item.project for item in fields}
    roots = {item.root for item in fields if item.root is not None}
    units = {item.unit for item in fields}
    if len(stage_set) != 1 or len(projects) != 1 or len(units) != 1:
        return None
    (stage,) = stage_set
    (project,) = projects
    if stage not in stages or project is None or (stage.startswith("row") and len(roots) != 1):
        return None
    return _Judgment(stage, roots, next(iter(units)), project)


def _inspect(value: object, expected: _Judgment) -> tuple[set[str], str | None, Path | None]:
    if expected.kind in ("row", "row predicate"):
        assert isinstance(value, ms.RowExpression), type(value)
        if expected.kind == "row":
            facts = consume_row(value)
            return {facts.root.ref.path}, facts.unit, value._context.project_root
        return (
            {root.ref.path for root in consume_predicate(value).roots},
            None,
            value._context.project_root,
        )
    assert isinstance(value, ms.MetricExpression), type(value)
    metric = consume_metric(value, allow_boolean=True)
    assert metric.boolean == (expected.kind == "metric predicate")
    return {root.ref.path for root in metric.roots}, metric.unit, value._context.project_root


def _check(
    action: Callable[[], object], expected: _Judgment | None, *, context: tuple[str, ...]
) -> None:
    try:
        value = action()
        # A lazy typed carrier can defer root or unit rejection until facts are consumed.
        if isinstance(value, ms.RowExpression):
            if value._role == "boolean":
                consume_predicate(value)
            else:
                consume_row(value)
        elif isinstance(value, ms.MetricExpression):
            consume_metric(value, allow_boolean=True)
    except SemanticError as error:
        assert expected is None, (context, expected, error)
        assert error.expected and error.received and error.repair is not None, (context, error)
    else:
        assert expected is not None, (context, "unexpectedly admitted", value)
        actual = _inspect(value, expected)
        assert actual == (expected.roots, expected.unit, expected.project), (
            context,
            expected,
            actual,
        )


def _apply(
    operation: Callable[..., object], left: _Operand, right: _Operand, expected: _Judgment | None
) -> None:
    # Deliberately call the normal Python operators on ill-typed pairs as well.
    _check(
        lambda: operation(left.value, right.value),
        expected,
        context=(operation.__name__, left.name, right.name),
    )


def test_exhaustive_binary_stage_and_scope_judgments(projects: _Projects) -> None:
    values = _operands(projects)
    for left, right in product(values, repeat=2):
        if left.project is None and right.project is None:
            continue
        operands = (left, right)
        for operation in (operator.add, operator.sub):
            _apply(
                operation,
                left,
                right,
                _judgment(operands, stages=("row", "metric"), literal="number"),
            )
        for operation in (operator.and_, operator.or_):
            _apply(
                operation,
                left,
                right,
                _judgment(operands, stages=("row predicate", "metric predicate"), literal="bool"),
            )


def test_exhaustive_numeric_conditional_stage_judgments(projects: _Projects) -> None:
    values = _operands(projects)
    branches = tuple(item for item in values if item.kind in ("row", "metric", "number"))
    for condition in values:
        if condition.kind == "row predicate":
            stage: _Kind = "row"
        elif condition.kind == "metric predicate":
            stage = "metric"
        else:
            continue
        for yes, no in product(branches, repeat=2):
            fields = tuple(branch for branch in (yes, no) if branch.kind != "number")
            roots = {item.root for item in (condition, *fields) if item.root is not None}
            units = {item.unit for item in fields}
            project = condition.project
            assert project is not None
            expected = (
                _Judgment(stage, roots, next(iter(units), None), project)
                if all(branch.kind == stage and branch.project == project for branch in fields)
                and len(units) <= 1
                and (stage == "metric" or len(roots) == 1)
                else None
            )
            assert isinstance(condition.value, (ms.RowExpression, ms.MetricExpression))
            # The negative cases intentionally pass branches of the wrong public type.
            conditional = cast("Callable[[object, object], object]", condition.value.ifelse)
            _check(
                partial(conditional, yes.value, no.value),
                expected,
                context=("ifelse", condition.name, yes.name, no.name),
            )


def test_exhaustive_context_merge_preserves_exact_owner_and_project(projects: _Projects) -> None:
    local = projects.session.catalog.workspace_dir
    foreign = projects.foreign.workspace_dir
    owner = projects.session._runtime.local.owner
    peer = projects.peer._runtime.local.owner
    # Exercise an equal Session ID with a different Store as well as a different Session.
    other_store = Owner(owner.session_id, "another_store")
    contexts = tuple(
        InputContext(root, selected)
        for root, selected in product((None, local, foreign), (None, owner, peer, other_store))
    )
    for triple in product(contexts, repeat=3):
        roots = {item.project_root for item in triple if item.project_root is not None}
        owners = {item.owner for item in triple if item.owner is not None}
        if len(roots) > 1 or len(owners) > 1:
            with pytest.raises(DefinitionGraphError) as result:
                merge_input_contexts(*triple)
            assert result.value.kind == "ownership_conflict", triple
        else:
            assert merge_input_contexts(*triple) == InputContext(
                next(iter(roots), None), next(iter(owners), None)
            ), triple


def test_receiver_checks_session_root_and_clock_before_reads(
    projects: _Projects, source_trace: SourceTrace
) -> None:
    session = projects.session
    catalog = session.catalog
    alpha = catalog_value(catalog, "sales.alpha", kind="entity")
    a = catalog_value(catalog, "sales.alpha.q", kind="measure")
    a_clock = catalog_value(catalog, "sales.alpha.clock", kind="time_dimension")
    b = catalog_value(catalog, "sales.beta.q", kind="measure")
    b_clock = catalog_value(catalog, "sales.beta.clock", kind="time_dimension")
    bucket = catalog_value(catalog, "sales.alpha.bucket", kind="dimension")

    with pytest.raises(SemanticError):
        session.observe({"q": a.sum(time=b_clock)})
    with pytest.raises(SemanticError):
        session.observe({"q": b.sum().where(bucket == "keep")})
    with pytest.raises(AnalysisError):
        session.observe({"q": a.sum()}, where=mv.in_population(projects.peer.members(alpha)))
    foreign = catalog_value(projects.foreign, "sales.alpha.q", kind="measure")
    with pytest.raises(SemanticError):
        session.observe({"q": foreign.sum()})
    samples = a.sum(time=a_clock).per(bucket)
    with pytest.raises(AnalysisError):
        session.observe({"q": samples})  # type: ignore[dict-item]  # A sample domain is unfinished.
    assert source_trace.native_sql == []

    selected = a.sum(time=a_clock, where=bucket == "keep")
    total = selected + b.sum(time=b_clock) + selected
    scope = session.time_scope(start="2026-02-02", end="2026-02-03")
    assert session.observe({"q": total}, where=scope).compute().to_pandas()["q"].tolist() == [25]
    membership = mv.in_population(session.members(alpha, where=bucket == "keep"))
    same_session = session.observe({"q": a.sum(time=a_clock)}, where=(scope, membership))
    assert same_session.compute().to_pandas()["q"].tolist() == [7]
    assert session.observe({"q": samples.mean()}, where=scope).compute().to_pandas()[
        "q"
    ].tolist() == [
        503.5,
    ]
