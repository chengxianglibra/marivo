"""SessionService — analysis session and semantic snapshot management."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException

from app.storage.sqlite_metadata import SQLiteMetadataStore


class SessionService:
    """Manage analysis sessions and their frozen semantic snapshots."""

    def __init__(self, store: SQLiteMetadataStore) -> None:
        self.store = store

    def create_session(self, requesting_user: str) -> dict[str, Any]:
        """Create an analysis session and freeze the current semantic snapshot."""
        session_id = f"sess_{uuid.uuid4().hex[:12]}"

        self.store.execute(
            """
            INSERT INTO analysis_sessions (session_id, requesting_user)
            VALUES (?, ?)
            """,
            [session_id, requesting_user],
        )

        # Snapshot official models
        official_models = self.store.query_rows(
            "SELECT name, revision, visibility, owner_user FROM semantic_models WHERE visibility = 'public'"
        )
        for model in official_models:
            self.store.execute(
                """
                INSERT INTO session_semantic_snapshots
                    (session_id, model_name, revision, visibility, owner_user)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    session_id,
                    model["name"],
                    model["revision"],
                    model["visibility"],
                    model["owner_user"],
                ],
            )

        # Snapshot private models owned by requesting_user
        private_models = self.store.query_rows(
            "SELECT name, revision, visibility, owner_user FROM semantic_models WHERE visibility = 'private' AND owner_user = ?",
            [requesting_user],
        )
        for model in private_models:
            self.store.execute(
                """
                INSERT INTO session_semantic_snapshots
                    (session_id, model_name, revision, visibility, owner_user)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    session_id,
                    model["name"],
                    model["revision"],
                    model["visibility"],
                    model["owner_user"],
                ],
            )

        return {
            "session_id": session_id,
            "requesting_user": requesting_user,
            "status": "active",
        }

    def get_session(self, session_id: str) -> dict[str, Any]:
        """Get session details including resolved models from snapshot."""
        session_row = self.store.query_one(
            "SELECT * FROM analysis_sessions WHERE session_id = ?", [session_id]
        )
        if session_row is None:
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

        snapshot_rows = self.store.query_rows(
            "SELECT * FROM session_semantic_snapshots WHERE session_id = ?", [session_id]
        )

        resolved_objects = [
            {
                "model_name": r["model_name"],
                "revision": r["revision"],
                "visibility": r["visibility"],
                "owner_user": r["owner_user"],
            }
            for r in snapshot_rows
        ]

        return {
            "session_id": session_row["session_id"],
            "requesting_user": session_row["requesting_user"],
            "snapshot_frozen_at": session_row["snapshot_frozen_at"],
            "status": session_row["status"],
            "resolved_objects": resolved_objects,
        }

    def end_session(self, session_id: str) -> dict[str, Any]:
        """End an active session."""
        session_row = self.store.query_one(
            "SELECT * FROM analysis_sessions WHERE session_id = ?", [session_id]
        )
        if session_row is None:
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
        if session_row["status"] == "ended":
            raise HTTPException(status_code=400, detail=f"Session '{session_id}' is already ended")

        self.store.execute(
            "UPDATE analysis_sessions SET status = 'ended', ended_at = datetime('now') WHERE session_id = ?",
            [session_id],
        )
        return {"session_id": session_id, "status": "ended"}

    def add_model_to_snapshot(
        self,
        session_id: str,
        model_name: str,
        revision: int,
        visibility: str,
        owner_user: str | None = None,
    ) -> None:
        """Add a newly created private model to the active session's snapshot."""
        session_row = self.store.query_one(
            "SELECT status FROM analysis_sessions WHERE session_id = ?", [session_id]
        )
        if session_row is None or session_row["status"] != "active":
            return
        self.store.execute(
            """
            INSERT INTO session_semantic_snapshots
                (session_id, model_name, revision, visibility, owner_user)
            VALUES (?, ?, ?, ?, ?)
            """,
            [session_id, model_name, revision, visibility, owner_user],
        )
