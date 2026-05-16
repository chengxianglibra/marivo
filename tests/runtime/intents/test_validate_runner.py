from __future__ import annotations

import unittest


class TestValidateIntentValidation(unittest.TestCase):
    """Validation tests for the validate intent runner."""

    def test_missing_metric_rejected(self) -> None:
        from unittest.mock import MagicMock

        from marivo.runtime.intents.validate import run_validate_intent

        runtime = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            run_validate_intent(
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

    def test_missing_time_scope_rejected(self) -> None:
        from unittest.mock import MagicMock

        from marivo.runtime.intents.validate import run_validate_intent

        runtime = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            run_validate_intent(
                runtime,
                "session-1",
                {
                    "metric": "metric.test",
                    "left": {},
                    "right": {},
                },
            )
        self.assertIn("time_scope", str(ctx.exception))
