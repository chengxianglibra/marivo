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
        )
        assert request.header.dimension_ref == "dimension.signup_week"
