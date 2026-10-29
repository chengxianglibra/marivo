"""Ordinary previews prepare remote deadlines before resolving their source."""

from __future__ import annotations

import textwrap
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock, Mock

import ibis
import ibis.expr.types as ir
import pandas as pd
import pytest
from ibis.backends import BaseBackend

import marivo.datasource as md
from marivo.datasource import backends
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.errors import DatasourceSourceCapabilityError
from marivo.datasource.inspection import (
    ExecutionCapabilities,
    Partitioning,
    PhysicalExtent,
    SourceInspection,
)
from marivo.datasource.ir import (
    DatasourceIR,
    EntitySourceIR,
    QueryParamScalar,
    QueryParamScalarList,
)
from marivo.datasource.metadata import ColumnMetadata
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.source import AuthoringScope, TableSource
from marivo.preview import PreviewResult
from marivo.refs import ref as ref_factory
from marivo.semantic import catalog as catalog_module
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticError
from marivo.semantic.reader import SemanticProject

_BackendKind = Literal["trino", "postgres", "mysql", "clickhouse"]
_PreviewPath = Literal["single", "rows", "metrics"]
_ProjectFactory = Callable[[dict[str, str]], SemanticProject]


@dataclass
class _Driver:
    session_properties: dict[str, object]
    autocommit: bool
    identity: int
    read_only: bool = False
    rollbacks: int = 0
    params: dict[str, object] = field(default_factory=dict)

    def thread_id(self) -> int:
        return self.identity

    def fileno(self) -> int:
        return 17

    def rollback(self) -> None:
        self.rollbacks += 1


@dataclass
class _Probe:
    catalog: SemanticCatalog
    backend_kind: _BackendKind
    connections: DatasourceConnectionService
    created: list[Mock] = field(default_factory=list)
    drivers: list[_Driver] = field(default_factory=list)
    connect_kwargs: list[dict[str, object]] = field(default_factory=list)
    resolved: list[tuple[int | None, bool]] = field(default_factory=list)
    collected: list[tuple[str, int]] = field(default_factory=list)
    sockets: list[MagicMock] = field(default_factory=list)
    active_backend: BaseBackend | None = None
    failure: Literal["bind", "read"] | None = None
    corrupt_configuration: bool = False


@contextmanager
def _probe(
    backend_kind: _BackendKind,
    semantic_project_factory: _ProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_Probe]:
    declaration = (
        'md.trino(name="warehouse", host="localhost", catalog="memory", '
        'user_env="MARIVO_PREVIEW_TEST_USER")'
        if backend_kind == "trino"
        else f'md.{backend_kind}(name="warehouse", host="localhost", database="preview", '
        'user_env="MARIVO_PREVIEW_TEST_USER")'
    )
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\n{declaration}\n",
            "sales/_domain.py": (
                'import marivo.semantic as ms\nms.domain(name="sales", owner="Data", default=True)\n'
            ),
            "sales/orders.py": textwrap.dedent(
                """                import marivo.datasource as md
                import marivo.semantic as ms
                orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
                refunds = ms.entity(name="refunds", datasource=ms.ref.datasource("warehouse"), source=md.table("refunds"))
                region = ms.dimension_column(name="region", entity=orders, column="region")
                amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="USD")
                refund_amount = ms.measure_column(name="amount", entity=refunds, column="amount", additivity=ms.additive_all(), unit="USD")
                amount_value = amount
                @ms.metric()
                def revenue():
                    return amount_value.sum()
                @ms.metric()
                def net_revenue():
                    return amount_value.sum()
                """
            ),
        }
    )
    probe = _Probe(
        SemanticCatalog(project), backend_kind, DatasourceConnectionService(project.workspace_dir)
    )
    profile = require_profile_for_backend_type(backend_kind)
    timeout = profile.native_query_timeout
    assert timeout is not None
    monkeypatch.setenv("MARIVO_PREVIEW_TEST_USER", "preview_reader")

    def connect(_name: str, kwargs: Mapping[str, object]) -> BaseBackend:
        properties = kwargs.get("session_properties", {})
        assert isinstance(properties, dict)
        driver = _Driver(
            dict(properties), kwargs.get("autocommit") is not False, len(probe.created) + 1
        )
        backend = Mock(spec=BaseBackend)
        backend.name = backend_kind
        backend.con = driver

        probe.created.append(backend)
        probe.drivers.append(driver)
        probe.connect_kwargs.append(dict(kwargs))
        return backend

    @contextmanager
    def guarded(backend: BaseBackend, seconds: int) -> Iterator[None]:
        if probe.corrupt_configuration:
            backend._marivo_terminal_timeout_seconds = seconds + 1
        with timeout(backend, seconds):
            assert probe.active_backend is None
            probe.active_backend = backend
            try:
                yield
            finally:
                probe.active_backend = None

    prepared_profile = replace(profile, connect=connect, native_query_timeout=guarded)
    monkeypatch.setattr(
        backends, "require_profile_for_backend_type", lambda _kind: prepared_profile
    )
    monkeypatch.setattr(
        catalog_module, "require_profile_for_backend_type", lambda _kind: prepared_profile
    )
    service = probe.connections
    monkeypatch.setattr(
        probe.catalog._project, "_connection_operation", lambda: nullcontext(service)
    )

    def bind(
        name: str,
        identity: str,
        datasource: DatasourceIR,
        source: EntitySourceIR,
        source_params: dict[str, QueryParamScalar | QueryParamScalarList] | None = None,
    ) -> ir.Table:
        backend = service.session_backend(name)
        probe.resolved.append(
            (
                getattr(backend, "_marivo_terminal_timeout_seconds", None),
                probe.active_backend is backend,
            )
        )
        if probe.failure == "bind":
            raise RuntimeError("source bind failed")
        return ibis.table(
            {"order_id": "int64", "amount": "float64", "region": "string"}, name=identity
        )

    def collect(name: str, expression: ir.Table, *, purpose: str, max_rows: int) -> pd.DataFrame:
        assert probe.active_backend is service.session_backend(name)
        probe.collected.append((purpose, max_rows))
        if probe.failure == "read":
            raise RuntimeError("source read failed")
        return pd.DataFrame(
            {
                column: ["east" if dtype.is_string() else 12.0 if dtype.is_floating() else 1]
                for column, dtype in expression.schema().items()
            }
        )

    monkeypatch.setattr(service, "bind_source", bind)
    monkeypatch.setattr(service, "collect_source", collect)

    def inspect_source(
        datasource: DatasourceIR,
        source: TableSource,
        *,
        project_root: Path,
        connections: DatasourceConnectionService,
    ) -> SourceInspection:
        assert probe.active_backend is connections.session_backend(datasource.name)
        return SourceInspection(
            _datasource=ref_factory.datasource(datasource.name),
            source=source,
            physical_extent=PhysicalExtent(None, "unknown", None, "unknown", "test", ()),
            partitioning=Partitioning("none", (), None, (), False, False),
            execution_capabilities=ExecutionCapabilities(True, False, True, False),
            schema=tuple(
                ColumnMetadata(name, dtype, None, None, index)
                for index, (name, dtype) in enumerate(
                    (("order_id", "int64"), ("amount", "float64"), ("region", "string")), 1
                )
            ),
            warnings=(),
            _project_root=project_root,
        )

    monkeypatch.setattr("marivo.datasource.inspection._inspect_resolved_source", inspect_source)
    monkeypatch.setattr(
        "marivo.datasource.engines.mysql.execute_provider_statement",
        lambda *_args, **_kwargs: ({"@@session.max_execution_time": 30000},),
    )
    try:
        yield probe
    finally:
        service.close_all()


