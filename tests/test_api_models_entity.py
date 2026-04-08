"""Tests for entity semantic object models."""

import pytest
from pydantic import ValidationError

from app.api.models.entity import (
    EntityHeader,
    EntityHierarchySpec,
    EntityIdentitySpec,
    EntityInterfaceContract,
    StableDescriptorSpec,
    TypedEntityCreateRequest,
    TypedEntityResponse,
    TypedEntityUpdateRequest,
)


class TestEntityHeader:
    """Tests for EntityHeader model."""

    def test_valid_header(self):
        header = EntityHeader(
            entity_ref="entity.user",
            display_name="User",
            description="Platform user entity",
            entity_contract_version="entity.v4",
        )
        assert header.entity_ref == "entity.user"
        assert header.display_name == "User"
        assert header.entity_contract_version == "entity.v4"

    def test_minimal_header(self):
        header = EntityHeader(
            entity_ref="entity.session",
            entity_contract_version="entity.v4",
        )
        assert header.entity_ref == "entity.session"
        assert header.display_name is None
        assert header.description is None

    def test_invalid_entity_ref_prefix(self):
        with pytest.raises(ValidationError, match=r"entity_ref must start with 'entity\.'"):
            EntityHeader(
                entity_ref="wrong.user",
                entity_contract_version="entity.v4",
            )

    def test_invalid_contract_version_prefix(self):
        with pytest.raises(ValidationError, match=r"contract_version must start with 'entity\.'"):
            EntityHeader(
                entity_ref="entity.user",
                entity_contract_version="wrong.v1",
            )


class TestEntityIdentitySpec:
    """Tests for EntityIdentitySpec model."""

    def test_valid_identity_global(self):
        identity = EntityIdentitySpec(
            key_refs=["key.user_id"],
            uniqueness_scope="global",
            id_stability="stable",
        )
        assert identity.key_refs == ["key.user_id"]
        assert identity.uniqueness_scope == "global"
        assert identity.id_stability == "stable"

    def test_valid_identity_parent_scoped(self):
        identity = EntityIdentitySpec(
            key_refs=["key.session_id"],
            uniqueness_scope="parent_scoped",
            id_stability="ephemeral",
            nullable_key_policy="reject",
        )
        assert identity.uniqueness_scope == "parent_scoped"
        assert identity.nullable_key_policy == "reject"

    def test_multiple_keys(self):
        identity = EntityIdentitySpec(
            key_refs=["key.user_id", "key.tenant_id"],
            uniqueness_scope="global",
            id_stability="stable",
        )
        assert len(identity.key_refs) == 2

    def test_empty_key_refs_rejected(self):
        with pytest.raises(ValidationError):
            EntityIdentitySpec(
                key_refs=[],
                uniqueness_scope="global",
                id_stability="stable",
            )

    def test_invalid_key_ref_prefix(self):
        with pytest.raises(ValidationError, match=r"key_refs must start with 'key\.'"):
            EntityIdentitySpec(
                key_refs=["wrong.user_id"],
                uniqueness_scope="global",
                id_stability="stable",
            )


class TestEntityHierarchySpec:
    """Tests for EntityHierarchySpec model."""

    def test_valid_no_parent(self):
        hierarchy = EntityHierarchySpec()
        assert hierarchy.parent_entity_ref is None
        assert hierarchy.cardinality_to_parent is None

    def test_valid_with_parent(self):
        hierarchy = EntityHierarchySpec(
            parent_entity_ref="entity.user",
            cardinality_to_parent="many_to_one",
            ownership_semantics="belongs_to",
        )
        assert hierarchy.parent_entity_ref == "entity.user"
        assert hierarchy.cardinality_to_parent == "many_to_one"

    def test_parent_requires_cardinality(self):
        with pytest.raises(ValidationError, match="cardinality_to_parent is required"):
            EntityHierarchySpec(parent_entity_ref="entity.user")

    def test_parent_requires_ownership(self):
        with pytest.raises(ValidationError, match="ownership_semantics is required"):
            EntityHierarchySpec(
                parent_entity_ref="entity.user",
                cardinality_to_parent="many_to_one",
            )

    def test_invalid_parent_entity_ref_prefix(self):
        with pytest.raises(ValidationError, match=r"parent_entity_ref must start with 'entity\.'"):
            EntityHierarchySpec(
                parent_entity_ref="wrong.user",
                cardinality_to_parent="many_to_one",
                ownership_semantics="belongs_to",
            )


