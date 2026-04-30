"""Tests for entity semantic object models."""

import math

import pytest
from pydantic import ValidationError

from app.api.models.entity import (
    EntityBindingSpec,
    EntityFieldSpec,
    EntityHeader,
    EntityHierarchySpec,
    EntityIdentitySpec,
    EntityInterfaceContract,
    PhysicalExpressionLocatorSpec,
    StableDescriptorSpec,
    TypedEntityCreateRequest,
    TypedEntityResponse,
    TypedEntityUpdateRequest,
)


def test_entity_contract_accepts_fields_and_binding():
    contract = EntityInterfaceContract(
        identity=EntityIdentitySpec(
            key_refs=["key.user_id"],
            uniqueness_scope="global",
            id_stability="stable",
        ),
        fields=[
            EntityFieldSpec(
                field_ref="field.user_id",
                display_name="User ID",
                value_type="string",
                nullable=False,
                physical_column="user_id",
            ),
            EntityFieldSpec(
                field_ref="field.country",
                value_type="string",
                nullable=True,
                physical_expression_locator={
                    "expression_kind": "coalesce",
                    "input_columns": ["country_code"],
                    "output_name": "country",
                    "parameters": {"fallback": "UNKNOWN"},
                },
                enum_hint="enum.country",
                sample_values=["CN", "US"],
            ),
        ],
        binding=EntityBindingSpec(
            source_object_ref="obj_users",
            source_object_fqn="main.analytics.users",
            carrier_kind="table",
        ),
    )

    assert contract.fields is not None
    assert contract.fields[0].field_ref == "field.user_id"
    assert contract.binding is not None
    assert contract.binding.source_object_fqn == "main.analytics.users"


def test_entity_contract_rejects_duplicate_field_refs():
    with pytest.raises(ValidationError, match="field_ref values must be unique"):
        EntityInterfaceContract(
            identity=EntityIdentitySpec(
                key_refs=["key.user_id"],
                uniqueness_scope="global",
                id_stability="stable",
            ),
            fields=[
                EntityFieldSpec(field_ref="field.user_id", physical_column="user_id"),
                EntityFieldSpec(field_ref="field.user_id", physical_column="account_id"),
            ],
        )


def test_entity_field_rejects_invalid_prefix():
    with pytest.raises(ValidationError, match=r"field_ref.*must start with 'field\.' prefix"):
        EntityFieldSpec(field_ref="dimension.country", physical_column="country")


def test_entity_field_rejects_role_like_properties():
    with pytest.raises(ValidationError):
        EntityFieldSpec(
            field_ref="field.user_id",
            physical_column="user_id",
            semantic_role="primary_key",
        )
    with pytest.raises(ValidationError):
        EntityFieldSpec(
            field_ref="field.user_id",
            physical_column="user_id",
            field_kind="identifier",
        )
    with pytest.raises(ValidationError):
        EntityFieldSpec(
            field_ref="field.user_id",
            physical_column="user_id",
            allowed_usages=["group_by"],
        )


def test_entity_field_accepts_controlled_expression_locator():
    field = EntityFieldSpec(
        field_ref="field.event_day",
        physical_expression_locator=PhysicalExpressionLocatorSpec(
            expression_kind="date_trunc",
            input_columns=["event_ts"],
            output_name="event_day",
            parameters={"unit": "day"},
        ),
    )

    assert field.physical_expression_locator is not None
    assert field.physical_expression_locator.expression_kind == "date_trunc"


def test_entity_field_rejects_missing_locator():
    with pytest.raises(ValidationError, match="Entity field requires one physical locator"):
        EntityFieldSpec(field_ref="field.user_id")


def test_entity_field_rejects_both_column_and_expression_locators():
    with pytest.raises(ValidationError, match="must not define both"):
        EntityFieldSpec(
            field_ref="field.event_day",
            physical_column="event_day",
            physical_expression_locator={
                "expression_kind": "date_trunc",
                "input_columns": ["event_ts"],
            },
        )


def test_entity_field_rejects_invalid_locator_names():
    with pytest.raises(ValidationError, match="physical_column"):
        EntityFieldSpec(field_ref="field.user_id", physical_column="user_id; drop table users")

    with pytest.raises(ValidationError, match="input_columns"):
        EntityFieldSpec(
            field_ref="field.event_day",
            physical_expression_locator={
                "expression_kind": "date_trunc",
                "input_columns": ["event ts"],
            },
        )


def test_expression_locator_rejects_raw_sql_like_parameters():
    for forbidden_key in ("raw_sql", "sql_expression", " raw_sql "):
        with pytest.raises(ValidationError, match="parameters must not contain"):
            PhysicalExpressionLocatorSpec(
                expression_kind="cast",
                input_columns=["price"],
                output_name="price_decimal",
                parameters={forbidden_key: "CAST(price AS DECIMAL(18, 2))"},
            )


def test_expression_locator_rejects_nested_raw_sql_like_parameters():
    with pytest.raises(ValidationError, match="parameters must not contain"):
        PhysicalExpressionLocatorSpec(
            expression_kind="bucket",
            input_columns=["price"],
            output_name="price_bucket",
            parameters={"options": [{"sql_expression": "price / 100"}]},
        )


