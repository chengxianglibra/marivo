"""Exercise Population rewrites against independently known complete key domains."""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from itertools import product
from pathlib import Path

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition import relations as population_definitions
from marivo._definition.query import ObserveQuery, PopulationValues
from marivo._definition.relations import PopulationProduct, PopulationProgram
from marivo.analysis.compiler.predicate import population_key
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.compiler.query_stages import FrameCompute, LocalCompute
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from marivo.analysis.materialization import graph_local_execution
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.analysis.materialization.query_sources import open_query_sources
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_population_membership import _MODEL, _session
from tests.shared_fixtures import catalog_value
from tests.support.optimization import compute_with_policy
from tests.support.source_trace import capture_source

_POLICY = OptimizationPolicy.default()
_EXTENDED = (
    _MODEL
    + """
day = ms.time_dimension_column(name="day", entity=orders, column="day")
parent_id = ms.dimension_column(name="parent_id", entity=orders, column="parent_id")
fallible = ms.dimension_column(name="fallible", entity=orders, column="fallible")
self_key = ms.relationship(name="self_key", from_entity=orders, to_entity=orders, keys=[ms.join_on(oid, oid)])
parent_key = ms.relationship(name="parent_key", from_entity=orders, to_entity=orders, keys=[ms.join_on(parent_id, oid)])
chosen_tag = ms.relationship(name="chosen_tag", from_entity=orders, to_entity=tags, keys=[ms.join_on(customer, tid)])
"""
)


def _mutate_fixture(path: Path, *statements: str) -> None:
    database = ibis.duckdb.connect(path)
    try:
        for statement in statements:
            database.raw_sql(statement)
    finally:
        database.disconnect()


@pytest.fixture
def session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    result = _session(semantic_project_factory, tmp_path, monkeypatch, model=_EXTENDED)
    _mutate_fixture(
        tmp_path / "population.duckdb",
        "ALTER TABLE orders ADD day DATE",
        "ALTER TABLE orders ADD parent_id BIGINT",
        "UPDATE orders SET day = DATE '2026-08-01' + CAST(id - 1 AS INTEGER)",
        "UPDATE orders SET parent_id = CASE id WHEN 1 THEN 2 WHEN 2 THEN 999 END",
    )
    return result


@contextmanager
def _prepared(
    session: Session, frame: mv.AnalysisFrame, policy: OptimizationPolicy
) -> Iterator[PreparedSources]:
    backing = frame._backing
    assert isinstance(backing, ExpressionFrameBacking) and isinstance(
        backing.root, SourceFrameBinding
    )
    with (
        session._runtime.connection_service().operation() as connection,
        open_query_sources(
            connection, backing.root.graph, session.catalog._state, policy=policy
        ) as prepared,
    ):
        yield prepared


def _cross_joins(table: ir.Table) -> int:
    return sum(
        link.how == "cross" for chain in table.op().find(ops.JoinChain) for link in chain.rest
    )


def test_calendar_and_identity_factorization_avoids_joint_fact_witness(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = catalog_value(session.catalog, "sales.orders", kind="entity")
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="dimension")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
    dates = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    chosen = session.members(order, where=customer == 1)
    query = session.observe(
        {"matched": amount.sum(time=day)},
        where=mv.in_population(mv.product(chosen, dates)),
    )
    off = _POLICY.with_rule("population_product_factorization", enabled=False)
    with _prepared(session, query, off) as baseline, _prepared(session, query, _POLICY) as active:
        assert _cross_joins(baseline.relations["read:0"]) == 1
        assert _cross_joins(active.relations["read:0"]) == 0
        assert any(
            item.rule == "population_product_factorization" and item.status == "adopted"
            for item in active.population_decisions
        )
    assert compute_with_policy(query, off).to_pandas()["matched"].tolist() == [40]
    original = graph_local_execution.run_local

    def observe(
        stage: LocalCompute | FrameCompute,
        inputs: tuple[StageOutput, ...],
        kernel: graph_local_execution.LocalKernel | None = None,
        *,
        report_domain: pd.DataFrame | None = None,
        sample_domains: Mapping[tuple[int, ...], pd.DataFrame] | None = None,
        policy: OptimizationPolicy = _POLICY,
    ) -> StageOutput:
        if isinstance(stage, LocalCompute) and isinstance(stage.program, ResultProgram):
            op = stage.program.operation
            if isinstance(op, PopulationValues) and isinstance(op.definition, PopulationProgram):
                assert not isinstance(op.definition.root, PopulationProduct)
        return original(
            stage,
            inputs,
            kernel,
            report_domain=report_domain,
            sample_domains=sample_domains,
            policy=policy,
        )

    monkeypatch.setattr(graph_local_execution, "run_local", observe)
    trace = capture_source(monkeypatch)
    assert query.compute().to_pandas()["matched"].tolist() == [40]
    assert not any("CROSS JOIN" in statement for statement in trace.native_sql)


