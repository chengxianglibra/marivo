"""Tests for the simplified CalendarConfig model."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from marivo.config import CalendarConfig


class CalendarConfigDefaultsTest(unittest.TestCase):
    """CalendarConfig provides sensible defaults for all fields."""

    def test_defaults(self) -> None:
        cfg = CalendarConfig()
        self.assertEqual(cfg.region_code, "CN")
        self.assertIsNone(cfg.calendar_version)


class CalendarConfigRegionCodeTest(unittest.TestCase):
    """region_code accepts any non-empty string."""

    def test_custom_region_code(self) -> None:
        cfg = CalendarConfig(region_code="US")
        self.assertEqual(cfg.region_code, "US")

    def test_region_code_strips_whitespace(self) -> None:
        # Pydantic str fields do not auto-strip; raw value is stored.
        cfg = CalendarConfig(region_code="  JP  ")
        self.assertEqual(cfg.region_code, "  JP  ")


class CalendarConfigVersionTest(unittest.TestCase):
    """calendar_version is optional but rejects sentinel values."""

    def test_valid_version(self) -> None:
        cfg = CalendarConfig(calendar_version="v2024.1")
        self.assertEqual(cfg.calendar_version, "v2024.1")

    def test_none_version_is_default(self) -> None:
        cfg = CalendarConfig()
        self.assertIsNone(cfg.calendar_version)

    def test_explicit_none(self) -> None:
        cfg = CalendarConfig(calendar_version=None)
        self.assertIsNone(cfg.calendar_version)

    def test_rejects_latest(self) -> None:
        with self.assertRaises(ValidationError):
            CalendarConfig(calendar_version="latest")

    def test_rejects_current(self) -> None:
        with self.assertRaises(ValidationError):
            CalendarConfig(calendar_version="current")

    def test_rejects_latest_case_insensitive(self) -> None:
        """The field_validator rejects 'latest' and 'current'
        case-insensitively."""
        with self.assertRaises(ValidationError):
            CalendarConfig(calendar_version="LATEST")
        with self.assertRaises(ValidationError):
            CalendarConfig(calendar_version="Current")

    def test_allows_version_containing_latest(self) -> None:
        """A version string that merely contains 'latest' but is not
        exactly 'latest' is allowed."""
        cfg = CalendarConfig(calendar_version="not-latest-v1")
        self.assertEqual(cfg.calendar_version, "not-latest-v1")


class CalendarConfigExtraFieldsTest(unittest.TestCase):
    """CalendarConfig forbids extra fields (via ConfigDict)."""

    def test_rejects_unknown_field(self) -> None:
        with self.assertRaises(ValidationError):
            CalendarConfig(region_code="CN", snapshots=[])  # type: ignore[call-arg]

    def test_rejects_arbitrary_extra(self) -> None:
        with self.assertRaises(ValidationError):
            CalendarConfig(region_code="CN", foo="bar")  # type: ignore[call-arg]


class CalendarConfigInMarivoConfigTest(unittest.TestCase):
    """CalendarConfig integrates correctly within MarivoConfig."""

    def test_marivo_config_calendar_default(self) -> None:
        from marivo.config import MarivoConfig

        cfg = MarivoConfig()
        self.assertIsInstance(cfg.calendar, CalendarConfig)
        self.assertEqual(cfg.calendar.region_code, "CN")
        self.assertIsNone(cfg.calendar.calendar_version)

    def test_marivo_config_calendar_custom(self) -> None:
        from marivo.config import MarivoConfig

        cfg = MarivoConfig(calendar={"region_code": "US", "calendar_version": "v2025.1"})
        self.assertEqual(cfg.calendar.region_code, "US")
        self.assertEqual(cfg.calendar.calendar_version, "v2025.1")


if __name__ == "__main__":
    unittest.main()
