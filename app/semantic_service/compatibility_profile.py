from __future__ import annotations

import json
from typing import Any, ClassVar, Literal
from uuid import uuid4

from app.analysis_core.calendar_policy import list_calendar_policy_catalog_entries
from app.api.models.compatibility_profile import (
    CompatibilityProfileCreateRequest,
    CompatibilityProfileRevalidateRequest,
    CompatibilityProfileUpdateRequest,
)

from .common import SemanticServiceSupport, _catalog_metadata_json, now_iso


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
        if payload.requirement is not None:
            self._validate_profile_entity_centric_requirements(
                payload.requirement.model_dump(mode="json")
            )
        profile_id = f"cprof_{uuid4().hex[:24]}"
        created_at = now_iso()
        self.metadata.execute(
            """
            INSERT INTO compiler_compatibility_profiles (
                profile_id, profile_ref, profile_kind, schema_version, subject_kind,
                subject_ref, subject_revision, requirement_json, capability_json,
                catalog_metadata_json, status, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
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
                _catalog_metadata_json(payload.catalog_metadata),
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
        subject_kind: str | None = None,
        subject_ref: str | None = None,
        left_entity_ref: str | None = None,
        right_entity_ref: str | None = None,
    ) -> dict[str, Any]:
        status = self._resolve_semantic_filters(status=status, lifecycle_status=lifecycle_status)
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if subject_kind is not None:
            clauses.append("subject_kind = ?")
            params.append(subject_kind)
        if subject_ref is not None:
            clauses.append("subject_ref = ?")
            params.append(subject_ref)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.metadata.query_rows(
            f"SELECT * FROM compiler_compatibility_profiles {where_sql} ORDER BY profile_ref",
            params,
        )
        mode: Literal["list", "detail"] = "detail" if detail else "list"
        items = [
            item
            for row in rows
            if self._profile_matches_entity_pair(
                self._row_to_compatibility_profile(row, mode="detail", include_dependents=False),
                left_entity_ref=left_entity_ref,
                right_entity_ref=right_entity_ref,
            )
            if self._matches_readiness_filter(
                item := self._row_to_compatibility_profile(
                    row, mode=mode, include_dependents=detail
                ),
                readiness_status=readiness_status,
            )
        ]
        include_builtin_profiles = (
            subject_kind is None
            and subject_ref is None
            and left_entity_ref is None
            and right_entity_ref is None
        )
        if include_builtin_profiles and status in (None, "published"):
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
            requirement_payload = payload.requirement.model_dump(mode="json")
            self._validate_profile_entity_centric_requirements(requirement_payload)
            updates.append("requirement_json = ?")
            params.append(json.dumps(requirement_payload))
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
        if payload.catalog_metadata is not None:
            updates.append("catalog_metadata_json = ?")
            params.append(_catalog_metadata_json(payload.catalog_metadata))
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
        if current.get("requirement") is not None:
            self._validate_profile_entity_centric_requirements(
                current["requirement"], require_published=False
            )
        return self.get_compatibility_profile(profile_id)

    def revalidate_compatibility_profile(
        self, profile_id_or_ref: str, payload: CompatibilityProfileRevalidateRequest
    ) -> dict[str, Any]:
        self._reject_builtin_calendar_policy_action(profile_id_or_ref, action="revalidate")
        current = self.read_compatibility_profile(profile_id_or_ref)
        self._validate_profile_subject_ref(
            current["subject_kind"],
            current["subject_ref"],
            require_published=True,
        )
        if current.get("requirement") is not None:
            self._validate_profile_entity_centric_requirements(
                current["requirement"], require_published=True
            )
        active_subject_revision = self._published_profile_subject_revision(
            current["subject_kind"],
            current["subject_ref"],
        )
        subject_revision = payload.subject_revision
        if subject_revision is None:
            subject_revision = active_subject_revision
        elif not self._profile_subject_revision_exists(
            current["subject_kind"],
            current["subject_ref"],
            subject_revision,
        ):
            raise self._compatibility_error(
                "Compatibility profile subject_revision must reference an existing subject revision.",
                code="profile_subject_revision_unknown",
            )
        self.metadata.execute(
            """
            UPDATE compiler_compatibility_profiles
            SET subject_revision = ?,
                revision = revision + 1,
                updated_at = ?
            WHERE profile_id = ?
            """,
            [subject_revision, now_iso(), current["profile_id"]],
        )
        return self.get_compatibility_profile(current["profile_id"])

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
            lambda: self._validate_profile_publish_contract(current)
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
                    "resolved_calendar_source": "calendar",
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

    def _validate_profile_publish_contract(self, current: dict[str, Any]) -> None:
        self._validate_profile_subject_ref(
            current["subject_kind"],
            current["subject_ref"],
            require_published=True,
        )
        if current.get("requirement") is not None:
            self._validate_profile_entity_centric_requirements(
                current["requirement"], require_published=True
            )

    def _profile_matches_entity_pair(
        self,
        profile: dict[str, Any],
        *,
        left_entity_ref: str | None,
        right_entity_ref: str | None,
    ) -> bool:
        if left_entity_ref is None and right_entity_ref is None:
            return True
        requirement = dict(profile.get("requirement") or {})
        entity_refs = {str(ref) for ref in requirement.get("entity_refs") or []}
        relationship_refs = [
            str(ref) for ref in requirement.get("required_relationship_refs") or []
        ]
        for relationship_ref in relationship_refs:
            row = self._relationship_row_by_ref(relationship_ref)
            if row is None:
                continue
            entity_refs.add(str(row["left_entity_ref"]))
            entity_refs.add(str(row["right_entity_ref"]))
        return (left_entity_ref is None or left_entity_ref in entity_refs) and (
            right_entity_ref is None or right_entity_ref in entity_refs
        )
