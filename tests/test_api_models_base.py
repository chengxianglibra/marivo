"""Tests for semantic layer base types and validators."""

import unittest

from pydantic import ValidationError

from app.api.models.base import (
    Additivity,
    ApiErrorDetail,
    BindingScope,
    BlockingRequirement,
    ContextKind,
    ContractMode,
    DimensionDomainKind,
    HorizonSpec,
    IdStability,
    LifecycleStatus,
    ListResponseBase,
    MetricFamily,
    ObjectHeaderBase,
    ObjectResponseBase,
    ObjectStatus,
    PopulationSpec,
    ProcessType,
    ProfileKind,
    ProfileSubjectKind,
    ReadinessStatus,
    SampleKind,
    SemanticRef,
    StateSpec,
    StepSpec,
    StructureKind,
    TimeSemanticRole,
    # Literal types
    UniquenessScope,
    ValueSemantics,
    WindowOffset,
    WindowSpec,
    validate_contract_version,
    validate_ref_prefix,
)


class TestValidateRefPrefix(unittest.TestCase):
    """Tests for ref prefix validation."""

    def test_valid_entity_ref(self):
        self.assertEqual(validate_ref_prefix("entity.user", "entity"), "entity.user")

    def test_valid_metric_ref(self):
        self.assertEqual(validate_ref_prefix("metric.dau", "metric"), "metric.dau")

    def test_valid_process_ref(self):
        self.assertEqual(validate_ref_prefix("process.exp_123", "process"), "process.exp_123")

    def test_valid_dimension_ref(self):
        self.assertEqual(validate_ref_prefix("dimension.country", "dimension"), "dimension.country")

    def test_valid_time_ref(self):
        self.assertEqual(validate_ref_prefix("time.exposure_time", "time"), "time.exposure_time")

    def test_valid_binding_ref(self):
        self.assertEqual(
            validate_ref_prefix("binding.user_identity", "binding"), "binding.user_identity"
        )

    def test_valid_key_ref(self):
        self.assertEqual(validate_ref_prefix("key.user_id", "key"), "key.user_id")

    def test_valid_grain_ref(self):
        self.assertEqual(validate_ref_prefix("grain.user", "grain"), "grain.user")

    def test_valid_subject_ref(self):
        self.assertEqual(validate_ref_prefix("subject.user", "subject"), "subject.user")

    def test_valid_enum_ref(self):
        self.assertEqual(
            validate_ref_prefix("enum.iso_country_code", "enum"), "enum.iso_country_code"
        )

    def test_valid_compiler_profile_ref(self):
        self.assertEqual(
            validate_ref_prefix("compiler_profile.conversion_rate_requirement", "compiler_profile"),
            "compiler_profile.conversion_rate_requirement",
        )

    def test_invalid_prefix(self):
        with self.assertRaises(ValueError) as ctx:
            validate_ref_prefix("wrong.user", "entity")
        self.assertIn("must start with", str(ctx.exception))

    def test_missing_dot(self):
        with self.assertRaises(ValueError) as ctx:
            validate_ref_prefix("entityuser", "entity")
        self.assertIn("must start with", str(ctx.exception))

    def test_empty_value(self):
        with self.assertRaises(ValueError) as ctx:
            validate_ref_prefix("", "entity")
        self.assertIn("must start with", str(ctx.exception))

    def test_unknown_prefix(self):
        with self.assertRaises(ValueError) as ctx:
            validate_ref_prefix("foo.bar", "unknown")
        self.assertIn("Unknown ref prefix", str(ctx.exception))


class TestValidateContractVersion(unittest.TestCase):
    """Tests for contract version validation."""

    def test_valid_entity_version(self):
        self.assertEqual(validate_contract_version("entity.v4", "entity"), "entity.v4")

    def test_valid_metric_version(self):
        self.assertEqual(validate_contract_version("metric.v1", "metric"), "metric.v1")

    def test_valid_process_version(self):
        self.assertEqual(validate_contract_version("process.v2", "process"), "process.v2")

    def test_valid_dimension_version(self):
        self.assertEqual(validate_contract_version("dimension.v1", "dimension"), "dimension.v1")

    def test_valid_time_version(self):
        self.assertEqual(validate_contract_version("time.v1", "time"), "time.v1")

    def test_valid_binding_version(self):
        self.assertEqual(validate_contract_version("binding.v2", "binding"), "binding.v2")

    def test_invalid_domain(self):
        with self.assertRaises(ValueError) as ctx:
            validate_contract_version("wrong.v1", "entity")
        self.assertIn("must start with", str(ctx.exception))


