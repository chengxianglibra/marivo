"""Tests for the test intent (source-type, Welch's t-test) and validate intent.

Covers:
  - Statistical helpers: pure Python math verification
  - Test intent: source-type validation (metric, time_scope, kind, hypothesis)
  - Validate intent: input validation
  - Value expression integration: extract_value_expression unit tests

For the full end-to-end flow with DuckDB, see the HTTP-based tests below.
"""

from __future__ import annotations

import unittest
from typing import Any

from marivo.core.semantic.value_expr import extract_value_expression
from marivo.runtime.intents.test import (
    _betai,
    _p_value_from_t,
    _t_sf,
)


class TestStatisticalHelpers(unittest.TestCase):
    """Pure-math unit tests for Welch's t-test statistical helpers."""

    def test_t_sf_symmetry(self) -> None:
        """Survival function should be symmetric: sf(t) + sf(-t) = 1."""
        for t in [-3.0, -1.0, 0.0, 1.0, 3.0]:
            for df in [5, 10, 30, 100]:
                self.assertAlmostEqual(
                    _t_sf(t, df) + _t_sf(-t, df),
                    1.0,
                    places=6,
                    msg=f"sf({t}, {df}) + sf({-t}, {df}) != 1",
                )

    def test_p_value_two_sided_zero_t(self) -> None:
        """When t=0, two-sided p-value should be 1.0."""
        self.assertAlmostEqual(_p_value_from_t(0.0, 10, "two_sided"), 1.0, places=6)

    def test_p_value_decreases_with_larger_t(self) -> None:
        """Larger |t| should give smaller p-value."""
        p_small = _p_value_from_t(1.0, 30, "two_sided")
        p_large = _p_value_from_t(5.0, 30, "two_sided")
        self.assertGreater(p_small, p_large)

    def test_betai_boundary_values(self) -> None:
        """betai at boundaries should be 0 and 1."""
        self.assertAlmostEqual(_betai(1, 1, 0.0), 0.0, places=6)
        self.assertAlmostEqual(_betai(1, 1, 1.0), 1.0, places=6)


class TestExtractValueExpressionIntegration(unittest.TestCase):
    """Integration tests for extract_value_expression used by test intent."""

    def test_sum_with_nested_case(self) -> None:
        expr = extract_value_expression(
            "SUM(CASE WHEN status='active' THEN amount ELSE 0 END)", "sum"
        )
        self.assertEqual(expr, "CASE WHEN status='active' THEN amount ELSE 0 END")

    def test_sum_arithmetic_inside(self) -> None:
        expr = extract_value_expression("SUM(price * quantity)", "sum")
        self.assertEqual(expr, "price * quantity")

    def test_multiple_sums_rejected(self) -> None:
        expr = extract_value_expression("SUM(a) + SUM(b)", "sum")
        self.assertIsNone(expr)

    def test_bare_expression_rejected(self) -> None:
        expr = extract_value_expression("revenue", "sum")
        self.assertIsNone(expr)


