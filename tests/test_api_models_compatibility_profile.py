"""Tests for compatibility profile models."""

import pytest
from pydantic import ValidationError

from app.api.models.compatibility_profile import CompatibilityProfileCreateRequest


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
