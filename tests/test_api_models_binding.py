"""Tests for typed binding models."""

import pytest
from pydantic import ValidationError

from app.api.models.binding import BindingHeader, TypedBindingCreateRequest


class TestBindingModels:
    def test_binding_scope_must_match_bound_object_ref(self):
        with pytest.raises(ValidationError, match=r"bound_object_ref must start with 'entity\.'"):
            BindingHeader(
                binding_ref="binding.user_binding",
                binding_scope="entity",
                bound_object_ref="metric.dau",
                binding_contract_version="binding.v1",
            )

    def test_valid_binding_create_request(self):
        request = TypedBindingCreateRequest(
            header=BindingHeader(
                binding_ref="binding.user_binding",
                binding_scope="entity",
                bound_object_ref="entity.user",
                binding_contract_version="binding.v1",
            ),
            interface_contract={},
        )
        assert request.header.binding_ref == "binding.user_binding"