def _preview(probe: _Probe, path: _PreviewPath, scope: AuthoringScope) -> tuple[PreviewResult, ...]:
    if path == "single":
        return (probe.catalog.preview(probe.catalog.get("entity:sales.orders"), scope=scope),)
    refs = (
        [
            probe.catalog.get("entity:sales.orders"),
            probe.catalog.get("dimension:sales.orders.region"),
            probe.catalog.get("measure:sales.orders.amount"),
        ]
        if path == "rows"
        else [probe.catalog.get("metric:sales.revenue")]
    )
    return probe.catalog.preview_many(refs, scope=scope).results


@pytest.mark.parametrize("backend_kind", ("trino", "postgres", "mysql"))
@pytest.mark.parametrize("path", ("single", "rows", "metrics"))
def test_preview_prepares_deadline_before_source_resolution(
    backend_kind: _BackendKind,
    path: _PreviewPath,
    semantic_project_factory: _ProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _probe(backend_kind, semantic_project_factory, monkeypatch) as probe:
        service = probe.connections
        ordinary = service.session_backend("warehouse")
        results = _preview(probe, path, md.unpruned(max_rows=10, timeout_seconds=30))
        assert all(result.returned_row_count == 1 for result in results)
        assert probe.resolved and set(probe.resolved) == {(30, True)}
        assert len(probe.collected) == 1
        assert service.session_backend("warehouse") is ordinary
        ordinary.disconnect.assert_not_called()
        for bounded in probe.created[1:]:
            bounded.disconnect.assert_called_once_with()
        if backend_kind == "trino":
            assert probe.drivers[1].session_properties["query_max_run_time"] == "30s"
        elif backend_kind == "postgres":
            assert (
                probe.connect_kwargs[1]["options"] == "-c statement_timeout=30000 -c TimeZone=UTC"
            )
            assert probe.drivers[1].read_only and not probe.drivers[1].autocommit
            assert probe.drivers[1].rollbacks == 1
        else:
            assert len(probe.created) >= 2
            assert not hasattr(probe.created[1], "_marivo_authoring_cancel_control")
            assert probe.created[1]._marivo_configured_query_timeout_seconds == 30


@pytest.mark.parametrize("backend_kind", ("trino", "postgres", "mysql"))
@pytest.mark.parametrize("failure", ("bind", "read"))
@pytest.mark.parametrize("path", ("single", "rows", "metrics"))
def test_preview_releases_remote_connections_and_restores_cache_after_failure(
    backend_kind: _BackendKind,
    failure: Literal["bind", "read"],
    path: _PreviewPath,
    semantic_project_factory: _ProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _probe(backend_kind, semantic_project_factory, monkeypatch) as probe:
        service = probe.connections
        ordinary = service.session_backend("warehouse")
        probe.failure = failure
        with pytest.raises(
            SemanticError if failure == "bind" or path in {"rows", "metrics"} else RuntimeError,
            match=f"source {failure} failed",
        ):
            _preview(probe, path, md.unpruned(max_rows=10, timeout_seconds=30))
        assert service.session_backend("warehouse") is ordinary
        ordinary.disconnect.assert_not_called()
        for bounded in probe.created[1:]:
            bounded.disconnect.assert_called_once_with()
        if backend_kind == "postgres":
            assert probe.drivers[1].rollbacks == 1


def test_batch_groups_use_their_own_deadlines_and_preserve_order(
    semantic_project_factory: _ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _probe("trino", semantic_project_factory, monkeypatch) as probe:
        refs = [
            probe.catalog.get("entity:sales.orders"),
            probe.catalog.get("measure:sales.orders.amount"),
            probe.catalog.get("entity:sales.refunds"),
            probe.catalog.get("measure:sales.refunds.amount"),
        ]
        batch = probe.catalog.preview_many(
            refs,
            scope=(
                (
                    probe.catalog.get("entity:sales.orders"),
                    md.unpruned(max_rows=10, timeout_seconds=30),
                ),
                (
                    probe.catalog.get("entity:sales.refunds"),
                    md.unpruned(max_rows=10, timeout_seconds=11),
                ),
            ),
        )
        assert batch.refs == (
            "sales.orders",
            "sales.orders.amount",
            "sales.refunds",
            "sales.refunds.amount",
        )
        assert len(probe.collected) == 2
        assert [backend._marivo_terminal_timeout_seconds for backend in probe.created] == [
            30,
            11,
            30,
            11,
        ]
        assert all(active for _seconds, active in probe.resolved)
        for backend in probe.created:
            backend.disconnect.assert_called_once_with()


@pytest.mark.parametrize("backend_kind", ("trino", "postgres", "mysql"))
def test_preview_keeps_real_timeout_preflight_and_releases_rejected_connection(
    backend_kind: _BackendKind,
    semantic_project_factory: _ProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _probe(backend_kind, semantic_project_factory, monkeypatch) as probe:
        probe.corrupt_configuration = True
        with pytest.raises(
            (RuntimeError, DatasourceSourceCapabilityError), match=r"no configured|isolated reader"
        ):
            _preview(probe, "single", md.unpruned(max_rows=10, timeout_seconds=30))
        assert not probe.resolved and not probe.collected
        for backend in probe.created:
            backend.disconnect.assert_called_once_with()


def test_invalid_batch_is_rejected_before_connection(
    semantic_project_factory: _ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _probe("trino", semantic_project_factory, monkeypatch) as probe:
        with pytest.raises(SemanticError):
            probe.catalog.preview_many(
                [
                    probe.catalog.get("entity:sales.orders"),
                    probe.catalog.get("entity:sales.missing"),
                ],
                scope=md.unpruned(max_rows=10, timeout_seconds=30),
            )
        assert not probe.created and not probe.resolved and not probe.collected


@pytest.mark.parametrize("backend_kind", ("trino", "postgres", "mysql", "clickhouse"))
@pytest.mark.parametrize("path", ("single", "batch", "entity_first_batch"))
def test_remote_metric_preview_uses_native_timeout_and_reports_inputs(
    backend_kind: _BackendKind,
    path: Literal["single", "batch", "entity_first_batch"],
    semantic_project_factory: _ProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _probe(backend_kind, semantic_project_factory, monkeypatch) as probe:
        scope = md.unpruned(max_rows=10, timeout_seconds=30)
        if path == "entity_first_batch":
            result = probe.catalog.preview_many(
                [
                    probe.catalog.get("entity:sales.orders"),
                    probe.catalog.get("metric:sales.revenue"),
                ],
                scope=scope,
            ).results[-1]
        elif path == "batch":
            result = _preview(probe, "metrics", scope)[0]
        else:
            result = probe.catalog.preview(probe.catalog.get("metric:sales.revenue"), scope=scope)
        assert result.kind == "semantic_metric"
        assert result.coverage.entity_row_limits == (("sales.orders", 10),)
        assert result.execution_limits is not None
        assert result.execution_limits.backend == backend_kind
        assert result.execution_limits.timeout_enforced
        assert result.execution_limits.scan_rows is None
        assert result.execution_limits.scan_bytes is None
        assert probe.collected[-1] == ("semantic.preview", 1)
        assert all(active for _seconds, active in probe.resolved)