class TestStableDescriptorSpec:
    """Tests for StableDescriptorSpec model."""

    def test_valid_descriptor(self):
        desc = StableDescriptorSpec(
            dimension_ref="dimension.country",
            cardinality="one",
        )
        assert desc.dimension_ref == "dimension.country"
        assert desc.cardinality == "one"

    def test_valid_without_cardinality(self):
        desc = StableDescriptorSpec(dimension_ref="dimension.platform")
        assert desc.cardinality is None

    def test_invalid_dimension_ref_prefix(self):
        with pytest.raises(ValidationError, match=r"dimension_ref must start with 'dimension\.'"):
            StableDescriptorSpec(dimension_ref="wrong.country")


class TestEntityInterfaceContract:
    """Tests for EntityInterfaceContract model."""

    def test_valid_minimal_contract(self):
        contract = EntityInterfaceContract(
            identity=EntityIdentitySpec(
                key_refs=["key.user_id"],
                uniqueness_scope="global",
                id_stability="stable",
            ),
        )
        assert contract.identity.key_refs == ["key.user_id"]
        assert contract.hierarchy is None
        assert contract.primary_time_ref is None

    def test_valid_full_contract(self):
        contract = EntityInterfaceContract(
            identity=EntityIdentitySpec(
                key_refs=["key.session_id"],
                uniqueness_scope="parent_scoped",
                id_stability="ephemeral",
            ),
            hierarchy=EntityHierarchySpec(
                parent_entity_ref="entity.user",
                cardinality_to_parent="many_to_one",
                ownership_semantics="belongs_to",
            ),
            primary_time_ref="time.session_started_at",
            stable_descriptors=[
                StableDescriptorSpec(dimension_ref="dimension.device_type"),
            ],
        )
        assert contract.primary_time_ref == "time.session_started_at"
        assert len(contract.stable_descriptors) == 1

    def test_invalid_primary_time_ref_prefix(self):
        with pytest.raises(ValidationError, match=r"primary_time_ref must start with 'time\.'"):
            EntityInterfaceContract(
                identity=EntityIdentitySpec(
                    key_refs=["key.user_id"],
                    uniqueness_scope="global",
                    id_stability="stable",
                ),
                primary_time_ref="wrong.time",
            )


class TestTypedEntityCreateRequest:
    """Tests for TypedEntityCreateRequest model."""

    def test_valid_create_request(self):
        request = TypedEntityCreateRequest(
            header=EntityHeader(
                entity_ref="entity.user",
                display_name="User",
                entity_contract_version="entity.v4",
            ),
            interface_contract=EntityInterfaceContract(
                identity=EntityIdentitySpec(
                    key_refs=["key.user_id"],
                    uniqueness_scope="global",
                    id_stability="stable",
                ),
                primary_time_ref="time.user_created_at",
            ),
        )
        assert request.header.entity_ref == "entity.user"
        assert request.interface_contract.primary_time_ref == "time.user_created_at"


class TestTypedEntityUpdateRequest:
    """Tests for TypedEntityUpdateRequest model."""

    def test_empty_update(self):
        update = TypedEntityUpdateRequest()
        assert update.display_name is None
        assert update.interface_contract is None

    def test_partial_update(self):
        update = TypedEntityUpdateRequest(display_name="Updated Name")
        assert update.display_name == "Updated Name"


class TestTypedEntityResponse:
    """Tests for TypedEntityResponse model."""

    def test_valid_response(self):
        response = TypedEntityResponse(
            entity_contract_id="ec_123",
            header=EntityHeader(
                entity_ref="entity.user",
                entity_contract_version="entity.v4",
            ),
            interface_contract=EntityInterfaceContract(
                identity=EntityIdentitySpec(
                    key_refs=["key.user_id"],
                    uniqueness_scope="global",
                    id_stability="stable",
                ),
            ),
            status="draft",
            revision=1,
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )
        assert response.entity_contract_id == "ec_123"
        assert response.status == "draft"
        assert response.revision == 1
