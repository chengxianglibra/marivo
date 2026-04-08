from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.api.models.compatibility_profile import (
    CompatibilityProfileCreateRequest,
    CompatibilityProfileUpdateRequest,
)

from .common import SemanticServiceSupport, now_iso


class CompatibilityProfileService(SemanticServiceSupport):
    def create_compatibility_profile(
        self, payload: CompatibilityProfileCreateRequest
    ) -> dict[str, Any]:
        self._validate_profile_subject_ref(payload.subject_kind, payload.subject_ref)
        profile_id = f"cprof_{uuid4().hex[:24]}"
        created_at = now_iso()
        self.metadata.execute(
            """
            INSERT INTO compiler_compatibility_profiles (
                profile_id, profile_ref, profile_kind, schema_version, subject_kind,
                subject_ref, requirement_json, capability_json, status, revision,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                profile_id,
                payload.profile_ref,
                payload.profile_kind,
                payload.schema_version,
                payload.subject_kind,
                payload.subject_ref,
                json.dumps(
                    payload.requirement.model_dump(mode="json") if payload.requirement else {}
                ),
                json.dumps(
                    payload.capability.model_dump(mode="json") if payload.capability else {}
                ),
                created_at,
                created_at,
            ],
        )
        return self.get_compatibility_profile(profile_id)

    def get_compatibility_profile(self, profile_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM compiler_compatibility_profiles WHERE profile_id = ?",
            [profile_id],
        )
        if row is None:
            raise self._not_found(f"Unknown compatibility profile: {profile_id}")
        return self._row_to_compatibility_profile(row)

    def list_compatibility_profiles(self, status: str | None = None) -> dict[str, Any]:
        if status is None:
            rows = self.metadata.query_rows(
                "SELECT * FROM compiler_compatibility_profiles ORDER BY profile_ref"
            )
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM compiler_compatibility_profiles WHERE status = ? ORDER BY profile_ref",
                [status],
            )
        items = [self._row_to_compatibility_profile(row) for row in rows]
        return {"items": items, "total": len(items)}

    def update_compatibility_profile(
        self, profile_id: str, payload: CompatibilityProfileUpdateRequest
    ) -> dict[str, Any]:
        current = self.get_compatibility_profile(profile_id)
        updates: list[str] = []
        params: list[Any] = []
        if payload.requirement is not None:
            if current["profile_kind"] != "requirement":
                raise self._validation_error("Only requirement profiles accept requirement updates")
            updates.append("requirement_json = ?")
            params.append(json.dumps(payload.requirement.model_dump(mode="json")))
        if payload.capability is not None:
            if current["profile_kind"] != "capability":
                raise self._validation_error("Only capability profiles accept capability updates")
            updates.append("capability_json = ?")
            params.append(json.dumps(payload.capability.model_dump(mode="json")))
        if not updates:
            return current
        updates.append("updated_at = ?")
        params.append(now_iso())
        params.append(profile_id)
        self.metadata.execute(
            f"UPDATE compiler_compatibility_profiles SET {', '.join(updates)} WHERE profile_id = ?",
            params,
        )
        return self.get_compatibility_profile(profile_id)

    def publish_compatibility_profile(self, profile_id: str) -> dict[str, Any]:
        self.get_compatibility_profile(profile_id)
        self.metadata.execute(
            """
            UPDATE compiler_compatibility_profiles
            SET status = 'published', revision = revision + 1, updated_at = ?
            WHERE profile_id = ?
            """,
            [now_iso(), profile_id],
        )
        return self.get_compatibility_profile(profile_id)
