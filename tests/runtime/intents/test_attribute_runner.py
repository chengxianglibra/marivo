from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from marivo.runtime.intents.attribute import run_attribute_intent


class TestAttributeRunnerOrchestration(unittest.TestCase):
    def _make_runtime(self) -> MagicMock:
        runtime = MagicMock()
        runtime.core.normalize_intent_metric_ref.side_effect = lambda metric: metric
        runtime.core.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix(
            "metric."
        )
        resolved_metric = MagicMock()
        resolved_metric.semantic_object = {
            "header": {"additive_dimensions": ["event_date", "channel", "region"]}
        }
        runtime.resolve_metric.return_value = resolved_metric
        runtime.insert_artifact.return_value = "art_attribute_001"
        runtime.insert_step.return_value = None
        return runtime

    def _params(self) -> dict[str, object]:
        return {
            "metric": "metric.revenue",
            "left": {
                "time_scope": {
                    "field": "event_date",
                    "start": "2026-01-08T00:00:00Z",
                    "end": "2026-01-15T00:00:00Z",
                },
                "scope": {"region": "US"},
            },
            "right": {
                "time_scope": {
                    "field": "event_date",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-08T00:00:00Z",
                },
                "scope": {"region": "US"},
            },
            "dimensions": ["channel", "region"],
            "decomposition_limit": 2,
        }

    def _observe_result(self, side: str) -> dict[str, object]:
        return {
            "observation_type": "scalar",
            "artifact_id": f"art_{side}",
            "step_ref": {
                "session_id": "sess_attr",
                "step_id": f"step_{side}",
                "step_type": "observe",
            },
            "time_scope": {"kind": "range", "start": "2026-01-01", "end": "2026-01-08"},
        }

    def _compare_result(self) -> dict[str, object]:
        return {
            "artifact_id": "art_compare",
            "step_ref": {
                "session_id": "sess_attr",
                "step_id": "step_compare",
                "step_type": "compare",
            },
            "comparability": {"status": "comparable", "issues": []},
            "left_value": 120.0,
            "right_value": 100.0,
            "absolute_delta": 20.0,
            "relative_delta": 0.2,
            "direction": "increase",
            "result": {"artifact_id": "art_compare", "result": {"comparison_type": "scalar_delta"}},
        }

    def _decompose_result(self, dimension: str) -> dict[str, object]:
        return {
            "artifact_id": f"art_decompose_{dimension}",
            "step_ref": {
                "session_id": "sess_attr",
                "step_id": f"step_decompose_{dimension}",
                "step_type": "decompose",
            },
            "attribution": {"status": "attributable", "issues": []},
            "rows": [
                {
                    dimension: "A",
                    "absolute_contribution": 12.0,
                    "contribution_share": 0.6,
                },
                {
                    dimension: "B",
                    "absolute_contribution": 8.0,
                    "contribution_share": 0.4,
                },
            ],
            "scope_absolute_delta": 20.0,
            "result": {
                "artifact_id": f"art_decompose_{dimension}",
                "result": {"dimension": dimension},
            },
        }

    def test_attribute_expands_child_runners_and_commits_bundle(self) -> None:
        runtime = self._make_runtime()
        decompose_results = [self._decompose_result("channel"), self._decompose_result("region")]

        with (
            patch(
                "marivo.runtime.intents.attribute.run_observe_intent",
                side_effect=[self._observe_result("left"), self._observe_result("right")],
            ) as mock_observe,
            patch(
                "marivo.runtime.intents.attribute.run_compare_intent",
                return_value=self._compare_result(),
            ) as mock_compare,
            patch(
                "marivo.runtime.intents.attribute.run_decompose_intent",
                side_effect=decompose_results,
            ) as mock_decompose,
        ):
            result = run_attribute_intent(runtime, "sess_attr", self._params())

        self.assertEqual(mock_observe.call_count, 2)
        self.assertEqual(mock_compare.call_count, 1)
        self.assertEqual(
            mock_compare.call_args.args[2],
            {"left_artifact_id": "art_left", "right_artifact_id": "art_right"},
        )
        self.assertEqual(mock_decompose.call_count, 2)
        self.assertEqual(
            [call.args[2]["dimension"] for call in mock_decompose.call_args_list],
            ["channel", "region"],
        )
        runtime.insert_artifact.assert_called_once()
        runtime.insert_step.assert_called_once()
        self.assertEqual(result["intent_type"], "attribute")
        self.assertEqual(result["artifact_id"], "art_attribute_001")
        self.assertEqual(result["result"]["bundle_type"], "attribute_bundle")
        self.assertEqual(result["product_metadata"]["status"], "succeeded")
        self.assertEqual(result["result"]["dimensions"], ["channel", "region"])
