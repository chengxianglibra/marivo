from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from app.api.models.binding import TypedBindingCreateRequest, TypedBindingUpdateRequest
from app.api.models.compatibility_profile import (
    CompatibilityProfileCreateRequest,
    CompatibilityProfileUpdateRequest,
)
from app.api.models.dimension import DimensionCreateRequest, DimensionUpdateRequest
from app.api.models.entity import TypedEntityCreateRequest, TypedEntityUpdateRequest
from app.api.models.enum_set import EnumSetCreateRequest, EnumSetUpdateRequest
from app.api.models.metric import TypedMetricCreateRequest, TypedMetricUpdateRequest
from app.api.models.process_object import ProcessObjectCreateRequest, ProcessObjectUpdateRequest
from app.api.models.time import TimeCreateRequest, TimeUpdateRequest
from app.semantic_service import (
    CompatibilityProfileService,
    LegacySemanticService,
    SemanticNotFoundError,
    SemanticServiceError,
    SemanticStateError,
    SemanticValidationError,
    TypedBindingService,
    TypedObjectService,
)
from app.storage.metadata import MetadataStore

ActionResultT = TypeVar("ActionResultT")


class SemanticService:
    """Facade that preserves the existing service contract while delegating by concern."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata
        self.legacy = LegacySemanticService(metadata)
        self.typed_objects = TypedObjectService(metadata)
        self.bindings = TypedBindingService(metadata)
        self.compatibility_profiles = CompatibilityProfileService(metadata)

    def _invoke(self, action: Callable[[], ActionResultT]) -> ActionResultT:
        try:
            return action()
        except SemanticNotFoundError as error:
            raise KeyError(str(error)) from error
        except (SemanticValidationError, SemanticStateError) as error:
            raise ValueError(str(error)) from error
        except SemanticServiceError as error:
            raise ValueError(str(error)) from error

    def create_entity(
        self,
        name: str,
        display_name: str,
        keys: list[str],
        description: str = "",
        level: str | None = None,
        join_constraints: dict[str, Any] | None = None,
        upstream_dependencies: list[str] | None = None,
        lineage: list[str] | None = None,
        quality_expectations: dict[str, Any] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            lambda: self.legacy.create_entity(
                name=name,
                display_name=display_name,
                keys=keys,
                description=description,
                level=level,
                join_constraints=join_constraints,
                upstream_dependencies=upstream_dependencies,
                lineage=lineage,
                quality_expectations=quality_expectations,
                properties=properties,
            )
        )

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.legacy.get_entity(entity_id))

    def list_entities(self, status: str | None = None) -> list[dict[str, Any]]:
        return self._invoke(lambda: self.legacy.list_entities(status=status))

    def update_entity(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._invoke(lambda: self.legacy.update_entity(entity_id, **kwargs))

    def patch_entity_properties(
        self, entity_id: str, properties_patch: dict[str, Any]
    ) -> dict[str, Any]:
        return self._invoke(
            lambda: self.legacy.patch_entity_properties(entity_id, properties_patch)
        )

    def publish_entity(self, entity_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.legacy.publish_entity(entity_id))

    def deprecate_entity(self, entity_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.legacy.deprecate_entity(entity_id))

    def create_metric(
        self,
        name: str,
        display_name: str,
        definition_sql: str,
        dimensions: list[str],
        description: str = "",
        entity_id: str | None = None,
        grain: str | None = None,
        measure_type: str | None = None,
        allowed_dimensions: list[str] | None = None,
        lineage: list[str] | None = None,
        quality_expectations: dict[str, Any] | None = None,
        properties: dict[str, Any] | None = None,
        desired_direction: str | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            lambda: self.legacy.create_metric(
                name=name,
                display_name=display_name,
                definition_sql=definition_sql,
                dimensions=dimensions,
                description=description,
                entity_id=entity_id,
                grain=grain,
                measure_type=measure_type,
                allowed_dimensions=allowed_dimensions,
                lineage=lineage,
                quality_expectations=quality_expectations,
                properties=properties,
                desired_direction=desired_direction,
            )
        )

    def get_metric(self, metric_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.legacy.get_metric(metric_id))

    def list_metrics(self, status: str | None = None) -> list[dict[str, Any]]:
        return self._invoke(lambda: self.legacy.list_metrics(status=status))

    def update_metric(self, metric_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._invoke(lambda: self.legacy.update_metric(metric_id, **kwargs))

    def publish_metric(self, metric_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.legacy.publish_metric(metric_id))

    def create_mapping(
        self,
        semantic_type: str,
        semantic_id: str,
        object_id: str,
        mapping_type: str,
        mapping_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            lambda: self.legacy.create_mapping(
                semantic_type=semantic_type,
                semantic_id=semantic_id,
                object_id=object_id,
                mapping_type=mapping_type,
                mapping_json=mapping_json,
            )
        )

    def delete_mapping(self, mapping_id: str) -> None:
        self._invoke(lambda: self.legacy.delete_mapping(mapping_id))

    def list_mappings(
        self,
        semantic_type: str | None = None,
        semantic_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._invoke(
            lambda: self.legacy.list_mappings(semantic_type=semantic_type, semantic_id=semantic_id)
        )

    def create_typed_entity(self, payload: TypedEntityCreateRequest) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.create_typed_entity(payload))

    def get_typed_entity(self, entity_contract_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.get_typed_entity(entity_contract_id))

    def list_typed_entities(self, status: str | None = None) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.list_typed_entities(status=status))

    def update_typed_entity(
        self, entity_contract_id: str, payload: TypedEntityUpdateRequest
    ) -> dict[str, Any]:
        return self._invoke(
            lambda: self.typed_objects.update_typed_entity(entity_contract_id, payload)
        )

    def publish_typed_entity(self, entity_contract_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.publish_typed_entity(entity_contract_id))

    def create_typed_metric(self, payload: TypedMetricCreateRequest) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.create_typed_metric(payload))

    def get_typed_metric(self, metric_contract_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.get_typed_metric(metric_contract_id))

    def list_typed_metrics(self, status: str | None = None) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.list_typed_metrics(status=status))

    def update_typed_metric(
        self, metric_contract_id: str, payload: TypedMetricUpdateRequest
    ) -> dict[str, Any]:
        return self._invoke(
            lambda: self.typed_objects.update_typed_metric(metric_contract_id, payload)
        )

    def publish_typed_metric(self, metric_contract_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.publish_typed_metric(metric_contract_id))

    def create_process_object(self, payload: ProcessObjectCreateRequest) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.create_process_object(payload))

    def get_process_object(self, process_contract_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.get_process_object(process_contract_id))

    def list_process_objects(self, status: str | None = None) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.list_process_objects(status=status))

    def update_process_object(
        self, process_contract_id: str, payload: ProcessObjectUpdateRequest
    ) -> dict[str, Any]:
        return self._invoke(
            lambda: self.typed_objects.update_process_object(process_contract_id, payload)
        )

    def publish_process_object(self, process_contract_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.publish_process_object(process_contract_id))

    def create_dimension(self, payload: DimensionCreateRequest) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.create_dimension(payload))

    def get_dimension(self, dimension_contract_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.get_dimension(dimension_contract_id))

    def list_dimensions(self, status: str | None = None) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.list_dimensions(status=status))

    def update_dimension(
        self, dimension_contract_id: str, payload: DimensionUpdateRequest
    ) -> dict[str, Any]:
        return self._invoke(
            lambda: self.typed_objects.update_dimension(dimension_contract_id, payload)
        )

    def publish_dimension(self, dimension_contract_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.publish_dimension(dimension_contract_id))

    def create_time_semantic(self, payload: TimeCreateRequest) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.create_time_semantic(payload))

    def get_time_semantic(self, time_contract_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.get_time_semantic(time_contract_id))

    def list_time_semantics(self, status: str | None = None) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.list_time_semantics(status=status))

    def update_time_semantic(
        self, time_contract_id: str, payload: TimeUpdateRequest
    ) -> dict[str, Any]:
        return self._invoke(
            lambda: self.typed_objects.update_time_semantic(time_contract_id, payload)
        )

    def publish_time_semantic(self, time_contract_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.publish_time_semantic(time_contract_id))

    def create_enum_set(self, payload: EnumSetCreateRequest) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.create_enum_set(payload))

    def get_enum_set(self, enum_set_contract_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.get_enum_set(enum_set_contract_id))

    def list_enum_sets(self, status: str | None = None) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.list_enum_sets(status=status))

    def update_enum_set(
        self, enum_set_contract_id: str, payload: EnumSetUpdateRequest
    ) -> dict[str, Any]:
        return self._invoke(
            lambda: self.typed_objects.update_enum_set(enum_set_contract_id, payload)
        )

    def publish_enum_set(self, enum_set_contract_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.typed_objects.publish_enum_set(enum_set_contract_id))

    def create_typed_binding(self, payload: TypedBindingCreateRequest) -> dict[str, Any]:
        return self._invoke(lambda: self.bindings.create_typed_binding(payload))

    def get_typed_binding(self, binding_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.bindings.get_typed_binding(binding_id))

    def list_typed_bindings(self, status: str | None = None) -> dict[str, Any]:
        return self._invoke(lambda: self.bindings.list_typed_bindings(status=status))

    def update_typed_binding(
        self, binding_id: str, payload: TypedBindingUpdateRequest
    ) -> dict[str, Any]:
        return self._invoke(lambda: self.bindings.update_typed_binding(binding_id, payload))

    def publish_typed_binding(self, binding_id: str) -> dict[str, Any]:
        return self._invoke(lambda: self.bindings.publish_typed_binding(binding_id))

    def create_compatibility_profile(
        self, payload: CompatibilityProfileCreateRequest
    ) -> dict[str, Any]:
        return self._invoke(
            lambda: self.compatibility_profiles.create_compatibility_profile(payload)
        )

    def get_compatibility_profile(self, profile_id: str) -> dict[str, Any]:
        return self._invoke(
            lambda: self.compatibility_profiles.get_compatibility_profile(profile_id)
        )

    def list_compatibility_profiles(self, status: str | None = None) -> dict[str, Any]:
        return self._invoke(
            lambda: self.compatibility_profiles.list_compatibility_profiles(status=status)
        )

    def update_compatibility_profile(
        self, profile_id: str, payload: CompatibilityProfileUpdateRequest
    ) -> dict[str, Any]:
        return self._invoke(
            lambda: self.compatibility_profiles.update_compatibility_profile(profile_id, payload)
        )

    def publish_compatibility_profile(self, profile_id: str) -> dict[str, Any]:
        return self._invoke(
            lambda: self.compatibility_profiles.publish_compatibility_profile(profile_id)
        )
