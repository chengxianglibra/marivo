from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.contracts.evidence import Evidence, Finding, Proposition
from app.contracts.ids import (
    ArtifactId,
    EvidenceRef,
    FindingId,
    PropositionId,
    SessionId,
)
from app.contracts.session import SessionEvent
from app.contracts.values import LAYOUT_VERSION, CacheValue


@pytest.fixture()
def tmp_marivo(tmp_path: Path) -> Path:
    """Create a temporary .marivo/ layout with all subdirectories and VERSION."""
    marivo_dir = tmp_path / ".marivo"
    marivo_dir.mkdir()
    (marivo_dir / "models").mkdir()
    (marivo_dir / "evidence").mkdir()
    (marivo_dir / "VERSION").write_text(str(LAYOUT_VERSION))
    (marivo_dir / "marivo.toml").write_text(
        '[profile]\nmode = "local"\n\n[datasource]\ntype = "duckdb"\n\n[telemetry]\nsink = "none"\n'
    )
    _init_state_db(marivo_dir / "state.db")
    return marivo_dir


def _init_state_db(db_path: Path) -> None:
    """Create state.db with session_events and cache_entries tables."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS session_events (
            session_id  TEXT NOT NULL,
            seq         INTEGER NOT NULL,
            event_type  TEXT NOT NULL,
            timestamp   TEXT NOT NULL,
            payload     TEXT NOT NULL,
            actor       TEXT,
            PRIMARY KEY (session_id, seq)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cache_entries (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            expires_at  TEXT
        )"""
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def sample_session_id() -> SessionId:
    return SessionId(f"sess-{uuid.uuid4().hex[:12]}")


@pytest.fixture()
def sample_session_event(sample_session_id: SessionId) -> SessionEvent:
    return SessionEvent(
        session_id=sample_session_id,
        event_type="session_created",
        timestamp="2026-05-07T10:00:00Z",
        payload={"goal": "test investigation"},
        actor=None,
    )


@pytest.fixture()
def sample_evidence(sample_session_id: SessionId) -> Evidence:
    return Evidence(
        ref=EvidenceRef("a" * 64),
        findings=[
            Finding(
                finding_id=FindingId(f"find-{uuid.uuid4().hex[:8]}"),
                session_id=sample_session_id,
                artifact_id=ArtifactId(f"art-{uuid.uuid4().hex[:8]}"),
                finding_type="test",
                content={"description": "test finding"},
            )
        ],
        proposition=Proposition(
            proposition_id=PropositionId(f"prop-{uuid.uuid4().hex[:8]}"),
            session_id=sample_session_id,
            identity_key="test_identity",
            description="test proposition",
        ),
    )


@pytest.fixture()
def sample_evidence_ref() -> EvidenceRef:
    return EvidenceRef("a" * 64)


@pytest.fixture()
def sample_cache_value() -> CacheValue:
    return CacheValue(b'{"key": "value"}')


@pytest.fixture()
def sample_semantic_model() -> dict[str, Any]:
    """Minimal OSI-compatible semantic model dict for FileModelStore tests."""
    return {
        "name": "test_model",
        "datasets": {
            "orders": {
                "table": "analytics.orders",
                "measures": {"revenue": {"expr": "SUM(amount)", "type": "numeric"}},
                "dimensions": {"region": {"expr": "customer_region", "type": "categorical"}},
            }
        },
        "relationships": {},
    }
