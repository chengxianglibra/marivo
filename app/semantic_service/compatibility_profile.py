from __future__ import annotations

import json
from typing import Any, ClassVar
from uuid import uuid4

from app.api.models.compatibility_profile import (
    CompatibilityProfileCreateRequest,
    CompatibilityProfileUpdateRequest,
)

from .common import SemanticServiceSupport, now_iso


class CompatibilityProfileService(SemanticServiceSupport):
    # Spec-defined valid (subject_kind, profile_kind) pairings
    _VALID_SUBJECT_KIND_FOR_PROFILE_KIND: ClassVar[dict[str, set[str]]] = {
        "requirement": {"metric"},
        "capability": {"process", "binding"},
    }

    def create_compatibility_profile(
        self, payload: CompatibilityProfileCreateRequest
    ) -> dict[str, Any]:
        self._validate_profile_subject_ref(payload.subject_kind, payload.subject_ref)
        self._validate_profile_kind_subject_kind(payload.profile_kind, payload.subject_kind)
        profile_id = f"cprof_{uuid4().hex[:24]}"
        created_at = now_iso()
        self.metadata.execute(
            """
            INSERT INTO compiler_compatibility_profiles (
                profile_id, profile_ref, profile_kind, schema_version, subject_kind,
                subject_ref, subject_revision, requirement_json, capability_json, status, revision,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                profile_id,
                payload.profile_ref,
                payload.profile_kind,
                payload.schema_version,
                payload.subject_kind,
                payload.subject_ref,
                None,
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
        self._require_draft_status(current["status"], "Compatibility profile", profile_id)
        updates: list[str] = []
        params: list[Any] = []
        if payload.requirement is not None:
            if current["profile_kind"] != "requirement":
                raise self._validation_error("Only requirement profiles accept requirement updates")
            if current["subject_kind"] != "metric":
                raise self._validation_error(
                    f"Requirement payloads are only valid for metric subjects, "
                    f"but subject_kind is '{current['subject_kind']}'"
                )
            updates.append("requirement_json = ?")
            params.append(json.dumps(payload.requirement.model_dump(mode="json")))
        if payload.capability is not None:
            if current["profile_kind"] != "capability":
                raise self._validation_error("Only capability profiles accept capability updates")
            if current["subject_kind"] not in ("process", "binding"):
                raise self._validation_error(
                    f"Capability payloads are only valid for process or binding subjects, "
                    f"but subject_kind is '{current['subject_kind']}'"
                )
            updates.append("capability_json = ?")
            params.append(json.dumps(payload.capability.model_dump(mode="json")))
        if not updates:
            return current
        updates.extend(["revision = revision + 1", "updated_at = ?"])
        params.extend([now_iso(), profile_id])
        self.metadata.execute(
            (
                "UPDATE compiler_compatibility_profiles "
                f"SET {', '.join(updates)} WHERE profile_id = ?"
            ),
            params,
        )
        return self.get_compatibility_profile(profile_id)

    def publish_compatibility_profile(self, profile_id: str) -> dict[str, Any]:
        current = self.get_compatibility_profile(profile_id)
        self._require_draft_status(current["status"], "Compatibility profile", profile_id)
        self._run_publish_compatibility_validation(
            lambda: self._validate_profile_subject_ref(
                current["subject_kind"],
                current["subject_ref"],
                require_published=True,
            )
        )
        subject_revision = self._published_profile_subject_revision(
            current["subject_kind"],
            current["subject_ref"],
        )
        self.metadata.execute(
            """
            UPDATE compiler_compatibility_profiles
            SET status = 'published',
                subject_revision = ?,
                revision = revision + 1,
                updated_at = ?
            WHERE profile_id = ?
            """,
            [subject_revision, now_iso(), profile_id],
        )
        return self.get_compatibility_profile(profile_id)

    def _validate_profile_kind_subject_kind(self, profile_kind: str, subject_kind: str) -> None:
        valid_subjects = self._VALID_SUBJECT_KIND_FOR_PROFILE_KIND.get(profile_kind)
        if valid_subjects is None:
            return  # unknown profile_kind is caught by DB constraint
        if subject_kind not in valid_subjects:
            raise self._validation_error(
                f"profile_kind '{profile_kind}' is not valid for subject_kind '{subject_kind}'. "
                f"Expected subject_kind in {sorted(valid_subjects)}."
            )
