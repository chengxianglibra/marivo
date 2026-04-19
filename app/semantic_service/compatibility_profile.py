from __future__ import annotations

import json
from typing import Any, ClassVar, Literal
from uuid import uuid4

from app.analysis_core.calendar_policy import list_calendar_policy_catalog_entries
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

    def read_compatibility_profile(self, profile_identifier: str) -> dict[str, Any]:
        builtin = self._builtin_calendar_policy_profile(profile_identifier)
        if builtin is not None:
            return builtin
        row = self.metadata.query_one(
            "SELECT * FROM compiler_compatibility_profiles WHERE profile_id = ?",
            [profile_identifier],
        )
        if row is None:
            row = self.metadata.query_one(
                "SELECT * FROM compiler_compatibility_profiles WHERE profile_ref = ?",
                [profile_identifier],
            )
        if row is None:
            raise self._not_found(f"Unknown compatibility profile: {profile_identifier}")
        return self._row_to_compatibility_profile(row)

    def get_compatibility_profile(self, profile_id: str) -> dict[str, Any]:
        builtin = self._builtin_calendar_policy_profile(profile_id)
        if builtin is not None:
            return builtin
        row = self.metadata.query_one(
            "SELECT * FROM compiler_compatibility_profiles WHERE profile_id = ?",
            [profile_id],
        )
        if row is None:
            raise self._not_found(f"Unknown compatibility profile: {profile_id}")
        return self._row_to_compatibility_profile(row)

    def list_compatibility_profiles(
        self,
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool = False,
    ) -> dict[str, Any]:
        status = self._resolve_semantic_filters(status=status, lifecycle_status=lifecycle_status)
        if status is None:
            rows = self.metadata.query_rows(
                "SELECT * FROM compiler_compatibility_profiles ORDER BY profile_ref"
            )
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM compiler_compatibility_profiles WHERE status = ? ORDER BY profile_ref",
                [status],
            )
        mode: Literal["list", "detail"] = "detail" if detail else "list"
        items = [
            item
            for row in rows
            if self._matches_readiness_filter(
                item := self._row_to_compatibility_profile(
                    row, mode=mode, include_dependents=detail
                ),
                readiness_status=readiness_status,
            )
        ]
        if status in (None, "published"):
            for entry in list_calendar_policy_catalog_entries():
                builtin = self._builtin_calendar_policy_profile(entry.policy_ref, mode=mode)
                if builtin is None:
                    continue
                if self._matches_readiness_filter(builtin, readiness_status=readiness_status):
                    items.append(builtin)
        items.sort(key=lambda item: str(item["profile_ref"]))
        return {"items": items, "total": len(items)}

    def update_compatibility_profile(
        self, profile_id: str, payload: CompatibilityProfileUpdateRequest
    ) -> dict[str, Any]:
        self._reject_builtin_calendar_policy_action(profile_id, action="update")
        current = self.get_compatibility_profile(profile_id)
        self._require_lifecycle_action_status(
            action="activate",
            status=current["status"],
            object_label="Compatibility profile",
            object_id=profile_id,
        )
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

    def validate_compatibility_profile(self, profile_id: str) -> dict[str, Any]:
        self._reject_builtin_calendar_policy_action(profile_id, action="validate")
        current = self.get_compatibility_profile(profile_id)
        self._validate_record(
            object_id=profile_id,
            object_label="Compatibility profile",
            status=current["status"],
            compatibility_validator=lambda: self._validate_profile_subject_ref(
                current["subject_kind"],
                current["subject_ref"],
                require_published=True,
            ),
        )
        return self.get_compatibility_profile(profile_id)

    def activate_compatibility_profile(self, profile_id: str) -> dict[str, Any]:
        self._reject_builtin_calendar_policy_action(profile_id, action="activate")
        current = self.get_compatibility_profile(profile_id)
        self._require_lifecycle_action_status(
            action="activate",
            status=current["status"],
            object_label="Compatibility profile",
            object_id=profile_id,
        )
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

    def deprecate_compatibility_profile(self, profile_id: str) -> dict[str, Any]:
        self._reject_builtin_calendar_policy_action(profile_id, action="deprecate")
        current = self.get_compatibility_profile(profile_id)
        self._deprecate_record(
            table_name="compiler_compatibility_profiles",
            id_column="profile_id",
            object_id=profile_id,
            object_label="Compatibility profile",
            status=current["status"],
        )
        return self.get_compatibility_profile(profile_id)

    def publish_compatibility_profile(self, profile_id: str) -> dict[str, Any]:
        self._reject_builtin_calendar_policy_action(profile_id, action="publish")
        return self.activate_compatibility_profile(profile_id)

    def _builtin_calendar_policy_profile(
        self, identifier: str, mode: Literal["list", "detail"] = "detail"
    ) -> dict[str, Any] | None:
        for entry in list_calendar_policy_catalog_entries():
            if identifier not in {entry.policy_ref, entry.object_id}:
                continue
            profile: dict[str, Any] = {
                "profile_id": entry.object_id,
                "profile_ref": entry.policy_ref,
                "subject_kind": "binding",
                "subject_ref": "binding.calendar_alignment",
                "status": entry.status,
                "revision": entry.revision,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
                "system_managed": True,
                "catalog_source": entry.catalog_source,
                "lifecycle_status": entry.lifecycle_status,
                "readiness_status": entry.readiness_status,
                "blocking_requirements": [],
                "capabilities": {"supports_observe_calendar_alignment": True},
                "dependency_refs": [],
                "dependent_refs": [],
            }
            if mode == "detail":
                profile["profile_kind"] = "capability"
                profile["schema_version"] = "v1"
                profile["subject_revision"] = 1
                profile["requirement"] = None
                profile["capability"] = {
                    "inferential_ready": True,
                    "supported_sample_summaries": [],
                }
                profile["semantic"] = {
                    "comparison_basis": entry.comparison_basis,
                    "resolved_alignment_mode": entry.resolved_alignment_mode,
                    "resolved_calendar_source": entry.resolved_calendar_source,
                    "use_when": list(entry.use_when),
                    "avoid_when": list(entry.avoid_when),
                    "matching_strategy_summary": list(entry.matching_strategy_summary),
                    "fallback_strategy": list(entry.fallback_strategy),
                    "coverage_behavior": entry.coverage_behavior,
                }
            return profile
        return None

    def _reject_builtin_calendar_policy_action(self, identifier: str, *, action: str) -> None:
        if self._builtin_calendar_policy_profile(identifier, mode="list") is None:
            return
        raise self._validation_error(
            f"Compatibility profile {identifier} is a system-managed builtin calendar policy and "
            f"does not support {action}."
        )

    def _validate_profile_kind_subject_kind(self, profile_kind: str, subject_kind: str) -> None:
        valid_subjects = self._VALID_SUBJECT_KIND_FOR_PROFILE_KIND.get(profile_kind)
        if valid_subjects is None:
            return  # unknown profile_kind is caught by DB constraint
        if subject_kind not in valid_subjects:
            raise self._validation_error(
                f"profile_kind '{profile_kind}' is not valid for subject_kind '{subject_kind}'. "
                f"Expected subject_kind in {sorted(valid_subjects)}."
            )
