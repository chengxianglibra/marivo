"""Independent source-column closure and exact binding regression cases."""

from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.normalize import required_source_dependencies
from marivo.analysis.materialization.admission import _declared_table
from marivo.analysis.operators.registry import implementation
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_observation_fixtures import NoIoActionPort


@pytest.mark.parametrize("engine", ["duckdb", "postgres", "mysql", "sqlite", "trino", "clickhouse"])
@pytest.mark.parametrize("hidden", [False, True])
def test_unused_complex_column_and_hidden_weight(engine: str, hidden: bool) -> None:
    registry, sidecar = make_execution_registry(Path("must-not-open"))
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    entity = replace(
        entity,
        source=replace(
            entity.source,
            columns=tuple(
                (name, replace(binding, data_type="array<string>") if name == "tenant" else binding)
                for name, binding in entity.source.columns
            ),
        ),
    )
    registry = replace(
        registry,
        entities={**registry.entities, entity.semantic_id: entity},
        datasources={
            name: replace(ds, backend_type=engine) for name, ds in registry.datasources.items()
        },
    )
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="dependencies",
        store_id="dependencies",
    )
    metrics = [ref.metric("sales.revenue")]
    if hidden:
        metrics.append(ref.metric("sales.weighted_amount"))
    target = sources.observe(metrics).aggregate().metric(ref.metric("sales.revenue"))
    dependencies = required_source_dependencies(target)
    assert len(dependencies.entries) == 1
    entry = dependencies.entries[0]
    assert {c.logical for c in entry.columns} == (
        {"id", "amount", "weight"} if hidden else {"id", "amount"}
    )
    assert implementation(target).for_backend(engine) is not None
    table = _declared_table(entry.entity, dependency=entry)
    assert "tenant" not in table.columns
    recipe = compile_dataset(target, {entry.entity.ref.path: table}, dependencies=dependencies)
    assert recipe.validations


@pytest.mark.parametrize("relation_shape", ["distinct", "same_table", "other_schema"])
def test_relationship_keys_and_aliases_are_owned_by_each_entity(relation_shape: str) -> None:
    registry, sidecar = make_execution_registry(Path("must-not-open"))
    original = registry.entities["sales.orders"]
    assert isinstance(original.source, TableSourceIR)
    entity = replace(
        original,
        source=replace(
            original.source,
            columns=tuple(
                (name, replace(binding, source="gross") if name == "amount" else binding)
                for name, binding in original.source.columns
            ),
        ),
    )
    entities = {**registry.entities, entity.semantic_id: entity}
    if relation_shape != "distinct":
        customer = entities["sales.customers"]
        assert isinstance(customer.source, TableSourceIR)
        entities[customer.semantic_id] = replace(
            customer,
            source=replace(
                customer.source,
                table="orders",
                database="other" if relation_shape == "other_schema" else None,
            ),
        )
    registry = replace(registry, entities=entities)
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="dependencies",
        store_id="dependencies",
    )
    target = (
        sources.observe(ref.metric("sales.revenue"))
        .with_dimensions(ref.dimension("sales.customers.region"))
        .aggregate()
    )
    entries = {e.entity.ref.path: e for e in required_source_dependencies(target).entries}
    assert {c.logical for c in entries["sales.orders"].columns} == {"id", "customer_id", "amount"}
    assert {c.logical for c in entries["sales.customers"].columns} == {"id", "region"}
    assert entries["sales.orders"].physical_columns == {"id", "customer_id", "gross"}
    assert entries["sales.customers"].physical_columns == {"id", "region"}

    if relation_shape == "other_schema":
        assert entries["sales.customers"].namespace == (None, "other")
        assert entries["sales.orders"].namespace == (None, None)


def test_expression_accesses_keep_argument_position() -> None:
    import ibis.expr.types as ir

    from marivo.semantic._expression_binding import ExpressionBody, expression_column_accesses

    def expression(left: ir.Table, right: ir.Table) -> ir.Value:
        return left.amount + right.amount

    body = ExpressionBody(
        callable=expression, body_ast_hash="test-owned", parameter_count=2, bindings=()
    )
    assert expression_column_accesses(body) == ((0, "amount"), (1, "amount"))


def test_unknown_expression_is_not_a_column_free_constant() -> None:
    import ibis
    import ibis.expr.types as ir

    from marivo.semantic._expression_binding import ExpressionBody, expression_column_accesses
    from marivo.semantic.errors import SemanticRuntimeError

    def opaque() -> ir.Value:
        return ibis.literal(1)

    def expression(table: ir.Table) -> ir.Value:
        return opaque()

    body = ExpressionBody(
        callable=expression, body_ast_hash="test-unknown", parameter_count=1, bindings=()
    )
    with pytest.raises(SemanticRuntimeError, match="unknown or unavailable expression dependency"):
        expression_column_accesses(body)


@pytest.mark.parametrize(
    "entity_name,expected", [("snapshots", {"id", "day"}), ("validity", {"id", "start", "end"})]
)
def test_version_assertion_columns_are_required(entity_name: str, expected: set[str]) -> None:
    from marivo.analysis import time_scope

    registry, sidecar = make_execution_registry(Path("must-not-open"))
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="dependencies",
        store_id="dependencies",
    )
    target = sources.population(
        ref.entity(f"sales.{entity_name}"),
        time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
    )
    entry = required_source_dependencies(target).entries[0]
    assert {column.logical for column in entry.columns} == expected


def test_dependency_owner_cannot_be_swapped() -> None:
    from marivo.analysis.compiler.errors import DatasetCompilationError

    registry, sidecar = make_execution_registry(Path("must-not-open"))
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="dependencies",
        store_id="dependencies",
    )
    target = sources.observe(ref.metric("sales.revenue")).aggregate()
    dependencies = required_source_dependencies(target)
    entry = dependencies.entries[0]
    foreign = replace(entry, owner=replace(entry.owner, session_id="foreign"))
    with pytest.raises(DatasetCompilationError, match="source binding mismatch"):
        compile_dataset(
            target,
            {entry.entity.ref.path: _declared_table(entry.entity, dependency=entry)},
            dependencies=replace(dependencies, entries=(foreign,)),
        )