def test_shared_fanout_keeps_incompatible_witnesses_joint(session: Session) -> None:
    order_links = catalog_value(session.catalog, "sales.order_links", kind="relationship")
    link_tags = catalog_value(session.catalog, "sales.link_tags", kind="relationship")
    revenue = catalog_value(session.catalog, "sales.revenue", kind="metric")
    link = mv.coordinates(by=(mv.identity(mv.path(order_links)),), keys=(((1,),),))
    tag = mv.coordinates(by=(mv.identity(mv.path(order_links, link_tags)),), keys=(((2,),),))
    query = session.observe({"value": revenue}, where=mv.in_population(mv.product(link, tag)))
    # Order 1 has both link 1 and tag 2, but link 1 points to tag 1.
    assert query.compute().to_pandas()["value"].tolist() == [0]
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="dimension")
    selected = mv.coordinates(by=(customer,), keys=((1,),))
    with_outer_factor = session.observe(
        {"value": revenue}, where=mv.in_population(mv.product(link, selected, tag))
    )
    assert with_outer_factor.compute().to_pandas()["value"].tolist() == [0]
    with _prepared(session, with_outer_factor, _POLICY) as prepared:
        assert any(
            item.rule == "population_product_factorization" and item.status == "adopted"
            for item in prepared.population_decisions
        )
    with _prepared(session, query, _POLICY) as prepared:
        assert any(
            item.rule == "population_product_factorization" and item.status == "refused"
            for item in prepared.population_decisions
        )


@pytest.mark.parametrize("empty_first", (False, True))
def test_empty_factor_retains_original_identity_check(session: Session, empty_first: bool) -> None:
    order = catalog_value(session.catalog, "sales.orders", kind="entity")
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="dimension")
    revenue = catalog_value(session.catalog, "sales.revenue", kind="metric")
    _mutate_fixture(
        session.project_root / "population.duckdb",
        "INSERT INTO orders VALUES (NULL,1,25,DATE '2026-08-01',NULL)",
    )
    empty = mv.coordinates(by=(customer,), keys=())
    checked = session.members(order)
    factors = (empty, checked) if empty_first else (checked, empty)
    query = session.observe({"value": revenue}, where=mv.in_population(mv.product(*factors)))
    for policy in (_POLICY, _POLICY.with_rule("population_product_factorization", enabled=False)):
        with pytest.raises(MaterializationError, match="non-null complete identity"):
            compute_with_policy(query, policy)


def test_round_trip_requires_exact_reflexive_primary_key(session: Session) -> None:
    order = catalog_value(session.catalog, "sales.orders", kind="entity")
    self_key = catalog_value(session.catalog, "sales.self_key", kind="relationship")
    parent_key = catalog_value(session.catalog, "sales.parent_key", kind="relationship")
    selected = session.members(order)
    for relationship, expected, adopted in (
        (self_key, [1, 2, 3], True),
        (parent_key, [1, 2], False),
    ):
        via = mv.path(relationship)
        reached = selected.image(via=via)
        retained = selected.preimage(reached, via=via)
        frame = session.frame(retained)
        baseline = compute_with_policy(
            frame, _POLICY.with_rule("L3_left_total_elimination", enabled=False)
        ).to_pandas()
        actual = frame.compute().to_pandas()
        pd.testing.assert_frame_equal(actual, baseline)
        assert actual.iloc[:, 0].tolist() == expected
        with _prepared(session, frame, _POLICY) as prepared:
            assert (
                any(
                    decision.rule == "L3_left_total_elimination"
                    for decision in prepared.population_decisions
                )
                is adopted
            )

    customer = catalog_value(session.catalog, "sales.orders.customer", kind="dimension")
    via_self = mv.path(self_key)
    chosen_targets = selected.image(via=via_self).where(
        mv.in_population(session.members(order, where=customer == 2))
    )
    assert isinstance(chosen_targets, mv.Population)
    filtered = session.frame(selected.preimage(chosen_targets, via=via_self))
    assert filtered.compute().to_pandas().iloc[:, 0].tolist() == [3]
    with _prepared(session, filtered, _POLICY) as prepared:
        assert all(d.rule != "L3_left_total_elimination" for d in prepared.population_decisions)


