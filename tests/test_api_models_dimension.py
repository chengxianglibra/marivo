"""Tests for dimension semantic object models."""

import pytest
from pydantic import ValidationError

from app.api.models.dimension import (
    DimensionCreateRequest,
    DimensionHeader,
    DimensionInterfaceContract,
    DimensionValueDomainSpec,
    TimeDerivedRequirementSpec,
)


class TestDimensionModels:
    def test_enumerated_domain_requires_enum_fields(self):
        with pytest.raises(ValidationError, match="enum_set_ref is required"):
            DimensionValueDomainSpec(
                structure_kind="flat",
                value_type="string",
                domain_kind="enumerated",
            )

    def test_time_derived_requires_requirement(self):
        with pytest.raises(ValidationError, match="time_derived_requirement is required"):
            DimensionInterfaceContract(
                value_domain=DimensionValueDomainSpec(
                    structure_kind="time_derived",
                    value_type="string",
                    domain_kind="open",
                )
            )

    def test_valid_time_derived_create_request(self):
        request = DimensionCreateRequest(
            header=DimensionHeader(
                dimension_ref="dimension.signup_week",
                display_name="Signup Week",
                dimension_contract_version="dimension.v1",
            ),
            interface_contract=DimensionInterfaceContract(
                value_domain=DimensionValueDomainSpec(
                    structure_kind="time_derived",
                    value_type="string",
                    domain_kind="open",
                ),
                time_derived_requirement=TimeDerivedRequirementSpec(
                    required_time_anchor_ref="time.signup_time"
                ),
            ),
            catalog_metadata={"domain_ref": "domain.growth", "aliases": ["Signup Week"]},
        )
        assert request.header.dimension_ref == "dimension.signup_week"
        assert request.catalog_metadata.domain_ref == "domain.growth"
        assert request.catalog_metadata.aliases == ["Signup Week"]

    def test_create_request_rejects_invalid_catalog_domain_ref(self):
        with pytest.raises(ValidationError, match=r"'domain_ref' must start with 'domain\.'"):
            DimensionCreateRequest(
                header=DimensionHeader(
                    dimension_ref="dimension.signup_week",
                    display_name="Signup Week",
                    dimension_contract_version="dimension.v1",
                ),
                interface_contract=DimensionInterfaceContract(
                    value_domain=DimensionValueDomainSpec(
                        structure_kind="time_derived",
                        value_type="string",
                        domain_kind="open",
                    ),
                    time_derived_requirement=TimeDerivedRequirementSpec(
                        required_time_anchor_ref="time.signup_time"
                    ),
                ),
                catalog_metadata={"domain_ref": "metric.gmv"},
            )

    def test_source_field_ref_requires_entity_field_ref(self):
        with pytest.raises(ValidationError, match="fully qualified entity field"):
            DimensionInterfaceContract(
                source_field_ref="field.country",
                value_domain=DimensionValueDomainSpec(
                    structure_kind="flat",
                    value_type="string",
                    domain_kind="open",
                ),
            )

    def test_source_field_ref_accepts_entity_field_ref(self):
        contract = DimensionInterfaceContract(
            source_field_ref="entity.user.field.country",
            value_domain=DimensionValueDomainSpec(
                structure_kind="flat",
                value_type="string",
                domain_kind="open",
            ),
        )

        assert contract.source_field_ref == "entity.user.field.country"
