"""Tests for time semantic object models."""

import pytest
from pydantic import ValidationError

from app.api.models.time import TimeCreateRequest, TimeSemanticHeader


class TestTimeModels:
    def test_semantic_roles_must_be_non_empty(self):
        with pytest.raises(ValidationError, match="semantic_roles must be non-empty"):
            TimeSemanticHeader(
                time_ref="time.signup_time",
                semantic_roles=[],
                time_contract_version="time.v1",
            )

    def test_valid_time_create_request(self):
        request = TimeCreateRequest(
            header=TimeSemanticHeader(
                time_ref="time.signup_time",
                display_name="Signup Time",
                semantic_roles=["business_anchor", "measurement"],
                time_contract_version="time.v1",
            )
        )
        assert request.header.time_ref == "time.signup_time"
