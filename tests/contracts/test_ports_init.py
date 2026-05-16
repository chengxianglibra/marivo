from __future__ import annotations

from marivo.ports import (
    AuditLog,
    AuthZ,
    CacheStore,
    DataSource,
    EvidenceStore,
    ModelStore,
    RuntimeConfig,
    SessionStore,
    Telemetry,
)


def test_all_protocols_importable() -> None:
    assert ModelStore is not None
    assert SessionStore is not None
    assert DataSource is not None
    assert EvidenceStore is not None
    assert CacheStore is not None
    assert AuthZ is not None
    assert AuditLog is not None
    assert Telemetry is not None
    assert RuntimeConfig is not None
