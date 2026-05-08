from __future__ import annotations

from app.config import MarivoConfig
from app.profiles.server import ServerComposition, ServerConfig


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
        "datasource_service",
        "query_router",
        "semantic_v2_service",
        "metrics",
        "resolved_analytics_path",
    }
