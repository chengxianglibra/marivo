from __future__ import annotations

from marivo.config import MarivoConfig
from marivo.profiles.server import ServerComposition, ServerConfig


def test_server_config_constructible_with_marivo_config_only() -> None:
    cfg = ServerConfig(marivo_config=MarivoConfig())
    assert cfg.marivo_config is not None
    assert cfg.db_path is None
    assert cfg.metadata_store is None
    assert cfg.analytics_engine is None


def test_server_config_accepts_test_injection_kwargs() -> None:
    cfg = ServerConfig(
        marivo_config=MarivoConfig(),
        db_path=":memory:",
        metadata_store=None,
        analytics_engine=None,
    )
    assert cfg.db_path == ":memory:"


def test_server_composition_has_expected_fields() -> None:
    import dataclasses

    fields = {f.name for f in dataclasses.fields(ServerComposition)}
    assert fields == {
        "runtime",
        "metadata_store",
        "analytics_engine",
        "metrics",
        "resolved_analytics_path",
    }


def test_create_server_runtime_returns_server_composition(tmp_path) -> None:
    from marivo.adapters.local.duckdb_analytics import DuckDBAnalyticsEngine
    from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
    from marivo.profiles.server import create_server_runtime
    from marivo.runtime.runtime import MarivoRuntime

    meta = SQLiteMetadataStore(tmp_path / "meta.sqlite")
    analytics = DuckDBAnalyticsEngine(":memory:")

    composition = create_server_runtime(
        ServerConfig(
            marivo_config=MarivoConfig(),
            db_path=":memory:",
            metadata_store=meta,
            analytics_engine=analytics,
        )
    )
    assert isinstance(composition, ServerComposition)
    assert isinstance(composition.runtime, MarivoRuntime)
    assert composition.metadata_store is meta
    assert composition.analytics_engine is analytics
    from marivo.runtime.semantic.calendar_data_runtime import CalendarDataReader
    from marivo.runtime.semantic.calendar_data_service import CalendarDataService

    assert isinstance(composition.runtime.get_service("calendar_data"), CalendarDataService)
    assert isinstance(composition.runtime.calendar_data_reader, CalendarDataReader)


def test_create_server_runtime_ports_are_wrapper_adapters(tmp_path) -> None:
    from marivo.adapters.local.duckdb_analytics import DuckDBAnalyticsEngine
    from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
    from marivo.adapters.server.audit_log import FileAuditLogAdapter
    from marivo.adapters.server.authz import NoopAuthZAdapter
    from marivo.adapters.server.cache_store import InMemoryCacheStore
    from marivo.adapters.server.data_source import RoutingDataSource
    from marivo.adapters.server.evidence_store import MetadataEvidenceStoreAdapter
    from marivo.adapters.server.model_store import SqlModelStoreAdapter
    from marivo.adapters.server.runtime_config import TomlRuntimeConfigAdapter
    from marivo.adapters.server.session_store import SqlSessionStore
    from marivo.profiles.server import create_server_runtime

    composition = create_server_runtime(
        ServerConfig(
            marivo_config=MarivoConfig(),
            db_path=":memory:",
            metadata_store=SQLiteMetadataStore(tmp_path / "meta.sqlite"),
            analytics_engine=DuckDBAnalyticsEngine(":memory:"),
        )
    )
    ports = composition.runtime.ports
    assert isinstance(ports.model_store, SqlModelStoreAdapter)
    assert isinstance(ports.session_store, SqlSessionStore)
    assert isinstance(ports.evidence_store, MetadataEvidenceStoreAdapter)
    assert isinstance(ports.data_source, RoutingDataSource)
    assert isinstance(ports.cache_store, InMemoryCacheStore)
    assert isinstance(ports.authz, NoopAuthZAdapter)
    assert isinstance(ports.audit_log, FileAuditLogAdapter)
    assert isinstance(ports.runtime_config, TomlRuntimeConfigAdapter)


def test_server_module_does_not_import_app_service() -> None:
    import inspect

    import marivo.profiles.server as mod

    src = inspect.getsource(mod)
    assert "from marivo.service" not in src
    assert "import marivo.service" not in src


def test_server_module_does_not_import_runtime_factory() -> None:
    import inspect

    import marivo.profiles.server as mod

    src = inspect.getsource(mod)
    assert "from marivo.runtime.factory" not in src
    assert "import marivo.runtime.factory" not in src
