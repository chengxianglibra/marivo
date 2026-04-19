"""Tests for process object semantic models."""

import pytest
from pydantic import ValidationError

from app.api.models.base import PopulationSpec
from app.api.models.process_object import (
    CohortDefinitionPayload,
    ContextProcessContract,
    EntityProcessContract,
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
        )

        assert request.header.process_type == "cohort_definition"
        assert request.payload.process_type == "cohort_definition"

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
