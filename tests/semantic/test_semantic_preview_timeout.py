"""Ordinary previews prepare remote deadlines before resolving their source."""

from __future__ import annotations

import socket
import textwrap
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Literal
from unittest.mock import MagicMock, Mock

import ibis
import ibis.expr.types as ir
import pandas as pd
import pytest
from ibis.backends import BaseBackend

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource import backends
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.ir import (
    DatasourceIR,
    EntitySourceIR,
    QueryParamScalar,
    QueryParamScalarList,
)
from marivo.datasource.source import AuthoringScope
from marivo.preview import PreviewResult
from marivo.semantic import catalog as catalog_module
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticError
from marivo.semantic.reader import SemanticProject

_BackendKind = Literal["trino", "postgres", "mysql"]
_PreviewPath = Literal["single", "rows", "metrics"]
_ProjectFactory = Callable[[dict[str, str]], SemanticProject]


@dataclass
class _Driver:
    session_properties: dict[str, object]
    autocommit: bool
    identity: int
    read_only: bool = False
    rollbacks: int = 0

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
                """\
                import marivo.datasource as md
                import marivo.semantic as ms
                orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
                refunds = ms.entity(name="refunds", datasource=ms.ref.datasource("warehouse"), source=md.table("refunds"))
                region = ms.dimension_column(name="region", entity=orders, column="region")
                amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="USD")
                revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
                @ms.metric(entities=[orders, refunds], root_entity=orders, additivity=ms.additive_all())
                def net_revenue(orders, refunds):
                    return orders.amount.sum()
                """
            ),
        }
    )
    probe = _Probe(SemanticCatalog(project), backend_kind)
    profile = require_profile_for_backend_type(backend_kind)
    timeout = profile.authoring_timeout
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

        def disconnect() -> None:
            # The MySQL driver owns the control attached during bounded construction.
            control = getattr(backend, "_marivo_authoring_cancel_control", None)
            if control is not None:
                control.disconnect()
                backend._marivo_authoring_cancel_control = None

        backend.disconnect.side_effect = disconnect
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

    prepared_profile = replace(profile, connect=connect, authoring_timeout=guarded)
    monkeypatch.setattr(
        backends, "require_profile_for_backend_type", lambda _kind: prepared_profile
    )
    monkeypatch.setattr(
        catalog_module, "require_profile_for_backend_type", lambda _kind: prepared_profile
    )
    service = probe.catalog._project._connection_service()

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

    def duplicate_socket(_descriptor: int, _family: int, _kind: int) -> MagicMock:
        owned = MagicMock(spec=socket.socket)
        probe.sockets.append(owned)
        return owned

    monkeypatch.setattr(service, "bind_source", bind)
    monkeypatch.setattr(service, "collect_source", collect)
    monkeypatch.setattr("marivo.datasource.engines.mysql.socket.fromfd", duplicate_socket)
    try:
        yield probe
    finally:
        service.close_all()


def _preview(probe: _Probe, path: _PreviewPath, scope: AuthoringScope) -> tuple[PreviewResult, ...]:
    if path == "single":
        return (probe.catalog.preview(ms.ref.entity("sales.orders"), scope=scope),)
    refs = (
        [
            ms.ref.entity("sales.orders"),
            ms.ref.dimension("sales.orders.region"),
            ms.ref.measure("sales.orders.amount"),
        ]
        if path == "rows"
        else [ms.ref.metric("sales.revenue")]
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
        service = probe.catalog._project._connection_service()
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
            assert len(probe.created) == 3
            assert probe.created[1]._marivo_authoring_thread_id == probe.drivers[1].identity
            assert probe.created[1]._marivo_authoring_cancel_control is None
            assert len(probe.sockets) == 1
            probe.sockets[0].__exit__.assert_called_once()


@pytest.mark.parametrize("backend_kind", ("trino", "postgres", "mysql"))
@pytest.mark.parametrize("failure", ("bind", "read"))
@pytest.mark.parametrize("path", ("single", "rows"))
def test_preview_releases_remote_connections_and_restores_cache_after_failure(
    backend_kind: _BackendKind,
    failure: Literal["bind", "read"],
    path: _PreviewPath,
    semantic_project_factory: _ProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _probe(backend_kind, semantic_project_factory, monkeypatch) as probe:
        service = probe.catalog._project._connection_service()
        ordinary = service.session_backend("warehouse")
        probe.failure = failure
        with pytest.raises(
            SemanticError if failure == "bind" or path == "rows" else RuntimeError,
            match=f"source {failure} failed",
        ):
            _preview(probe, path, md.unpruned(max_rows=10, timeout_seconds=30))
        assert service.session_backend("warehouse") is ordinary
        ordinary.disconnect.assert_not_called()
        for bounded in probe.created[1:]:
            bounded.disconnect.assert_called_once_with()
        if backend_kind == "postgres":
            assert probe.drivers[1].rollbacks == 1
        elif backend_kind == "mysql":
            probe.sockets[0].__exit__.assert_called_once()


def test_batch_groups_use_their_own_deadlines_and_preserve_order(
    semantic_project_factory: _ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _probe("trino", semantic_project_factory, monkeypatch) as probe:
        refs = [
            ms.ref.entity("sales.orders"),
            ms.ref.metric("sales.revenue"),
            ms.ref.entity("sales.refunds"),
            ms.ref.metric("sales.net_revenue"),
        ]
        batch = probe.catalog.preview_many(
            refs,
            scope={
                ms.ref.entity("sales.orders"): md.unpruned(max_rows=10, timeout_seconds=30),
                ms.ref.entity("sales.refunds"): md.unpruned(max_rows=10, timeout_seconds=11),
            },
        )
        assert batch.refs == tuple(ref.path for ref in refs)
        assert len(probe.collected) == 4
        assert [backend._marivo_terminal_timeout_seconds for backend in probe.created] == [
            30,
            30,
            11,
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
        with pytest.raises(RuntimeError, match=r"no configured|isolated owned reader"):
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
                [ms.ref.entity("sales.orders"), ms.ref.entity("sales.missing")],
                scope=md.unpruned(max_rows=10, timeout_seconds=30),
            )
        assert not probe.created and not probe.resolved and not probe.collected
