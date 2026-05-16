from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from tests.runtime.intents._runner_fixtures import (
    _FAKE_ARTIFACT_ID,
    _SESSION,
    _FakeCalendarDataReader,
    _scalar_observation,
    _time_series_observation,
)


class TestCompareRunnerCommitPath(unittest.TestCase):
    """run_compare_intent must call _commit_artifact_with_extraction(step_type='compare')."""

    def _make_runtime(self) -> MagicMock:
        runtime = MagicMock()
        runtime.core = MagicMock()
        runtime.new_step_id.return_value = "step_4c2_001"
        runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
        runtime.insert_step.return_value = None
        return runtime

    def _run_scalar_compare(self, runtime: MagicMock) -> dict[str, Any]:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime.resolve_artifact_for_ref.side_effect = [
            _scalar_observation("m1"),
            _scalar_observation("m1"),
        ]
        params = {
            "left_ref": {"step_id": "step_left", "session_id": _SESSION, "step_type": "observe"},
            "right_ref": {"step_id": "step_right", "session_id": _SESSION, "step_type": "observe"},
        }
        return run_compare_intent(runtime, _SESSION, params)

    def test_compare_calls_commit_artifact_with_extraction(self) -> None:
        runtime = self._make_runtime()
        self._run_scalar_compare(runtime)
        runtime.commit_artifact_with_extraction.assert_called_once()

    def test_compare_passes_step_type_compare(self) -> None:
        runtime = self._make_runtime()
        self._run_scalar_compare(runtime)
        _, kwargs = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "compare")

    def test_compare_artifact_type_is_compare_artifact(self) -> None:
        runtime = self._make_runtime()
        self._run_scalar_compare(runtime)
        args, _ = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "compare_artifact")

    def test_compare_type_non_normal_rejects_scalar_observations(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _scalar_observation("m1")
        right = _scalar_observation("m1")
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(ValueError, "compare_type 'yoy' requires time_series"):
            run_compare_intent(
                runtime,
                _SESSION,
                {
                    "left_ref": {
                        "step_id": "step_left",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "step_id": "step_right",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                    "compare_type": "yoy",
                },
            )

    def test_compare_time_series_commits_time_series_delta(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        runtime.resolve_artifact_for_ref.side_effect = [
            _time_series_observation("m1"),
            _time_series_observation(
                "m1",
                series=[
                    {
                        "window": {"start": "2024-01-01", "end": "2024-01-02"},
                        "value": 8.0,
                    },
                    {
                        "window": {"start": "2024-01-02", "end": "2024-01-03"},
                        "value": 15.0,
                    },
                ],
            ),
        ]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
            },
        )

        self.assertEqual(result["comparison_type"], "time_series_delta")
        self.assertEqual(result["granularity"], "day")
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["summary_left_value"], 30.0)
        self.assertEqual(result["summary_right_value"], 23.0)
        self.assertEqual(result["analytical_metadata"]["pairing_basis"], "observed_series")
        self.assertEqual(
            result["analytical_metadata"]["pairing_rule"], "intersection_by_time_bucket"
        )

    def test_compare_type_yoy_aligns_time_series_by_baseline_window(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2026-02-14", "end": "2026-02-15"}, "value": 10.0},
                {"window": {"start": "2026-02-15", "end": "2026-02-16"}, "value": 12.0},
            ],
        )
        left["time_scope"] = {"kind": "range", "start": "2026-02-14", "end": "2026-02-16"}
        right = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2025-02-14", "end": "2025-02-15"}, "value": 9.0},
                {"window": {"start": "2025-02-15", "end": "2025-02-16"}, "value": 11.0},
            ],
        )
        right["time_scope"] = {"kind": "range", "start": "2025-02-14", "end": "2025-02-16"}
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "compare_type": "yoy",
            },
        )

        self.assertEqual(
            result["analytical_metadata"]["pairing_basis"], "compare_type_calendar_alignment"
        )
        self.assertEqual(result["analytical_metadata"]["pairing_rule"], "natural_date")
        self.assertEqual(result["analytical_metadata"]["compare_type"], "yoy")
        self.assertEqual(result["summary_left_value"], 22.0)
        self.assertEqual(result["summary_right_value"], 20.0)
        self.assertEqual(result["summary_absolute_delta"], 2.0)
        self.assertEqual(
            result["analytical_metadata"]["matched_left_time_scope"],
            {"kind": "range", "start": "2026-02-14", "end": "2026-02-16"},
        )
        self.assertEqual(
            result["analytical_metadata"]["matched_right_time_scope"],
            {"kind": "range", "start": "2025-02-14", "end": "2025-02-16"},
        )
        self.assertEqual(result["rows"][0]["left_value"], 10.0)
        self.assertEqual(result["rows"][0]["right_value"], 9.0)

    def test_compare_type_mom_aligns_time_series_to_previous_period(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2026-04-08", "end": "2026-04-09"}, "value": 30.0},
                {"window": {"start": "2026-04-09", "end": "2026-04-10"}, "value": 40.0},
            ],
        )
        left["time_scope"] = {"kind": "range", "start": "2026-04-08", "end": "2026-04-10"}
        right = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2026-04-06", "end": "2026-04-07"}, "value": 20.0},
                {"window": {"start": "2026-04-07", "end": "2026-04-08"}, "value": 25.0},
            ],
        )
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "compare_type": "mom",
            },
        )

        self.assertEqual(result["analytical_metadata"]["pairing_rule"], "natural_date")
        self.assertEqual(result["summary_left_value"], 70.0)
        self.assertEqual(result["summary_right_value"], 45.0)
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["baseline_window"],
            {"start": "2026-04-06", "end": "2026-04-08"},
        )

    def test_compare_type_wow_aligns_time_series_to_previous_week(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2026-04-08", "end": "2026-04-09"}, "value": 30.0},
                {"window": {"start": "2026-04-09", "end": "2026-04-10"}, "value": 40.0},
            ],
        )
        left["time_scope"] = {"kind": "range", "start": "2026-04-08", "end": "2026-04-10"}
        right = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2026-04-01", "end": "2026-04-02"}, "value": 20.0},
                {"window": {"start": "2026-04-02", "end": "2026-04-03"}, "value": 25.0},
            ],
        )
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "compare_type": "wow",
            },
        )

        self.assertEqual(result["analytical_metadata"]["pairing_rule"], "same_weekday")
        self.assertEqual(result["summary_right_value"], 45.0)
        self.assertEqual(
            result["analytical_metadata"]["matched_right_time_scope"],
            {"kind": "range", "start": "2026-04-01", "end": "2026-04-03"},
        )

    def test_compare_type_weekday_aligned_yoy_uses_nearest_weekday(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _time_series_observation(
            "m1",
            series=[{"window": {"start": "2026-04-02", "end": "2026-04-03"}, "value": 120.0}],
        )
        left["time_scope"] = {"kind": "range", "start": "2026-04-02", "end": "2026-04-04"}
        right = _time_series_observation(
            "m1",
            series=[{"window": {"start": "2025-04-03", "end": "2025-04-04"}, "value": 100.0}],
        )
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "compare_type": "weekday_aligned_yoy",
            },
        )

        self.assertEqual(result["analytical_metadata"]["pairing_rule"], "same_weekday")
        self.assertEqual(result["rows"][0]["right_value"], 100.0)
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
                "pairing_reason"
            ],
            "same_weekday_nearest",
        )

    def test_compare_type_weekday_aligned_mom_falls_back_to_natural_date(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _time_series_observation(
            "m1",
            series=[{"window": {"start": "2026-04-08", "end": "2026-04-09"}, "value": 120.0}],
        )
        left["time_scope"] = {"kind": "range", "start": "2026-04-08", "end": "2026-04-09"}
        right = _time_series_observation(
            "m1",
            series=[{"window": {"start": "2026-04-07", "end": "2026-04-08"}, "value": 100.0}],
        )
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "compare_type": "weekday_aligned_mom",
            },
        )

        self.assertEqual(result["rows"][0]["right_value"], 100.0)
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
                "pairing_reason"
            ],
            "natural_date_shift",
        )

    def test_compare_type_holiday_aligned_yoy_reads_calendar_data(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        runtime.calendar_data_reader = _FakeCalendarDataReader()
        left = _time_series_observation(
            "m1",
            series=[{"window": {"start": "2026-02-20", "end": "2026-02-21"}, "value": 120.0}],
        )
        left["time_scope"] = {"kind": "range", "start": "2026-02-20", "end": "2026-02-21"}
        right = _time_series_observation(
            "m1",
            series=[{"window": {"start": "2025-02-20", "end": "2025-02-21"}, "value": 100.0}],
        )
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "compare_type": "holiday_aligned_yoy",
            },
        )

        self.assertEqual(result["rows"][0]["right_value"], 100.0)
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["resolved_calendar_version"],
            "cn_2026_v1",
        )
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
                "pairing_reason"
            ],
            "holiday_cluster",
        )

    def test_compare_type_holiday_aligned_yoy_requires_calendar_reader(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        runtime.calendar_data_reader = None
        left = _time_series_observation("m1")
        right = _time_series_observation("m1")
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(ValueError, "requires configured calendar data"):
            run_compare_intent(
                runtime,
                _SESSION,
                {
                    "left_ref": {
                        "step_id": "step_left",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "step_id": "step_right",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                    "compare_type": "holiday_aligned_yoy",
                },
            )

    def test_compare_time_series_missing_granularity_fails(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _time_series_observation("m1")
        right = _time_series_observation("m1")
        left["granularity"] = None
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(
            ValueError, "compare: NOT_COMPARABLE - time_series observations must include"
        ):
            run_compare_intent(
                runtime,
                _SESSION,
                {
                    "left_ref": {
                        "step_id": "step_left",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "step_id": "step_right",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                },
            )

    def test_compare_time_series_empty_series_fails_before_commit(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        runtime.resolve_artifact_for_ref.side_effect = [
            _time_series_observation("m1", series=[]),
            _time_series_observation("m1", series=[]),
        ]

        with self.assertRaisesRegex(
            ValueError, "compare: NOT_COMPARABLE - no time-series buckets found"
        ):
            run_compare_intent(
                runtime,
                _SESSION,
                {
                    "left_ref": {
                        "step_id": "step_left",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "step_id": "step_right",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                },
            )
        runtime.commit_artifact_with_extraction.assert_not_called()

    # ── decompose ─────────────────────────────────────────────────────────────────
