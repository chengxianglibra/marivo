"""Real source preparation preserves distinct identity, filters, and time anchors."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import ibis
import pytest
from ibis.backends.mysql import Backend as MySQLBackend

from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.distinct import membership_validations
from marivo.analysis.compiler.nodes import RetainedRelationSpec
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.materialization.scalar_projection import project
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from tests.lazy_distinct_fixtures import (
    CHANNEL,
    guard_membership_transport,
    make_distinct_registry,
    seed_distinct_database,
)
from tests.lazy_execution_fixtures import ExecutionFixture, assert_compiled_validations
from tests.lazy_observation_fixtures import NoIoActionPort


@contextmanager
def _fixture(path: Path) -> Iterator[ExecutionFixture]:
    database = path / "distinct.duckdb"
    seed_distinct_database(database)
    registry, sidecar = make_distinct_registry(database)
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="session-distinct-compiler",
        store_id="store-distinct-compiler",
    )
    backend = ibis.duckdb.connect(str(database))
    try:
        yield ExecutionFixture(database, registry, sidecar, sources, backend)
    finally:
        backend.disconnect()


def _rows(dataset: LogicalDataset, fixture: ExecutionFixture) -> list[dict[str, object]]:
    compiled = compile_dataset(dataset, fixture.tables(dataset))
    with guard_membership_transport():
        assert_compiled_validations(compiled.validations)
        result = compiled.expression.select(*compiled.primary_columns).to_pyarrow()
    return [
        {name: result[name][index].as_py() for name in result.column_names}
        for index in range(len(result))
    ]


def test_composite_entity_distinct_retains_complete_struct_identity_in_source(
    tmp_path: Path,
) -> None:
    with _fixture(tmp_path) as fixture:
        metric = fixture.sources.observe(ref.metric("sales.distinct_composite")).aggregate()
        compiled = compile_dataset(metric, fixture.tables(metric))
        membership = next(
            part for part in compiled.retained_parts if isinstance(part, RetainedRelationSpec)
        )
        key_type = membership.expression["__mv_distinct_key"].type()
        assert key_type.is_struct() and tuple(key_type.names) == ("tenant", "id")
        with guard_membership_transport():
            assert_compiled_validations(compiled.validations)
            counts = (
                membership.expression.aggregate(membership_count=membership.expression.count())
                .to_pyarrow()
                .to_pylist()
            )
            assert counts == [{"membership_count": 3}]
            result = compiled.expression.select(*compiled.primary_columns).to_pyarrow()
        metric_name = next(
            field.name for field in metric.schema.columns if field.role_id == "metric"
        )
        assert result[metric_name].to_pylist() == [3]


@pytest.mark.parametrize("composite", [False, True])
def test_scalar_identity_membership_compiles_without_mysql_struct_rule(
    tmp_path: Path, composite: bool
) -> None:
    with _fixture(tmp_path) as fixture:
        observed = fixture.sources.observe(
            ref.metric("sales.distinct_composite" if composite else "sales.distinct_orders")
        )
        metric = (
            observed.aggregate() if composite else observed.with_dimensions(CHANNEL).aggregate()
        )
        compiled = compile_dataset(metric, fixture.tables(metric), scalar_identity_distinct=True)
        part = next(
            item for item in compiled.retained_parts if isinstance(item, RetainedRelationSpec)
        )
        expressions = (
            compiled.expression,
            part.expression,
            *(
                check.expression
                for check in membership_validations(
                    metric.row_contract,
                    compiled.expression,
                    {part.role: part.expression},
                )
            ),
        )
        backend = MySQLBackend()
        for expression in expressions:
            sql = backend.compile(project(expression).expression)
            assert "STRUCT(" not in sql.upper()
