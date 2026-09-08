"""Isolated equal-argument backends shared by comparison and Attribution Runtime tests."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import ibis

from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.datasource.backends import BuiltDatasourceBackend, EffectiveDatasourceKwargs
from marivo.datasource.ir import DatasourceIR
from marivo.refs import MetricKind, Ref, ref
from tests.lazy_execution_fixtures import make_execution_registry

REVENUE = ref.metric("sales.revenue")


@contextmanager
def independent_sources(
    project: Path,
    *,
    metric: Ref[MetricKind] = REVENUE,
) -> Iterator[tuple[DatasetRuntime, LogicalMetricDataset, LogicalMetricDataset, list[int]]]:
    registry, sidecar = make_execution_registry(Path(":memory:"))
    runtime = DatasetRuntime.create(project, "independent-comparison")
    first = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    second = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    backends = [ibis.duckdb.connect(":memory:") for _ in range(2)]
    calls: list[int] = []
    try:
        for backend, amount in zip(backends, (11, 29), strict=True):
            backend.raw_sql(
                "CREATE TABLE orders (id BIGINT, tenant VARCHAR, customer_id BIGINT, "
                "order_id BIGINT, amount DOUBLE, weight DOUBLE, region VARCHAR, "
                'channel VARCHAR, day DATE, start DATE, "end" DATE)'
            )
            backend.con.execute("INSERT INTO orders (id, amount) VALUES (1, ?)", [amount])

        def supplied(
            datasource: DatasourceIR, effective: EffectiveDatasourceKwargs, *, read_only: bool
        ) -> BuiltDatasourceBackend:
            assert datasource.fields == {"path": ":memory:"}
            assert effective.kwargs == {"path": ":memory:"} and read_only
            selected = backends[len(calls)]
            calls.append(id(selected.con))
            return BuiltDatasourceBackend(selected, ())

        with patch.object(admission, "_build_backend_from_effective", supplied):
            yield runtime, first.observe(metric), second.observe(metric), calls
    finally:
        for backend in backends:
            backend.disconnect()