class TestTestIntentSourceValidation(unittest.TestCase):
    """Validation tests for the source-type test intent runner."""

    def test_missing_metric_rejected(self) -> None:
        from unittest.mock import MagicMock

        from marivo.runtime.intents.test import run_test_intent

        runtime = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            run_test_intent(
                runtime,
                "session-1",
                {
                    "left": {
                        "time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-01-08"}
                    },
                    "right": {
                        "time_scope": {"kind": "range", "start": "2026-01-08", "end": "2026-01-15"}
                    },
                },
            )
        self.assertIn("metric", str(ctx.exception))

    def test_missing_left_time_scope_rejected(self) -> None:
        from unittest.mock import MagicMock

        from marivo.runtime.intents.test import run_test_intent

        runtime = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            run_test_intent(
                runtime,
                "session-1",
                {
                    "metric": "metric.test",
                    "left": {},
                    "right": {
                        "time_scope": {"kind": "range", "start": "2026-01-08", "end": "2026-01-15"}
                    },
                },
            )
        self.assertIn("left.time_scope", str(ctx.exception))

    def test_missing_right_time_scope_rejected(self) -> None:
        from unittest.mock import MagicMock

        from marivo.runtime.intents.test import run_test_intent

        runtime = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            run_test_intent(
                runtime,
                "session-1",
                {
                    "metric": "metric.test",
                    "left": {
                        "time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-01-08"}
                    },
                    "right": {},
                },
            )
        self.assertIn("right.time_scope", str(ctx.exception))

    def test_invalid_kind_rejected(self) -> None:
        from unittest.mock import MagicMock

        from marivo.runtime.intents.test import run_test_intent

        runtime = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            run_test_intent(
                runtime,
                "session-1",
                {
                    "metric": "metric.test",
                    "left": {
                        "time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-01-08"}
                    },
                    "right": {
                        "time_scope": {"kind": "range", "start": "2026-01-08", "end": "2026-01-15"}
                    },
                    "kind": "rate",
                },
            )
        self.assertIn("rate", str(ctx.exception))

    def test_invalid_significance_rejected(self) -> None:
        from unittest.mock import MagicMock

        from marivo.runtime.intents.test import run_test_intent

        runtime = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            run_test_intent(
                runtime,
                "session-1",
                {
                    "metric": "metric.test",
                    "left": {
                        "time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-01-08"}
                    },
                    "right": {
                        "time_scope": {"kind": "range", "start": "2026-01-08", "end": "2026-01-15"}
                    },
                    "kind": "numeric",
                    "hypothesis": {"alternative": "two_sided", "significance": "loose"},
                },
            )
        self.assertIn("significance", str(ctx.exception))

    def test_method_parameter_rejected(self) -> None:
        from unittest.mock import MagicMock

        from marivo.runtime.intents.test import run_test_intent

        runtime = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            run_test_intent(
                runtime,
                "session-1",
                {
                    "metric": "metric.test",
                    "left": {
                        "time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-01-08"}
                    },
                    "right": {
                        "time_scope": {"kind": "range", "start": "2026-01-08", "end": "2026-01-15"}
                    },
                    "kind": "numeric",
                    "hypothesis": {"alternative": "two_sided", "significance": "balanced"},
                    "method": "two_proportion_z",
                },
            )
        self.assertIn("method", str(ctx.exception))

    def test_unsupported_hypothesis_families_rejected(self) -> None:
        from unittest.mock import MagicMock

        from marivo.runtime.intents.test import run_test_intent

        for family in ("two_sample_proportion", "paired_mean"):
            runtime = MagicMock()
            with self.subTest(family=family):
                with self.assertRaises(ValueError) as ctx:
                    run_test_intent(
                        runtime,
                        "session-1",
                        {
                            "metric": "metric.test",
                            "left": {
                                "time_scope": {
                                    "kind": "range",
                                    "start": "2026-01-01",
                                    "end": "2026-01-08",
                                }
                            },
                            "right": {
                                "time_scope": {
                                    "kind": "range",
                                    "start": "2026-01-08",
                                    "end": "2026-01-15",
                                }
                            },
                            "kind": "numeric",
                            "hypothesis": {
                                "family": family,
                                "alternative": "two_sided",
                                "significance": "balanced",
                            },
                        },
                    )
                self.assertIn("hypothesis.family", str(ctx.exception))

    def test_hypothesis_label_rejected(self) -> None:
        from unittest.mock import MagicMock

        from marivo.runtime.intents.test import run_test_intent

        runtime = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            run_test_intent(
                runtime,
                "session-1",
                {
                    "metric": "metric.test",
                    "left": {
                        "time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-01-08"}
                    },
                    "right": {
                        "time_scope": {"kind": "range", "start": "2026-01-08", "end": "2026-01-15"}
                    },
                    "kind": "numeric",
                    "hypothesis": {
                        "family": "two_sample_mean",
                        "alternative": "two_sided",
                        "significance": "balanced",
                        "label": "legacy label",
                    },
                },
            )
        self.assertIn("label", str(ctx.exception))

    def test_hypothesis_alpha_rejected(self) -> None:
        from unittest.mock import MagicMock

        from marivo.runtime.intents.test import run_test_intent

        runtime = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            run_test_intent(
                runtime,
                "session-1",
                {
                    "metric": "metric.test",
                    "left": {
                        "time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-01-08"}
                    },
                    "right": {
                        "time_scope": {"kind": "range", "start": "2026-01-08", "end": "2026-01-15"}
                    },
                    "kind": "numeric",
                    "hypothesis": {
                        "family": "two_sample_mean",
                        "alternative": "two_sided",
                        "alpha": 0.05,
                    },
                },
            )
        self.assertIn("alpha", str(ctx.exception))


