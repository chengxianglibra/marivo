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
            ),
            catalog_metadata={
                "domain_ref": "domain.growth",
                "related_domain_refs": ["domain.core"],
            },
        )
        assert request.header.time_ref == "time.signup_time"
        assert request.catalog_metadata.domain_ref == "domain.growth"
        assert request.catalog_metadata.related_domain_refs == ["domain.core"]

    def test_create_request_rejects_invalid_catalog_domain_ref(self):
        with pytest.raises(ValidationError, match=r"'domain_ref' must start with 'domain\.'"):
            TimeCreateRequest(
                header=TimeSemanticHeader(
                    time_ref="time.signup_time",
                    semantic_roles=["business_anchor"],
                    time_contract_version="time.v1",
                ),
                catalog_metadata={"domain_ref": "time.signup_time"},
            )

    def test_source_field_ref_requires_entity_field_ref(self):
        with pytest.raises(ValidationError, match="fully qualified entity field"):
            TimeSemanticHeader(
                time_ref="time.signup_time",
                semantic_roles=["business_anchor"],
                time_contract_version="time.v1",
                source_field_ref="field.signup_time",
            )

    def test_source_field_ref_accepts_entity_field_ref(self):
        header = TimeSemanticHeader(
            time_ref="time.signup_time",
            semantic_roles=["business_anchor"],
            time_contract_version="time.v1",
            source_field_ref="entity.user.field.signup_time",
        )

        assert header.source_field_ref == "entity.user.field.signup_time"

    @pytest.mark.parametrize("legacy_field", ["binding", "physical_column", "field_bindings"])
    def test_header_rejects_legacy_physical_binding_fields(self, legacy_field):
        with pytest.raises(ValidationError, match=legacy_field):
            TimeSemanticHeader(
                time_ref="time.signup_time",
                semantic_roles=["business_anchor"],
                time_contract_version="time.v1",
                source_field_ref="entity.user.field.signup_time",
                **{legacy_field: "legacy_value"},
            )