def test_classify_postpones_direct_scalar_and_retains_identity_failure(session: Session) -> None:
    order = catalog_value(session.catalog, "sales.orders", kind="entity")
    oid = catalog_value(session.catalog, "sales.orders.id", kind="dimension")
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="dimension")
    parent = catalog_value(session.catalog, "sales.parent_key", kind="relationship")
    base = session.members(order).classify(by=(customer,))
    filtered = base.classify(by=(oid,)).where(customer == 1)
    assert isinstance(filtered, mv.Population)
    frame = session.frame(filtered)
    assert frame.compute().to_pandas().iloc[:, 0].tolist() == [1, 2]
    with _prepared(session, frame, _POLICY) as prepared:
        assert any(d.rule == "classify_after_filter" for d in prepared.population_decisions)
    identity = base.classify(by=(mv.identity(mv.path(parent)),)).where(customer == 1)
    assert isinstance(identity, mv.Population)
    failure = session.frame(identity)
    with _prepared(session, failure, _POLICY) as prepared:
        assert prepared.graph is not None and isinstance(prepared.graph.root, ObserveQuery)
        domain = prepared.graph.root.population
        assert domain is not None
        (check,) = [
            check
            for check in prepared.population_checks[population_key(domain)]
            if check.expected == "a non-null complete identity for every input tuple"
        ]
        bad_keys = check.relation.filter(check.relation[check.flag]).select(*check.keys)
        assert bad_keys.to_pyarrow()[check.keys[0]].to_pylist() == [3]
    with pytest.raises(MaterializationError, match="complete identity for every input tuple"):
        failure.compute()


def test_classify_refuses_fallible_dimension_in_view(session: Session) -> None:
    _mutate_fixture(
        session.project_root / "population.duckdb",
        "ALTER TABLE orders RENAME TO raw_orders",
        "CREATE VIEW orders AS SELECT *, CASE WHEN customer = 2 "
        "THEN CAST('bad' || CAST(id AS VARCHAR) AS BIGINT) ELSE 7 END AS fallible "
        "FROM raw_orders",
    )
    order = catalog_value(session.catalog, "sales.orders", kind="entity")
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="dimension")
    fallible = catalog_value(session.catalog, "sales.orders.fallible", kind="dimension")
    population = session.members(order).classify(by=(customer,)).classify(by=(fallible,))
    filtered = population.where(customer == 1)
    assert isinstance(filtered, mv.Population)
    frame = session.frame(filtered)
    off = _POLICY.with_rule("classify_after_filter", enabled=False)
    for policy in (off, _POLICY):
        with _prepared(session, frame, policy) as prepared:
            assert all(d.rule != "classify_after_filter" for d in prepared.population_decisions)
        with pytest.raises(MaterializationError, match="bad3") as failure:
            compute_with_policy(frame, policy)
        assert failure.value.stage == "source_execution"


@pytest.mark.parametrize("shape", ("pair", "nested_left", "nested_right"))
def test_literal_factorization_avoids_cartesian_key_enumeration(
    session: Session, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="dimension")
    day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    selected = mv.coordinates(by=(customer,), keys=tuple((key,) for key in range(1, 8)))
    dates = session.time_scope(start="2026-08-01", end="2026-08-06").by(mv.grain("day"))
    orders = catalog_value(session.catalog, "sales.orders", kind="entity")
    ids = mv.coordinates(by=(mv.identity(orders),), keys=tuple(((key,),) for key in (1, 2, 3)))
    domain = (
        mv.product(mv.product(ids, selected), dates)
        if shape == "nested_left"
        else mv.product(ids, mv.product(selected, dates))
        if shape == "nested_right"
        else mv.product(selected, dates)
    )
    frame = session.observe({"matched": amount.sum(time=day)}, where=mv.in_population(domain))
    sizes: list[tuple[int, ...]] = []

    def record(*factors: tuple[object, ...]) -> Iterator[tuple[object, ...]]:
        sizes.append(tuple(len(factor) for factor in factors))
        return product(*factors)

    monkeypatch.setattr(population_definitions, "cartesian_product", record)
    trace = capture_source(monkeypatch)
    assert frame.compute().to_pandas()["matched"].tolist() == [140]
    assert sizes == []
    assert not any("CROSS JOIN" in sql for sql in trace.native_sql)


@pytest.mark.parametrize("empty", (True, False))
def test_factored_check_preserves_foreign_source_and_original_axes(
    session: Session, empty: bool
) -> None:
    tags = catalog_value(session.catalog, "sales.tags", kind="entity")
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="dimension")
    day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    _mutate_fixture(
        session.project_root / "population.duckdb",
        "INSERT INTO tags VALUES (NULL, 'unrelated', 'x')",
    )
    independent = (
        mv.coordinates(by=(customer,), keys=())
        if empty
        else session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    )
    frame = session.observe(
        {"matched": amount.sum(time=day)},
        where=mv.in_population(mv.product(session.members(tags), independent)),
    )
    off = _POLICY.with_rule("population_product_factorization", enabled=False)
    with pytest.raises(MaterializationError) as original:
        compute_with_policy(frame, off)
    with pytest.raises(MaterializationError) as factored:
        frame.compute()
    assert original.value.stage == factored.value.stage == "population_identity"
    assert factored.value.expected == "non-null complete identity keys"
    assert factored.value.received == original.value.received
    assert "sales.tags; source=population:" in (factored.value.received or "")
    assert "{'population.0': None}" in (factored.value.received or "")