class TestTestArtifactStructure(unittest.TestCase):
    """Verify the test intent artifact conforms to AOI hypothesis_test_result."""

    def _run_test_with_mock_data(self) -> dict[str, Any]:
        """Run test intent with mocked compute_numeric_sample_summary."""
        from unittest.mock import MagicMock, patch

        from marivo.runtime.intents._helpers import SampleSummary
        from marivo.runtime.intents.test import run_test_intent

        runtime = MagicMock()
        runtime.core.normalize_intent_metric_ref = MagicMock(return_value="metric.test_metric")
        runtime.core.metric_name_from_ref = MagicMock(return_value="test_metric")

        left_ss = SampleSummary(
            n=30, mean=100.0, standard_deviation=15.0, predicate_filter_lineage=None
        )
        right_ss = SampleSummary(
            n=25, mean=90.0, standard_deviation=12.0, predicate_filter_lineage=None
        )

        with patch("marivo.runtime.intents.test.compute_numeric_sample_summary") as mock_compute:
            mock_compute.side_effect = [left_ss, right_ss]
            with patch(
                "marivo.runtime.intents.test.resolve_predicate_lineage_reuse_for_intent"
            ) as mock_lineage:
                mock_lineage.return_value = {
                    "issues": [],
                    "fatal_message": None,
                    "reuse_summary": None,
                }
                with patch("marivo.runtime.intents.test.commit_step_result") as mock_commit:
                    mock_commit.return_value = {
                        "intent_type": "test",
                        "step_type": "test",
                        "step_ref": {"session_id": "s1", "step_id": "step-1", "step_type": "test"},
                        "artifact_id": "art-1",
                    }
                    result = run_test_intent(
                        runtime,
                        "session-1",
                        {
                            "metric": "metric.test_metric",
                            "left": {"time_scope": {"start": "2026-01-01", "end": "2026-01-08"}},
                            "right": {"time_scope": {"start": "2026-01-08", "end": "2026-01-15"}},
                            "kind": "numeric",
                            "hypothesis": {
                                "family": "two_sample_mean",
                                "alternative": "two_sided",
                                "significance": "balanced",
                            },
                        },
                    )
                    # Extract the artifact payload from commit_step_result
                    artifact = mock_commit.call_args[0][6]  # 7th arg is artifact_payload
                    return artifact

    def test_statistic_is_flat_number(self) -> None:
        artifact = self._run_test_with_mock_data()
        self.assertIsInstance(artifact["statistic"], float)
        self.assertNotIsInstance(artifact["statistic"], dict)

    def test_hypothesis_family_is_two_sample_mean(self) -> None:
        artifact = self._run_test_with_mock_data()
        self.assertEqual(artifact["hypothesis"]["family"], "two_sample_mean")

    def test_hypothesis_has_no_label(self) -> None:
        artifact = self._run_test_with_mock_data()
        self.assertNotIn("label", artifact["hypothesis"])

    def test_hypothesis_records_significance_and_resolved_alpha(self) -> None:
        artifact = self._run_test_with_mock_data()
        self.assertEqual(artifact["hypothesis"]["significance"], "balanced")
        self.assertEqual(artifact["hypothesis"]["alpha"], 0.05)

    def test_assumption_notes_is_list_of_strings(self) -> None:
        artifact = self._run_test_with_mock_data()
        self.assertIsInstance(artifact["assumption_notes"], list)
        for note in artifact["assumption_notes"]:
            self.assertIsInstance(note, str)

    def test_no_assumptions_key(self) -> None:
        artifact = self._run_test_with_mock_data()
        self.assertNotIn("assumptions", artifact)

    def test_no_left_ref_or_right_ref(self) -> None:
        artifact = self._run_test_with_mock_data()
        self.assertNotIn("left_ref", artifact)
        self.assertNotIn("right_ref", artifact)

    def test_kind_is_numeric(self) -> None:
        artifact = self._run_test_with_mock_data()
        self.assertEqual(artifact["kind"], "numeric")

    def test_no_sample_kind_key(self) -> None:
        artifact = self._run_test_with_mock_data()
        self.assertNotIn("sample_kind", artifact)
