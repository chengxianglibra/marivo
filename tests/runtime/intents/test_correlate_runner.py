from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from tests.runtime.intents._runner_fixtures import _FAKE_ARTIFACT_ID, _SESSION


class TestCorrelateRunnerCommitPath(unittest.TestCase):
    """run_correlate_intent must call commit_artifact_with_extraction(step_type='correlate')."""

    def _make_ts_artifact(self, metric: str = "m1") -> dict[str, Any]:
        return {
            "observation_type": "time_series",
            "metric": metric,
            "granularity": "day",
            "series": [
                {
                    "window": {"start": f"2024-01-{d:02d}", "end": f"2024-01-{d + 1:02d}"},
                    "value": float(d * 10),
                }
                for d in range(1, 8)  # 7 aligned pairs
            ],
        }

    def _make_runtime(self) -> MagicMock:
        runtime = MagicMock()
        runtime.core = MagicMock()
        runtime.resolve_artifact_for_ref.side_effect = [
            self._make_ts_artifact("m1"),
            self._make_ts_artifact("m2"),
        ]
        runtime.new_step_id.return_value = "step_4c2_001"
        runtime.resolve_artifact_id_for_step.return_value = "art_left_001"
        runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
        runtime.insert_step.return_value = None
        return runtime

    def _run_correlate(self, runtime: MagicMock) -> dict[str, Any]:
        from marivo.runtime.intents.correlate import run_correlate_intent

        params = {
            "left_ref": {"step_id": "step_left", "session_id": _SESSION},
            "right_ref": {"step_id": "step_right", "session_id": _SESSION},
        }
        return run_correlate_intent(runtime, _SESSION, params)

    def test_correlate_calls_commit_artifact_with_extraction(self) -> None:
        runtime = self._make_runtime()
        self._run_correlate(runtime)
        runtime.commit_artifact_with_extraction.assert_called_once()

    def test_correlate_passes_step_type_correlate(self) -> None:
        runtime = self._make_runtime()
        self._run_correlate(runtime)
        _, kwargs = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "correlate")

    def test_correlate_artifact_type_is_pairwise_ts_association(self) -> None:
        runtime = self._make_runtime()
        self._run_correlate(runtime)
        args, _ = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "pairwise_time_series_association")


# ── forecast ──────────────────────────────────────────────────────────────────
