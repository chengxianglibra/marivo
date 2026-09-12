"""Small physical temporal sources shared by compiler and recovery acceptance."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from marivo.analysis.observation.temporal import ReportTimeAuthority
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.ir import SemanticParse
from tests.lazy_execution_fixtures import ExecutionFixture, execution_fixture
from tests.lazy_observation_fixtures import NoIoActionPort

AXIS = "sales.orders.order_time"


@contextmanager
def temporal_fixture(
    path: Path,
    *,
    physical: str = "TIMESTAMP",
    declared: str = "timestamp(6)",
    parse: SemanticParse | None = None,
    granularity: str = "second",
    report_zone: str = "Asia/Shanghai",
    values: tuple[str, ...] = ("2026-07-01 15:59:00", "2026-07-01 16:01:00"),
) -> Iterator[ExecutionFixture]:
    with execution_fixture(path) as fixture:
        fixture.backend.raw_sql("DELETE FROM orders")
        fixture.backend.raw_sql(
            f"ALTER TABLE orders ALTER day TYPE {physical} USING NULL::{physical}"
        )
        for index, value in enumerate(values, 1):
            fixture.backend.con.execute(
                "INSERT INTO orders (id, customer_id, day, amount) VALUES (?, 1, ?, ?)",
                [index, value, float(index)],
            )
        registry = replace(
            fixture.registry,
            entities=dict(fixture.registry.entities),
            dimensions=dict(fixture.registry.dimensions),
        )
        entity = registry.entities["sales.orders"]
        source = entity.source
        assert isinstance(source, TableSourceIR)
        registry.entities[entity.semantic_id] = replace(
            entity,
            source=replace(
                source,
                columns=tuple(
                    (name, replace(binding, data_type=declared) if name == "day" else binding)
                    for name, binding in source.columns
                ),
            ),
        )
        registry.dimensions[AXIS] = replace(
            registry.dimensions[AXIS], parse=parse, granularity=granularity
        )
        registry.freeze()
        sources = make_lazy_sources(
            semantic_registry=registry,
            sidecar=fixture.sidecar,
            action_port=NoIoActionPort(),
            session_id="temporal",
            store_id="temporal",
            report_time=ReportTimeAuthority(
                timezone=report_zone,
                resolution="fixed_offset" if report_zone.startswith(("UTC+", "UTC-")) else "iana",
            ),
        )
        yield replace(fixture, registry=registry, sources=sources)
