"""Tests for process object semantic models."""

import pytest
from pydantic import ValidationError

from app.api.models.base import PopulationSpec, StateSpec, StepSpec
from app.api.models.process_object import (
    CohortDefinitionPayload,
    ContextProcessContract,
    EntityProcessContract,
    FunnelDefinitionPayload,
    LifecycleStateMachinePayload,
    ProcessObjectCreateRequest,
    ProcessObjectHeader,
)


class TestProcessObjectCreateRequest:
    """Tests for process object discriminated unions and cross-field validation."""

    def test_valid_cohort_definition_request(self):
        request = ProcessObjectCreateRequest(
            header=ProcessObjectHeader(
                process_ref="process.new_user_cohort",
                process_type="cohort_definition",
                process_contract_version="process.v2",
            ),
            interface_contract=ContextProcessContract(
                contract_mode="context_provider",
                context_kind="cohort_membership",
                population_subject_ref="subject.user",
                membership_cardinality="exclusive_one",
            ),
            payload=CohortDefinitionPayload(
                process_type="cohort_definition",
                cohort_key="new_users",
                entry_population=PopulationSpec(base_population_ref="population.users"),
                cohort_anchor_ref="time.signup_time",
            ),
            catalog_metadata={"domain_ref": "domain.growth", "aliases": ["New User Cohort"]},
        )

        assert request.header.process_type == "cohort_definition"
        assert request.payload.process_type == "cohort_definition"
        assert request.catalog_metadata.domain_ref == "domain.growth"
        assert request.catalog_metadata.aliases == ["New User Cohort"]

    def test_create_request_rejects_invalid_catalog_domain_ref(self):
        with pytest.raises(ValidationError, match=r"'domain_ref' must start with 'domain\.'"):
            ProcessObjectCreateRequest(
                header=ProcessObjectHeader(
                    process_ref="process.new_user_cohort",
                    process_type="cohort_definition",
                    process_contract_version="process.v2",
                ),
                interface_contract=ContextProcessContract(
                    contract_mode="context_provider",
                    context_kind="cohort_membership",
                    population_subject_ref="subject.user",
                    membership_cardinality="exclusive_one",
                ),
                payload=CohortDefinitionPayload(
                    process_type="cohort_definition",
                    cohort_key="new_users",
                    entry_population=PopulationSpec(base_population_ref="population.users"),
                    cohort_anchor_ref="time.signup_time",
                ),
                catalog_metadata={"domain_ref": "process.cohort"},
            )

    def test_rejects_mismatched_header_and_payload_process_type(self):
        with pytest.raises(
            ValidationError,
            match=r"header\.process_type \(funnel_definition\) must match payload\.process_type",
        ):
            ProcessObjectCreateRequest(
                header=ProcessObjectHeader(
                    process_ref="process.new_user_cohort",
                    process_type="funnel_definition",
                    process_contract_version="process.v2",
                ),
                interface_contract=ContextProcessContract(
                    contract_mode="context_provider",
                    context_kind="cohort_membership",
                    population_subject_ref="subject.user",
                    membership_cardinality="exclusive_one",
                ),
                payload=CohortDefinitionPayload(
                    process_type="cohort_definition",
                    cohort_key="new_users",
                    entry_population=PopulationSpec(base_population_ref="population.users"),
                    cohort_anchor_ref="time.signup_time",
                ),
            )

    def test_rejects_mismatched_contract_mode_for_context_process(self):
        with pytest.raises(
            ValidationError,
            match=r"interface_contract\.contract_mode \(entity_stream\) must be 'context_provider'",
        ):
            ProcessObjectCreateRequest(
                header=ProcessObjectHeader(
                    process_ref="process.new_user_cohort",
                    process_type="cohort_definition",
                    process_contract_version="process.v2",
                ),
                interface_contract=EntityProcessContract(
                    contract_mode="entity_stream",
                    entity_ref="entity.cohort_membership",
                    emitted_grain_ref="grain.user",
                    population_subject_ref="subject.user",
                    subject_cardinality="many",
                ),
                payload=CohortDefinitionPayload(
                    process_type="cohort_definition",
                    cohort_key="new_users",
                    entry_population=PopulationSpec(base_population_ref="population.users"),
                    cohort_anchor_ref="time.signup_time",
                ),
            )

    def test_step_event_ref_accepts_entity_field_and_predicate_refs(self):
        entity_field_step = StepSpec(
            step_key="view_product",
            event_ref="entity.behavior_event.field.product_viewed",
            qualifier_refs=["predicate.valid_session"],
        )
        predicate_step = StepSpec(
            step_key="submit_order",
            event_ref="predicate.order_submitted",
        )

        assert entity_field_step.event_ref == "entity.behavior_event.field.product_viewed"
        assert predicate_step.event_ref == "predicate.order_submitted"

    def test_step_event_ref_rejects_physical_field_surface_ref(self):
        with pytest.raises(ValidationError, match="event_ref"):
            StepSpec(step_key="view_product", event_ref="field.product_viewed")

    def test_state_refs_accept_predicate_or_entity_field_refs(self):
        payload = LifecycleStateMachinePayload(
            process_type="lifecycle_state_machine",
            machine_key="customer_state",
            states=[
                StateSpec(
                    state_key="active",
                    entry_ref="predicate.active_customer",
                    exit_ref="entity.customer.field.churned_at",
                )
            ],
        )

        assert payload.states[0].entry_ref == "predicate.active_customer"
        assert payload.states[0].exit_ref == "entity.customer.field.churned_at"

    def test_checkout_funnel_refs_entity_fields_time_and_predicates(self):
        request = ProcessObjectCreateRequest(
            header=ProcessObjectHeader(
                process_ref="process.checkout_funnel",
                process_type="funnel_definition",
                process_contract_version="process.v2",
            ),
            interface_contract=EntityProcessContract(
                contract_mode="entity_stream",
                entity_ref="entity.checkout_event",
                emitted_grain_ref="grain.user_session",
                population_subject_ref="subject.user",
                subject_cardinality="many",
                anchor_time_ref="time.checkout_event_at",
            ),
            payload=FunnelDefinitionPayload(
                process_type="funnel_definition",
                funnel_key="checkout",
                steps=[
                    StepSpec(
                        step_key="cart",
                        event_ref="entity.checkout_event.field.cart_event",
                        qualifier_refs=["predicate.valid_checkout_event"],
                    ),
                    StepSpec(
                        step_key="payment",
                        event_ref="entity.checkout_event.field.payment_event",
                    ),
                    StepSpec(
                        step_key="success",
                        event_ref="entity.checkout_event.field.success_event",
                    ),
                ],
                ordering_rule="strict",
                max_step_gap={"value": 30, "unit": "minute"},
                conversion_step_key="success",
                partition_scope="same_session",
            ),
        )

        assert request.payload.max_step_gap is not None
        assert request.payload.steps[0].event_ref == "entity.checkout_event.field.cart_event"
        assert request.interface_contract.anchor_time_ref == "time.checkout_event_at"
