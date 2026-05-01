"""Tests for Session API endpoints."""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.analysis_session import router as session_router
from app.api.models.osi import OSI_SPEC_VERSION
from app.api.semantic_v2 import router as semantic_v2_router
from app.semantic_service_v2.service import SemanticModelV2Service
from app.semantic_service_v2.session import SessionService
from app.storage.sqlite_metadata import SQLiteMetadataStore


class _TestMetadataStore(SQLiteMetadataStore):
    def initialize(self) -> None:
        import sqlite3

        from app.storage.schema import METADATA_DDL, metadata_schema_marker_row

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            self.db_path.unlink()
        con = sqlite3.connect(str(self.db_path))
        try:
            for ddl in METADATA_DDL:
                con.execute(ddl)
            marker = metadata_schema_marker_row("sqlite")
            con.execute(
                "INSERT OR IGNORE INTO metadata_schema_marker (backend, schema_version, ddl_fingerprint) VALUES (?, ?, ?)",
                [marker["backend"], marker["schema_version"], marker["ddl_fingerprint"]],
            )
            con.commit()
        finally:
            con.close()


def _make_app() -> TestClient:
    tmp = tempfile.mkdtemp(prefix=f"marivo_session_{uuid.uuid4().hex[:8]}_")
    db_path = Path(tmp) / "meta.sqlite"
    store = _TestMetadataStore(db_path)
    store.initialize()
    semantic_service = SemanticModelV2Service(store)
    session_service = SessionService(store)

    app = FastAPI()
    app.include_router(semantic_v2_router)
    app.include_router(session_router)
    app.state.semantic_v2_service = semantic_service
    app.state.session_service = session_service
    return TestClient(app)


def _make_model_dict(
    name: str = "test_model",
    visibility: str = "public",
    owner_user: str | None = None,
) -> dict:
    marivo_data: dict = {"visibility": visibility}
    if owner_user:
        marivo_data["owner_user"] = owner_user
    return {
        "name": name,
        "datasets": [
            {
                "name": "orders",
                "source": "analytics.orders",
                "primary_key": ["order_id"],
                "fields": [
                    {
                        "name": "order_id",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]
                        },
                    },
                ],
            }
        ],
        "custom_extensions": [
            {"vendor_name": "MARIVO", "data": json.dumps(marivo_data)},
        ],
    }


class TestCreateSession(unittest.TestCase):
    def test_create_session_returns_session_id(self) -> None:
        client = _make_app()
        resp = client.post("/analysis-sessions", json={"requesting_user": "alice"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("session_id", body)
        self.assertEqual(body["status"], "active")

    def test_create_session_snapshots_official_models(self) -> None:
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        client.post("/semantic-models/import", json=doc)
        resp = client.post("/analysis-sessions", json={"requesting_user": "alice"})
        session_id = resp.json()["session_id"]
        detail = client.get(f"/analysis-sessions/{session_id}").json()
        model_names = [o["model_name"] for o in detail["resolved_objects"]]
        self.assertIn("commerce", model_names)


class TestGetSession(unittest.TestCase):
    def test_get_session_returns_snapshot(self) -> None:
        client = _make_app()
        session_id = client.post("/analysis-sessions", json={"requesting_user": "alice"}).json()[
            "session_id"
        ]
        resp = client.get(f"/analysis-sessions/{session_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("resolved_objects", resp.json())

    def test_get_nonexistent_returns_404(self) -> None:
        client = _make_app()
        self.assertEqual(client.get("/analysis-sessions/nonexistent").status_code, 404)


class TestEndSession(unittest.TestCase):
    def test_end_session(self) -> None:
        client = _make_app()
        session_id = client.post("/analysis-sessions", json={"requesting_user": "alice"}).json()[
            "session_id"
        ]
        resp = client.post(f"/analysis-sessions/{session_id}/end")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ended")