class TestSemanticRef(unittest.TestCase):
    """Tests for SemanticRef model."""

    def test_valid_ref(self):
        ref = SemanticRef(ref="entity.user", description="User entity")
        self.assertEqual(ref.ref, "entity.user")
        self.assertEqual(ref.description, "User entity")

    def test_valid_ref_without_description(self):
        ref = SemanticRef(ref="metric.dau")
        self.assertEqual(ref.ref, "metric.dau")
        self.assertIsNone(ref.description)

    def test_empty_ref_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            SemanticRef(ref="")
        self.assertIn("ref must not be empty", str(ctx.exception))

    def test_whitespace_ref_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            SemanticRef(ref="   ")
        self.assertIn("ref must not be empty", str(ctx.exception))


class TestObjectBaseModels(unittest.TestCase):
    """Tests for shared object base models."""

    def test_header_base(self):
        header = ObjectHeaderBase(display_name="Users", description="User object")
        self.assertEqual(header.display_name, "Users")
        self.assertEqual(header.description, "User object")

    def test_response_base(self):
        response = ObjectResponseBase(
            status="draft",
            lifecycle_status="draft",
            readiness_status="not_ready",
            blocking_requirements=[],
            capabilities={},
            revision=1,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        self.assertEqual(response.status, "draft")
        self.assertEqual(response.lifecycle_status, "draft")
        self.assertEqual(response.readiness_status, "not_ready")
        self.assertEqual(response.revision, 1)

    def test_blocking_requirement(self):
        blocker = BlockingRequirement(
            code="METRIC_BINDING_MISSING",
            message="No active metric binding grounds this metric",
            subject_ref="metric.dau",
            dependency_ref="binding.metric_dau",
        )
        self.assertEqual(blocker.code, "METRIC_BINDING_MISSING")
        self.assertEqual(blocker.subject_ref, "metric.dau")

    def test_list_response_base(self):
        response = ListResponseBase[str](items=["a", "b"], total=2)
        self.assertEqual(response.items, ["a", "b"])
        self.assertEqual(response.total, 2)

    def test_api_error_detail(self):
        detail = ApiErrorDetail(message="invalid payload", code="invalid_payload", field="header")
        self.assertEqual(detail.message, "invalid payload")
        self.assertEqual(detail.code, "invalid_payload")
        self.assertEqual(detail.field, "header")


class TestWindowOffset(unittest.TestCase):
    """Tests for WindowOffset model."""

    def test_valid_offset(self):
        offset = WindowOffset(value=7, unit="day")
        self.assertEqual(offset.value, 7)
        self.assertEqual(offset.unit, "day")

    def test_all_units(self):
        for unit in ["minute", "hour", "day", "week"]:
            offset = WindowOffset(value=1, unit=unit)
            self.assertEqual(offset.unit, unit)


class TestWindowSpec(unittest.TestCase):
    """Tests for WindowSpec model."""

    def test_valid_window_with_anchor(self):
        window = WindowSpec(
            anchor_ref="time.exposure_time",
            start_offset=WindowOffset(value=0, unit="day"),
            end_offset=WindowOffset(value=7, unit="day"),
        )
        self.assertEqual(window.anchor_ref, "time.exposure_time")

    def test_valid_window_without_anchor(self):
        window = WindowSpec(
            start_offset=WindowOffset(value=0, unit="day"),
            end_offset=WindowOffset(value=7, unit="day"),
        )
        self.assertIsNone(window.anchor_ref)

    def test_invalid_anchor_ref_prefix(self):
        with self.assertRaises(ValidationError) as ctx:
            WindowSpec(anchor_ref="wrong.time")
        self.assertIn("'anchor_ref' must start with 'time.'", str(ctx.exception))


class TestPopulationSpec(unittest.TestCase):
    """Tests for PopulationSpec model."""

    def test_valid_population(self):
        pop = PopulationSpec(
            base_population_ref="population.all_users",
            include_refs=["population.active"],
            exclude_refs=["population.bots"],
            membership_mode="once",
        )
        self.assertEqual(pop.base_population_ref, "population.all_users")
        self.assertEqual(pop.membership_mode, "once")


class TestStepSpec(unittest.TestCase):
    """Tests for StepSpec model."""

    def test_valid_step(self):
        step = StepSpec(
            step_key="view_product",
            event_ref="event.product_view",
            qualifier_refs=["predicate.mobile"],
        )
        self.assertEqual(step.step_key, "view_product")
        self.assertEqual(step.event_ref, "event.product_view")


class TestStateSpec(unittest.TestCase):
    """Tests for StateSpec model."""

    def test_valid_state(self):
        state = StateSpec(
            state_key="active",
            entry_ref="predicate.is_active",
            exit_ref="predicate.is_churned",
            priority=80,
        )
        self.assertEqual(state.state_key, "active")
        self.assertEqual(state.entry_ref, "predicate.is_active")
        self.assertEqual(state.priority, 80)


class TestHorizonSpec(unittest.TestCase):
    """Tests for HorizonSpec model."""

    def test_valid_horizon(self):
        horizon = HorizonSpec(value=90, unit="day")
        self.assertEqual(horizon.value, 90)
        self.assertEqual(horizon.unit, "day")

    def test_all_units(self):
        for unit in ["day", "week", "month"]:
            horizon = HorizonSpec(value=1, unit=unit)
            self.assertEqual(horizon.unit, unit)


class TestLiteralTypes(unittest.TestCase):
    """Tests that literal types contain expected values."""

    def test_uniqueness_scope(self):
        # Type check - if this compiles, the values are correct
        scope: UniquenessScope = "global"
        scope = "parent_scoped"

    def test_id_stability(self):
        stability: IdStability = "stable"
        stability = "reassignable"
        stability = "ephemeral"

    def test_metric_family(self):
        family: MetricFamily = "count_metric"
        family = "sum_metric"
        family = "rate_metric"
        family = "average_metric"
        family = "distribution_metric"
        family = "score_metric"
        family = "survival_metric"

    def test_sample_kind(self):
        kind: SampleKind = "numeric"
        kind = "rate"
        kind = "binary"
        kind = "survival"

    def test_value_semantics(self):
        semantics: ValueSemantics = "count"
        semantics = "sum"
        semantics = "ratio"
        semantics = "mean"
        semantics = "distribution_statistic"
        semantics = "score"
        semantics = "survival_probability"

    def test_additivity(self):
        add: Additivity = "additive"
        add = "semi_additive"
        add = "non_additive"

    def test_process_type(self):
        ptype: ProcessType = "experiment_context"
        ptype = "cohort_definition"
        ptype = "funnel_definition"
        ptype = "session_contract"
        ptype = "path_pattern"
        ptype = "lifecycle_state_machine"

    def test_contract_mode(self):
        mode: ContractMode = "context_provider"
        mode = "entity_stream"

    def test_context_kind(self):
        kind: ContextKind = "cohort_membership"
        kind = "experiment_split"

    def test_structure_kind(self):
        skind: StructureKind = "flat"
        skind = "hierarchical"
        skind = "ordinal"
        skind = "time_derived"

    def test_dimension_domain_kind(self):
        dkind: DimensionDomainKind = "open"
        dkind = "enumerated"

    def test_time_semantic_role(self):
        role: TimeSemanticRole = "business_anchor"
        role = "measurement"
        role = "operational_support"

    def test_binding_scope(self):
        scope: BindingScope = "entity"
        scope = "process_object"
        scope = "metric"

    def test_profile_kind(self):
        kind: ProfileKind = "requirement"
        kind = "capability"

    def test_profile_subject_kind(self):
        skind: ProfileSubjectKind = "metric"
        skind = "process"
        skind = "binding"

    def test_object_status(self):
        status: ObjectStatus = "draft"
        status = "published"
        status = "deprecated"

    def test_lifecycle_status(self):
        """Type includes reserved 'validated' for Phase B; Phase A never produces it."""
        status: LifecycleStatus = "draft"
        status = "validated"  # reserved, not produced by current derivation
        status = "active"
        status = "deprecated"

    def test_readiness_status(self):
        """Type includes `stale` for dependency-drift readiness surfaces."""
        status: ReadinessStatus = "not_ready"
        status = "ready"
        status = "stale"
