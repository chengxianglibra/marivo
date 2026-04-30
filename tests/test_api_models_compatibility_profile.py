"""Tests for compatibility profile models."""

import pytest
from pydantic import ValidationError

from app.api.models.compatibility_profile import (
    CompatibilityProfileCreateRequest,
    EntityRelationshipCreateRequest,
)


class TestCompatibilityProfileModels:
    def test_subject_kind_and_profile_kind_must_match(self):
        with pytest.raises(ValidationError, match="Invalid combination"):
            CompatibilityProfileCreateRequest(
                profile_ref="compiler_profile.metric_capability",
                profile_kind="capability",
                subject_kind="metric",
                subject_ref="metric.dau",
                capability={"inferential_ready": True},
            )

    def test_requirement_profile_requires_requirement_payload(self):
        with pytest.raises(ValidationError, match="requirement is required"):
            CompatibilityProfileCreateRequest(
                profile_ref="compiler_profile.metric_requirement",
                profile_kind="requirement",
                subject_kind="metric",
                subject_ref="metric.dau",
            )

    def test_valid_capability_profile(self):
        request = CompatibilityProfileCreateRequest(
            profile_ref="compiler_profile.binding_capability",
            profile_kind="capability",
            subject_kind="binding",
            subject_ref="binding.user_binding",
            capability={"inferential_ready": True},
        )
        assert request.subject_ref == "binding.user_binding"

    def test_relationship_rejects_raw_sql_shape(self):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            EntityRelationshipCreateRequest(
                relationship_ref="relationship.exposure_to_conversion",
                left_entity_ref="entity.exposure",
                right_entity_ref="entity.conversion",
                key_alignment={
                    "left_field_ref": "entity.exposure.field.user_id",
                    "right_field_ref": "entity.conversion.field.user_id",
                },
                cardinality="many_to_many",
                sql="SELECT * FROM exposure JOIN conversion USING (user_id)",
            )

    def test_profile_rejects_generic_rule_engine_fields(self):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            CompatibilityProfileCreateRequest(
                profile_ref="compiler_profile.cross_entity_ratio",
                profile_kind="requirement",
                subject_kind="metric",
                subject_ref="metric.conversion_rate",
                requirement={
                    "entity_refs": ["entity.exposure", "entity.conversion"],
                    "required_relationship_refs": [
                        "relationship.exposure_to_conversion",
                    ],
                    "rules": [{"when": "anything", "then": "join"}],
                },
            )