def test_expression_locator_rejects_non_finite_parameters():
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError, match="non-finite float"):
            PhysicalExpressionLocatorSpec(
                expression_kind="bucket",
                input_columns=["price"],
                parameters={"options": [value]},
            )


def test_entity_create_request_defaults_entity_kind():
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
        ),
    )

    assert request.entity_kind == "business_entity"


def test_entity_create_request_carries_catalog_domain_metadata():
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
        ),
        catalog_metadata={
            "domain_ref": "domain.growth",
            "related_domain_refs": ["domain.core"],
            "aliases": ["Growth User"],
        },
    )

    assert request.catalog_metadata.domain_ref == "domain.growth"
    assert request.catalog_metadata.related_domain_refs == ["domain.core"]
    assert request.catalog_metadata.aliases == ["Growth User"]


def test_entity_update_request_rejects_invalid_catalog_domain_ref():
    with pytest.raises(ValidationError, match=r"'domain_ref' must start with 'domain\.'"):
        TypedEntityUpdateRequest(catalog_metadata={"domain_ref": "entity.user"})


def test_entity_create_request_accepts_entity_kind():
    request = TypedEntityCreateRequest(
        header=EntityHeader(
            entity_ref="entity.order_event",
            display_name="Order Event",
            entity_contract_version="entity.v4",
        ),
        entity_kind="event_entity",
        interface_contract=EntityInterfaceContract(
            identity=EntityIdentitySpec(
                key_refs=["key.order_event_id"],
                uniqueness_scope="global",
                id_stability="stable",
            ),
        ),
    )

    assert request.entity_kind == "event_entity"


def test_entity_create_request_rejects_unknown_entity_kind():
    with pytest.raises(ValidationError):
        TypedEntityCreateRequest(
            header=EntityHeader(
                entity_ref="entity.order",
                display_name="Order",
                entity_contract_version="entity.v4",
            ),
            entity_kind="table_entity",
            interface_contract=EntityInterfaceContract(
                identity=EntityIdentitySpec(
                    key_refs=["key.order_id"],
                    uniqueness_scope="global",
                    id_stability="stable",
                ),
            ),
        )


def test_entity_update_request_accepts_entity_kind():
    update = TypedEntityUpdateRequest(entity_kind="snapshot_entity")

    assert update.entity_kind == "snapshot_entity"


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

    def test_valid_contract_with_fields_and_binding(self):
        contract = EntityInterfaceContract(
            identity=EntityIdentitySpec(
                key_refs=["key.user_id"],
                uniqueness_scope="global",
                id_stability="stable",
            ),
            fields=[
                EntityFieldSpec(
                    field_ref="field.user_id",
                    display_name="User ID",
                    value_type="string",
                    nullable=False,
                    physical_column="user_id",
                ),
                EntityFieldSpec(
                    field_ref="field.country",
                    value_type="string",
                    nullable=True,
                    physical_expression_locator={
                        "expression_kind": "coalesce",
                        "input_columns": ["country_code"],
                        "parameters": {"fallback": "UNKNOWN"},
                    },
                    enum_hint="enum.country",
                    sample_values=["CN", "US"],
                ),
            ],
            binding=EntityBindingSpec(
                source_object_ref="obj_users",
                source_object_fqn="main.analytics.users",
                carrier_kind="table",
            ),
        )

        assert contract.fields is not None
        assert contract.fields[0].field_ref == "field.user_id"
        assert contract.binding is not None
        assert contract.binding.source_object_fqn == "main.analytics.users"

    def test_duplicate_field_refs_rejected(self):
        with pytest.raises(ValidationError, match="field_ref values must be unique"):
            EntityInterfaceContract(
                identity=EntityIdentitySpec(
                    key_refs=["key.user_id"],
                    uniqueness_scope="global",
                    id_stability="stable",
                ),
                fields=[
                    EntityFieldSpec(field_ref="field.user_id", physical_column="user_id"),
                    EntityFieldSpec(field_ref="field.user_id", physical_column="account_id"),
                ],
            )

    def test_field_ref_prefix_rejected(self):
        with pytest.raises(ValidationError, match=r"field_ref must start with 'field\.'"):
            EntityFieldSpec(field_ref="dimension.country", physical_column="country")

    def test_role_like_field_properties_rejected(self):
        with pytest.raises(ValidationError):
            EntityFieldSpec(
                field_ref="field.user_id",
                physical_column="user_id",
                semantic_role="primary_key",
            )
        with pytest.raises(ValidationError):
            EntityFieldSpec(
                field_ref="field.user_id",
                physical_column="user_id",
                field_kind="identifier",
            )
        with pytest.raises(ValidationError):
            EntityFieldSpec(
                field_ref="field.user_id",
                physical_column="user_id",
                allowed_usages=["group_by"],
            )

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
            entity_kind="business_entity",
            status="draft",
            lifecycle_status="draft",
            readiness_status="not_ready",
            blocking_requirements=[],
            capabilities={},
            revision=1,
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )
        assert response.entity_contract_id == "ec_123"
        assert response.entity_kind == "business_entity"
        assert response.status == "draft"
        assert response.lifecycle_status == "draft"
        assert response.revision == 1
