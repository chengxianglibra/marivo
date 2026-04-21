"""Tests for AdditivityConstraints model and related validation rules."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.analysis_core.additivity_capabilities import derive_additivity_capabilities
from app.api.models.base import AdditivityConstraints
from app.api.models.metric import TypedMetricCreateRequest


class AdditivityConstraintsModelTests(unittest.TestCase):
    """Test AdditivityConstraints Pydantic model validation."""

    def test_dimension_policy_all_valid(self) -> None:
        c = AdditivityConstraints(dimension_policy="all", time_axis_policy="additive")
        self.assertEqual(c.dimension_policy, "all")
        self.assertIsNone(c.additive_dimensions)

    def test_dimension_policy_none_valid(self) -> None:
        c = AdditivityConstraints(dimension_policy="none", time_axis_policy="non_additive")
        self.assertEqual(c.dimension_policy, "none")
        self.assertIsNone(c.additive_dimensions)

    def test_dimension_policy_subset_with_dimensions(self) -> None:
        c = AdditivityConstraints(
            dimension_policy="subset",
            time_axis_policy="non_additive",
            additive_dimensions=["dimension.country"],
        )
        self.assertEqual(c.additive_dimensions, ["dimension.country"])

    def test_dimension_policy_subset_rejects_empty_dimensions(self) -> None:
        with self.assertRaises(ValueError):
            AdditivityConstraints(
                dimension_policy="subset",
                time_axis_policy="non_additive",
                additive_dimensions=[],
            )

    def test_dimension_policy_subset_rejects_null_dimensions(self) -> None:
        with self.assertRaises(ValueError):
            AdditivityConstraints(
                dimension_policy="subset",
                time_axis_policy="non_additive",
            )

    def test_dimension_policy_all_rejects_additive_dimensions(self) -> None:
        with self.assertRaises(ValueError):
            AdditivityConstraints(
                dimension_policy="all",
                time_axis_policy="additive",
                additive_dimensions=["dimension.country"],
            )

    def test_dimension_policy_none_rejects_additive_dimensions(self) -> None:
        with self.assertRaises(ValueError):
            AdditivityConstraints(
                dimension_policy="none",
                time_axis_policy="non_additive",
                additive_dimensions=["dimension.country"],
            )

    def test_notes_optional(self) -> None:
        c = AdditivityConstraints(
            dimension_policy="none",
            time_axis_policy="non_additive",
            notes="test note",
        )
        self.assertEqual(c.notes, "test note")

    def test_notes_default_null(self) -> None:
        c = AdditivityConstraints(dimension_policy="all", time_axis_policy="additive")
        self.assertIsNone(c.notes)


class AdditivityCapabilitiesWithConstraintsTests(unittest.TestCase):
    """Test derive_additivity_capabilities with constraints-based input."""

    def _header(self, **overrides: object) -> dict:
        base: dict = {
            "additivity_constraints": {"dimension_policy": "all", "time_axis_policy": "additive"},
            "primary_time_ref": "time.activity_date",
            "sample_kind": "numeric",
        }
        base.update(overrides)
        return base

    # ── dimension_policy = "all" ────────────────────────────────────────────

    def test_all_additive_full_capabilities(self) -> None:
        caps = derive_additivity_capabilities(header=self._header())
        self.assertTrue(caps.supports_decompose)
        self.assertTrue(caps.supports_attribute)
        self.assertTrue(caps.time_rollup_allowed)
        self.assertEqual(caps.dimension_policy, "all")
        self.assertEqual(caps.time_axis_policy, "additive")
        self.assertIsNone(caps.additive_dimensions)
        self.assertIsNone(caps.blocker)

    def test_all_non_additive_time(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "all",
                    "time_axis_policy": "non_additive",
                },
            )
        )
        self.assertTrue(caps.supports_decompose)
        self.assertFalse(caps.time_rollup_allowed)
        self.assertEqual(caps.time_axis_policy, "non_additive")

    # ── dimension_policy = "subset" ─────────────────────────────────────────

    def test_subset_with_additive_dimensions(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "subset",
                    "time_axis_policy": "non_additive",
                    "additive_dimensions": ["dimension.country"],
                },
            )
        )
        self.assertTrue(caps.supports_decompose)
        self.assertTrue(caps.supports_attribute)
        self.assertFalse(caps.time_rollup_allowed)
        self.assertEqual(caps.dimension_policy, "subset")
        self.assertEqual(caps.additive_dimensions, ["dimension.country"])
        self.assertIsNone(caps.blocker)

    def test_subset_without_additive_dimensions_fail_closed(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "subset",
                    "time_axis_policy": "non_additive",
                },
            )
        )
        self.assertFalse(caps.supports_decompose)
        self.assertFalse(caps.supports_attribute)
        self.assertEqual(caps.blocker, "ADDITIVITY_SUBSET_NO_DIMENSIONS")

    # ── dimension_policy = "none" ───────────────────────────────────────────

    def test_none_policy(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "none",
                    "time_axis_policy": "non_additive",
                },
            )
        )
        self.assertFalse(caps.supports_decompose)
        self.assertFalse(caps.supports_attribute)
        self.assertFalse(caps.time_rollup_allowed)
        self.assertIsNone(caps.blocker)

    # ── missing / invalid constraints ──────────────────────────────────────

    def test_missing_additivity_constraints(self) -> None:
        caps = derive_additivity_capabilities(header=self._header(additivity_constraints=None))
        self.assertFalse(caps.supports_decompose)
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_MISSING")

    def test_invalid_dimension_policy(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "unknown",
                    "time_axis_policy": "additive",
                },
            )
        )
        self.assertFalse(caps.supports_decompose)
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_INVALID")

    def test_invalid_constraints_type(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(additivity_constraints="bad_string")
        )
        self.assertFalse(caps.supports_decompose)
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_INVALID")

    # ── capability composition ─────────────────────────────────────────────

    def test_attribute_requires_compare_and_decompose(self) -> None:
        # subset with dimensions → decompose true, but no primary_time_ref → compare false → attribute false
        caps = derive_additivity_capabilities(
            header={
                "additivity_constraints": {
                    "dimension_policy": "subset",
                    "time_axis_policy": "non_additive",
                    "additive_dimensions": ["dimension.country"],
                },
                "primary_time_ref": None,
                "sample_kind": "numeric",
            }
        )
        self.assertTrue(caps.supports_decompose)
        self.assertFalse(caps.supports_compare)
        self.assertFalse(caps.supports_attribute)

    # ── additivity_basis reflects constraints ──────────────────────────────

    def test_basis_includes_constraints(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "subset",
                    "time_axis_policy": "non_additive",
                    "additive_dimensions": ["dimension.region"],
                },
            )
        )
        self.assertEqual(caps.additivity_basis["dimension_policy"], "subset")
        self.assertEqual(caps.additivity_basis["additive_dimensions"], ["dimension.region"])

    # ── fine-grained blockers ─────────────────────────────────────────────

    def test_missing_dimension_policy_blocker(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={"time_axis_policy": "additive"},
            ),
        )
        self.assertFalse(caps.supports_decompose)
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_DIMENSION_POLICY_MISSING")

    def test_missing_time_axis_policy_blocker(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={"dimension_policy": "all"},
            ),
        )
        self.assertFalse(caps.supports_decompose)
        self.assertEqual(caps.blocker, "ADDITIVITY_CONSTRAINTS_TIME_AXIS_POLICY_MISSING")

    def test_capability_condition_subset_policy(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(
                additivity_constraints={
                    "dimension_policy": "subset",
                    "time_axis_policy": "non_additive",
                    "additive_dimensions": ["dimension.country"],
                },
            ),
        )
        self.assertEqual(caps.capability_condition, "dimension_must_be_allowed")

    def test_capability_condition_none_for_blocked(self) -> None:
        caps = derive_additivity_capabilities(
            header=self._header(additivity_constraints=None),
        )
        self.assertIsNone(caps.capability_condition)


class CountDistinctCrossValidatorTests(unittest.TestCase):
    """Test TypedMetricCreateRequest cross-validator for count_distinct."""

    def test_count_distinct_with_all_policy_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            TypedMetricCreateRequest.model_validate(
                {
                    "header": {
                        "metric_ref": "metric.dau",
                        "metric_family": "count_metric",
                        "observed_entity_ref": "entity.user",
                        "observation_grain_ref": "grain.user",
                        "sample_kind": "numeric",
                        "value_semantics": "count",
                        "additivity_constraints": {
                            "dimension_policy": "all",
                            "time_axis_policy": "additive",
                        },
                        "metric_contract_version": "metric.v1",
                    },
                    "payload": {
                        "metric_family": "count_metric",
                        "count_target": {
                            "name": "users",
                            "semantics": "distinct users",
                            "aggregation": "count_distinct",
                        },
                    },
                }
            )
        self.assertIn("count_distinct", str(ctx.exception))

    def test_count_distinct_with_none_policy_accepted(self) -> None:
        req = TypedMetricCreateRequest.model_validate(
            {
                "header": {
                    "metric_ref": "metric.dau",
                    "metric_family": "count_metric",
                    "observed_entity_ref": "entity.user",
                    "observation_grain_ref": "grain.user",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "additivity_constraints": {
                        "dimension_policy": "none",
                        "time_axis_policy": "non_additive",
                    },
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "users",
                        "semantics": "distinct users",
                        "aggregation": "count_distinct",
                    },
                },
            }
        )
        self.assertEqual(req.header.additivity_constraints.dimension_policy, "none")

    def test_count_distinct_with_subset_policy_accepted(self) -> None:
        req = TypedMetricCreateRequest.model_validate(
            {
                "header": {
                    "metric_ref": "metric.dau",
                    "metric_family": "count_metric",
                    "observed_entity_ref": "entity.user",
                    "observation_grain_ref": "grain.user",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "additivity_constraints": {
                        "dimension_policy": "subset",
                        "time_axis_policy": "non_additive",
                        "additive_dimensions": ["dimension.country"],
                    },
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "users",
                        "semantics": "distinct users",
                        "aggregation": "count_distinct",
                    },
                },
            }
        )
        self.assertEqual(req.header.additivity_constraints.dimension_policy, "subset")

    def test_plain_count_with_all_policy_accepted(self) -> None:
        req = TypedMetricCreateRequest.model_validate(
            {
                "header": {
                    "metric_ref": "metric.events",
                    "metric_family": "count_metric",
                    "observed_entity_ref": "entity.event",
                    "observation_grain_ref": "grain.event",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "additivity_constraints": {
                        "dimension_policy": "all",
                        "time_axis_policy": "additive",
                    },
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "events",
                        "semantics": "event count",
                        "aggregation": "count",
                    },
                },
            }
        )
        self.assertEqual(req.header.additivity_constraints.dimension_policy, "all")


class SubsetDimensionEnforcementTests(unittest.TestCase):
    """Test that decompose/attribute reject dimensions not in additive_dimensions when policy='subset'."""

    def _make_subset_caps(self, additive_dimensions: list[str]):
        from app.analysis_core.additivity_capabilities import derive_additivity_capabilities

        return derive_additivity_capabilities(
            header={
                "additivity_constraints": {
                    "dimension_policy": "subset",
                    "time_axis_policy": "non_additive",
                    "additive_dimensions": additive_dimensions,
                },
                "primary_time_ref": "time.activity_date",
                "sample_kind": "numeric",
            },
        )

    def test_subset_caps_allow_declared_dimension(self) -> None:
        caps = self._make_subset_caps(["dimension.country", "dimension.region"])
        self.assertTrue(caps.supports_decompose)
        self.assertIn("dimension.country", caps.additive_dimensions or [])

    def test_subset_caps_blocker_on_empty(self) -> None:
        caps = self._make_subset_caps([])
        self.assertFalse(caps.supports_decompose)
        self.assertEqual(caps.blocker, "ADDITIVITY_SUBSET_NO_DIMENSIONS")

    def test_attribute_rejects_disallowed_dimension_on_subset_policy(self) -> None:
        from unittest.mock import MagicMock

        from app.intents.attribute import run_attribute_intent

        svc = MagicMock()
        svc.normalize_intent_metric_ref.return_value = "metric.revenue"
        svc.metric_name_from_ref.return_value = "revenue"
        mock_metric = MagicMock()
        mock_metric.additivity_constraints = {
            "dimension_policy": "subset",
            "time_axis_policy": "additive",
            "additive_dimensions": ["dimension.country"],
        }
        mock_metric.primary_time_ref = "time.date"
        mock_metric.sample_kind = "numeric"
        svc.semantic_repository.resolve_metric.return_value = mock_metric

        with self.assertRaises(ValueError) as ctx:
            run_attribute_intent(
                svc,
                "session_1",
                {
                    "metric": "metric.revenue",
                    "left": {
                        "time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-02-01"}
                    },
                    "right": {
                        "time_scope": {"kind": "range", "start": "2025-01-01", "end": "2025-02-01"}
                    },
                    "dimensions": ["dimension.country", "dimension.product"],
                },
            )
        self.assertIn("ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED", str(ctx.exception))
        self.assertIn("dimension.product", str(ctx.exception))

    def test_attribute_allows_only_declared_dimensions_on_subset_policy(self) -> None:
        from unittest.mock import MagicMock

        from app.intents.attribute import run_attribute_intent

        svc = MagicMock()
        svc.normalize_intent_metric_ref.return_value = "metric.revenue"
        svc.metric_name_from_ref.return_value = "revenue"
        mock_metric = MagicMock()
        mock_metric.additivity_constraints = {
            "dimension_policy": "subset",
            "time_axis_policy": "additive",
            "additive_dimensions": ["dimension.country", "dimension.region"],
        }
        mock_metric.primary_time_ref = "time.date"
        mock_metric.sample_kind = "numeric"
        svc.semantic_repository.resolve_metric.return_value = mock_metric

        # Should not raise at the dimension gate — will fail later at observe,
        # but the dimension validation itself should pass
        try:
            run_attribute_intent(
                svc,
                "session_1",
                {
                    "metric": "metric.revenue",
                    "left": {
                        "time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-02-01"}
                    },
                    "right": {
                        "time_scope": {"kind": "range", "start": "2025-01-01", "end": "2025-02-01"}
                    },
                    "dimensions": ["dimension.country"],
                },
            )
        except ValueError as e:
            # Should NOT be ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED
            self.assertNotIn("ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED", str(e))

    def test_decompose_rejects_disallowed_dimension_on_subset_policy(self) -> None:
        from unittest.mock import MagicMock

        from app.intents.decompose import run_decompose_intent

        svc = MagicMock()
        compare_artifact = {
            "comparison_type": "scalar_delta",
            "metric": "m1",
            "unit": None,
            "left_value": 100.0,
            "right_value": 90.0,
            "absolute_delta": 10.0,
            "relative_delta": 0.111,
            "direction": "increase",
            "lineage": {
                "left_source_ref": {"step_id": "step_obs_left", "session_id": "session_1"},
                "right_source_ref": {"step_id": "step_obs_right", "session_id": "session_1"},
            },
            "resolved_input_summary": {
                "left_time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "right_time_scope": {"kind": "range", "start": "2023-12-25", "end": "2024-01-01"},
            },
            "analytical_metadata": {
                "additivity_constraints": {
                    "dimension_policy": "subset",
                    "time_axis_policy": "non_additive",
                    "additive_dimensions": ["dimension.country"],
                },
            },
        }
        svc._resolve_artifact_for_ref.return_value = compare_artifact
        svc._resolve_artifact_id_for_step.return_value = "art_fake"

        mock_metric = MagicMock()
        mock_metric.additivity_constraints = {
            "dimension_policy": "subset",
            "time_axis_policy": "non_additive",
            "additive_dimensions": ["dimension.country"],
        }
        mock_metric.primary_time_ref = "time.date"
        mock_metric.sample_kind = "numeric"
        mock_metric.allowed_dimensions = ["dimension.country", "dimension.product"]
        mock_metric.dimensions = ["dimension.country", "dimension.product"]
        mock_metric.grain = "day"
        svc.semantic_repository.resolve_metric.return_value = mock_metric
        svc.resolve_metric_dimensions.return_value = ["dimension.country", "dimension.product"]

        with self.assertRaises(ValueError) as ctx:
            run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.product",
                },
            )
        self.assertIn("ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED", str(ctx.exception))
        self.assertIn("dimension.product", str(ctx.exception))

    def test_decompose_allows_declared_dimension_on_subset_policy(self) -> None:
        from unittest.mock import MagicMock, patch

        from app.intents.decompose import run_decompose_intent

        svc = MagicMock()
        compare_artifact = {
            "comparison_type": "scalar_delta",
            "metric": "m1",
            "unit": None,
            "left_value": 100.0,
            "right_value": 90.0,
            "absolute_delta": 10.0,
            "relative_delta": 0.111,
            "direction": "increase",
            "lineage": {
                "left_source_ref": {"step_id": "step_obs_left", "session_id": "session_1"},
                "right_source_ref": {"step_id": "step_obs_right", "session_id": "session_1"},
            },
            "resolved_input_summary": {
                "left_time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "right_time_scope": {"kind": "range", "start": "2023-12-25", "end": "2024-01-01"},
            },
            "analytical_metadata": {
                "additivity_constraints": {
                    "dimension_policy": "subset",
                    "time_axis_policy": "non_additive",
                    "additive_dimensions": ["dimension.country"],
                },
            },
        }
        svc._resolve_artifact_for_ref.return_value = compare_artifact
        svc._resolve_artifact_id_for_step.return_value = "art_fake"

        mock_metric = MagicMock()
        mock_metric.additivity_constraints = {
            "dimension_policy": "subset",
            "time_axis_policy": "non_additive",
            "additive_dimensions": ["dimension.country"],
        }
        mock_metric.primary_time_ref = "time.date"
        mock_metric.sample_kind = "numeric"
        mock_metric.allowed_dimensions = ["dimension.country"]
        mock_metric.dimensions = ["dimension.country"]
        mock_metric.grain = "day"
        svc.semantic_repository.resolve_metric.return_value = mock_metric
        svc.resolve_metric_dimensions.return_value = ["dimension.country"]
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc._resolve_metric_table.return_value = "src.metrics"
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"metrics": "src.metrics"})
        svc._compile_step_with_feedback.return_value = MagicMock()
        svc._build_scoped_query.return_value = None

        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("app.intents.decompose.execute_compiled", return_value=mock_result):
            # dimension.country is in additive_dimensions, should pass the gate
            result = run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        self.assertIn("rows", result)


class AttributeAdditivityGateTests(unittest.TestCase):
    """P5: Test attribute intent additivity gate — structured 409 errors and bundle metadata."""

    def _make_svc(self, *, constraints: dict | None, primary_time_ref: str | None = "time.date"):
        from unittest.mock import MagicMock

        svc = MagicMock()
        svc.normalize_intent_metric_ref.return_value = "metric.test_metric"
        svc.metric_name_from_ref.return_value = "test_metric"
        mock_metric = MagicMock()
        mock_metric.additivity_constraints = constraints
        mock_metric.primary_time_ref = primary_time_ref
        mock_metric.sample_kind = "numeric"
        svc.semantic_repository.resolve_metric.return_value = mock_metric
        return svc

    def _attribute_params(self, dimensions: list[str]) -> dict:
        return {
            "metric": "metric.test_metric",
            "left": {"time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-02-01"}},
            "right": {"time_scope": {"kind": "range", "start": "2025-01-01", "end": "2025-02-01"}},
            "dimensions": dimensions,
        }

    # ── dimension_policy = "none" → 409 via Gate 2 (supports_decompose=False) ──

    def test_none_policy_rejected_with_structured_409(self) -> None:
        from app.execution.errors import ExecutionError
        from app.intents.attribute import run_attribute_intent

        svc = self._make_svc(
            constraints={"dimension_policy": "none", "time_axis_policy": "non_additive"},
        )
        with self.assertRaises(ExecutionError) as ctx:
            run_attribute_intent(svc, "session_1", self._attribute_params(["dimension.country"]))
        err = ctx.exception
        # Gate 2 fires first: supports_decompose=False when dimension_policy="none"
        self.assertEqual(err.code, "ADDITIVITY_CONSTRAINT")
        self.assertEqual(err.category, "compatibility")
        payload = err.detail["compatibility_error"]
        self.assertEqual(payload["gate"], "supports_decompose")
        self.assertEqual(payload["dimension_policy"], "none")
        self.assertFalse(payload["time_rollup_allowed"])

    # ── missing additivity_constraints → 409 via Gate 1 (supports_compare=False) ──

    def test_missing_constraints_rejected_with_structured_409(self) -> None:
        from app.execution.errors import ExecutionError
        from app.intents.attribute import run_attribute_intent

        svc = self._make_svc(constraints=None)
        with self.assertRaises(ExecutionError) as ctx:
            run_attribute_intent(svc, "session_1", self._attribute_params(["dimension.country"]))
        err = ctx.exception
        self.assertEqual(err.code, "ADDITIVITY_CONSTRAINT")
        self.assertEqual(err.category, "compatibility")
        payload = err.detail["compatibility_error"]
        # constraints=None → supports_compare=False → Gate 1 fires
        self.assertEqual(payload["gate"], "supports_compare")
        self.assertEqual(payload["blocker"], "ADDITIVITY_CONSTRAINTS_MISSING")

    # ── supports_compare=false → specific gate error ──────────────────────

    def test_compare_not_supported_specific_error(self) -> None:
        from app.execution.errors import ExecutionError
        from app.intents.attribute import run_attribute_intent

        # No primary_time_ref → supports_compare=False
        svc = self._make_svc(
            constraints={"dimension_policy": "all", "time_axis_policy": "additive"},
            primary_time_ref=None,
        )
        with self.assertRaises(ExecutionError) as ctx:
            run_attribute_intent(svc, "session_1", self._attribute_params(["dimension.country"]))
        err = ctx.exception
        self.assertEqual(err.code, "ADDITIVITY_CONSTRAINT")
        payload = err.detail["compatibility_error"]
        self.assertEqual(payload["gate"], "supports_compare")

    # ── supports_decompose=false → specific gate error ────────────────────

    def test_decompose_not_supported_specific_error(self) -> None:
        from app.execution.errors import ExecutionError
        from app.intents.attribute import run_attribute_intent

        # dimension_policy=none → supports_decompose=False, but supports_compare=True
        svc = self._make_svc(
            constraints={"dimension_policy": "none", "time_axis_policy": "non_additive"},
        )
        with self.assertRaises(ExecutionError) as ctx:
            run_attribute_intent(svc, "session_1", self._attribute_params(["dimension.country"]))
        err = ctx.exception
        # Gate 2 fires first (supports_decompose=False) → ADDITIVITY_CONSTRAINT
        self.assertEqual(err.code, "ADDITIVITY_CONSTRAINT")
        self.assertEqual(err.category, "compatibility")
        payload = err.detail["compatibility_error"]
        self.assertEqual(payload.get("gate"), "supports_decompose")
        self.assertEqual(payload["dimension_policy"], "none")

    # ── mixed dimensions → 409 with allowed + disallowed lists ────────────

    def test_mixed_dimensions_returns_structured_409_with_both_lists(self) -> None:
        from app.execution.errors import ExecutionError
        from app.intents.attribute import run_attribute_intent

        svc = self._make_svc(
            constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country", "dimension.region"],
            },
        )
        with self.assertRaises(ExecutionError) as ctx:
            run_attribute_intent(
                svc,
                "session_1",
                self._attribute_params(["dimension.country", "dimension.product", "dimension.region"]),
            )
        err = ctx.exception
        self.assertEqual(err.code, "ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED")
        self.assertEqual(err.category, "compatibility")
        payload = err.detail["compatibility_error"]
        self.assertEqual(payload["dimension_policy"], "subset")
        self.assertEqual(sorted(payload["allowed_dimensions"]), ["dimension.country", "dimension.region"])
        self.assertEqual(payload["disallowed_dimensions"], ["dimension.product"])
        self.assertFalse(payload["time_rollup_allowed"])
        self.assertIn("Retry", payload["remediation_hint"])

    # ── all-allowed dimensions on subset → passes gate ────────────────────

    def test_all_allowed_dimensions_on_subset_passes_gate(self) -> None:
        from app.intents.attribute import run_attribute_intent

        svc = self._make_svc(
            constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country", "dimension.region"],
            },
        )
        try:
            run_attribute_intent(
                svc,
                "session_1",
                self._attribute_params(["dimension.country"]),
            )
        except Exception as e:
            error_str = str(e)
            self.assertNotIn("ADDITIVITY_CONSTRAINT", error_str)

    # ── bundle includes analytical_metadata with additivity basis ─────────

    def test_bundle_includes_additivity_basis_metadata(self) -> None:
        from unittest.mock import patch

        from app.intents.attribute import run_attribute_intent

        svc = self._make_svc(
            constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country"],
            },
        )

        # Mock the full orchestration pipeline to return a valid bundle
        left_obs = {
            "observation_type": "scalar",
            "step_ref": {"step_id": "obs_left", "session_id": "session_1"},
            "artifact_id": "art_left",
            "time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-02-01"},
        }
        right_obs = {
            "observation_type": "scalar",
            "step_ref": {"step_id": "obs_right", "session_id": "session_1"},
            "artifact_id": "art_right",
            "time_scope": {"kind": "range", "start": "2025-01-01", "end": "2025-02-01"},
        }
        compare_result = {
            "step_ref": {"step_id": "step_compare", "session_id": "session_1"},
            "artifact_id": "art_compare",
            "left_value": 100.0,
            "right_value": 90.0,
            "absolute_delta": 10.0,
            "relative_delta": 0.111,
            "direction": "increase",
            "comparability": {"status": "comparable", "issues": []},
        }
        decompose_result = {
            "step_ref": {"step_id": "step_decompose", "session_id": "session_1"},
            "artifact_id": "art_decompose",
            "attribution": {"status": "attributable", "issues": []},
            "rows": [
                {
                    "key": "US",
                    "left_value": 60.0,
                    "right_value": 50.0,
                    "absolute_contribution": 10.0,
                    "contribution_share": 1.0,
                    "direction": "increase",
                    "presence": "both",
                }
            ],
            "scope_absolute_delta": 10.0,
            "unexplained_absolute_delta": 0.0,
            "unexplained_share": 0.0,
            "unexplained_reason": None,
        }

        with (
            patch("app.intents.attribute.run_observe_intent", side_effect=[left_obs, right_obs]),
            patch("app.intents.attribute.run_compare_intent", return_value=compare_result),
            patch("app.intents.attribute.run_decompose_intent", return_value=decompose_result),
        ):
            result = run_attribute_intent(
                svc, "session_1", self._attribute_params(["dimension.country"])
            )

        pm = result.get("projection_metadata", {})
        ab = pm.get("additivity_basis", {})
        self.assertEqual(ab["dimension_policy"], "subset")
        self.assertEqual(ab["additive_dimensions"], ["dimension.country"])
        self.assertEqual(ab["time_axis_policy"], "non_additive")
        self.assertFalse(ab["time_rollup_allowed"])
        self.assertEqual(ab["capability_condition"], "dimension_must_be_allowed")
        tbc = pm.get("time_boundary_constraint", {})
        self.assertEqual(tbc["scope"], "frozen_compare_window")
        self.assertFalse(tbc["time_rollup_implied"])

    def test_bundle_additivity_basis_all_policy(self) -> None:
        from unittest.mock import patch

        from app.intents.attribute import run_attribute_intent

        svc = self._make_svc(
            constraints={"dimension_policy": "all", "time_axis_policy": "additive"},
        )

        left_obs = {
            "observation_type": "scalar",
            "step_ref": {"step_id": "obs_left", "session_id": "session_1"},
            "artifact_id": "art_left",
            "time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-02-01"},
        }
        right_obs = {
            "observation_type": "scalar",
            "step_ref": {"step_id": "obs_right", "session_id": "session_1"},
            "artifact_id": "art_right",
            "time_scope": {"kind": "range", "start": "2025-01-01", "end": "2025-02-01"},
        }
        compare_result = {
            "step_ref": {"step_id": "step_compare", "session_id": "session_1"},
            "artifact_id": "art_compare",
            "left_value": 100.0,
            "right_value": 90.0,
            "absolute_delta": 10.0,
            "relative_delta": 0.111,
            "direction": "increase",
            "comparability": {"status": "comparable", "issues": []},
        }
        decompose_result = {
            "step_ref": {"step_id": "step_decompose", "session_id": "session_1"},
            "artifact_id": "art_decompose",
            "attribution": {"status": "attributable", "issues": []},
            "rows": [],
            "scope_absolute_delta": 10.0,
            "unexplained_absolute_delta": 0.0,
            "unexplained_share": 0.0,
            "unexplained_reason": None,
        }

        with (
            patch("app.intents.attribute.run_observe_intent", side_effect=[left_obs, right_obs]),
            patch("app.intents.attribute.run_compare_intent", return_value=compare_result),
            patch("app.intents.attribute.run_decompose_intent", return_value=decompose_result),
        ):
            result = run_attribute_intent(
                svc, "session_1", self._attribute_params(["dimension.country"])
            )

        pm = result.get("projection_metadata", {})
        ab = pm.get("additivity_basis", {})
        self.assertEqual(ab["dimension_policy"], "all")
        self.assertTrue(ab["time_rollup_allowed"])
        self.assertEqual(ab["capability_condition"], "all_dimensions_allowed")

    # ── structural errors still use ValueError (422) ──────────────────────

    def test_structural_errors_still_raise_valueerror(self) -> None:
        from app.intents.attribute import run_attribute_intent

        svc = self._make_svc(
            constraints={"dimension_policy": "all", "time_axis_policy": "additive"},
        )
        # Missing metric
        with self.assertRaises(ValueError) as ctx:
            run_attribute_intent(svc, "session_1", {"metric": "", "dimensions": ["d"]})
        self.assertNotIn("ADDITIVITY_CONSTRAINT", str(ctx.exception))

    # ── disallowed dimensions list enables auto-retry ─────────────────────

    def test_mixed_dimensions_payload_enables_auto_retry(self) -> None:
        from app.execution.errors import ExecutionError
        from app.intents.attribute import run_attribute_intent

        svc = self._make_svc(
            constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country"],
            },
        )
        with self.assertRaises(ExecutionError) as ctx:
            run_attribute_intent(
                svc,
                "session_1",
                self._attribute_params(["dimension.country", "dimension.platform", "dimension.os"]),
            )
        payload = ctx.exception.detail["compatibility_error"]
        # Caller can construct retry with only allowed_dimensions
        retry_dimensions = payload["allowed_dimensions"]
        self.assertEqual(retry_dimensions, ["dimension.country"])
        # Disallowed must be complete
        self.assertEqual(
            sorted(payload["disallowed_dimensions"]),
            ["dimension.os", "dimension.platform"],
        )


class MetricUpdateCountDistinctValidationTests(unittest.TestCase):
    """Test that update_typed_metric rejects count_distinct + dimension_policy='all'."""

    def setUp(self) -> None:
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test_update_validate.meta.sqlite"
        self.store = SQLiteMetadataStore(db_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_update_additivity_to_all_with_count_distinct_payload_rejected(self) -> None:
        from app.api.models.metric import TypedMetricCreateRequest, TypedMetricUpdateRequest
        from app.semantic_service.typed_objects import TypedObjectService

        svc = TypedObjectService(self.store)

        # Create a count_distinct metric with subset policy (valid)
        created = svc.create_typed_metric(
            TypedMetricCreateRequest.model_validate(
                {
                    "header": {
                        "metric_ref": "metric.dau",
                        "metric_family": "count_metric",
                        "observed_entity_ref": "entity.user",
                        "observation_grain_ref": "grain.user",
                        "sample_kind": "numeric",
                        "value_semantics": "count",
                        "additivity_constraints": {
                            "dimension_policy": "subset",
                            "time_axis_policy": "non_additive",
                            "additive_dimensions": ["dimension.country"],
                        },
                        "metric_contract_version": "metric.v1",
                    },
                    "payload": {
                        "metric_family": "count_metric",
                        "count_target": {
                            "name": "users",
                            "semantics": "distinct users",
                            "aggregation": "count_distinct",
                        },
                    },
                }
            )
        )
        metric_id = created["metric_contract_id"]

        # Try to update additivity_constraints to 'all' — should be rejected
        with self.assertRaises(Exception) as ctx:
            svc.update_typed_metric(
                metric_id,
                TypedMetricUpdateRequest(
                    additivity_constraints=AdditivityConstraints(
                        dimension_policy="all", time_axis_policy="additive"
                    )
                ),
            )
        self.assertIn("count_distinct", str(ctx.exception))

    def test_update_payload_to_count_distinct_with_all_policy_rejected(self) -> None:
        from app.api.models.metric import TypedMetricCreateRequest, TypedMetricUpdateRequest
        from app.semantic_service.typed_objects import TypedObjectService

        svc = TypedObjectService(self.store)

        # Create a sum metric with all policy (valid for sum)
        created = svc.create_typed_metric(
            TypedMetricCreateRequest.model_validate(
                {
                    "header": {
                        "metric_ref": "metric.revenue",
                        "metric_family": "sum_metric",
                        "observed_entity_ref": "entity.order",
                        "observation_grain_ref": "grain.order",
                        "sample_kind": "numeric",
                        "value_semantics": "sum",
                        "additivity_constraints": {
                            "dimension_policy": "all",
                            "time_axis_policy": "additive",
                        },
                        "metric_contract_version": "metric.v1",
                    },
                    "payload": {
                        "metric_family": "sum_metric",
                        "measure": {
                            "name": "revenue",
                            "semantics": "total revenue",
                            "aggregation": "sum",
                        },
                    },
                }
            )
        )
        metric_id = created["metric_contract_id"]

        # Try to update payload to count_distinct while policy is 'all' — should be rejected
        with self.assertRaises(Exception) as ctx:
            svc.update_typed_metric(
                metric_id,
                TypedMetricUpdateRequest(
                    payload={
                        "metric_family": "count_metric",
                        "count_target": {
                            "name": "orders",
                            "semantics": "distinct orders",
                            "aggregation": "count_distinct",
                        },
                    }
                ),
            )
        # metric_family is immutable, so this should fail on that check instead
        self.assertIn("metric_family", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
