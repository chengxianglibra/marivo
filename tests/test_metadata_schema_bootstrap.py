from __future__ import annotations

import unittest

from app.storage.schema import (
    METADATA_SCHEMA_MARKER_TABLE,
    METADATA_SCHEMA_VERSION,
    evaluate_metadata_schema_state,
    expected_metadata_tables,
    metadata_ddl_fingerprint,
    metadata_schema_marker_row,
)


class MetadataSchemaBootstrapTests(unittest.TestCase):
    def test_empty_schema_can_be_initialized(self) -> None:
        check = evaluate_metadata_schema_state("mysql", set(), None)

        self.assertEqual(check.state, "empty")

    def test_marker_only_schema_fails_closed(self) -> None:
        check = evaluate_metadata_schema_state("mysql", {METADATA_SCHEMA_MARKER_TABLE}, None)

        self.assertEqual(check.state, "invalid")
        self.assertIn("missing tables", check.reason)

    def test_unknown_table_fails_closed(self) -> None:
        check = evaluate_metadata_schema_state("mysql", {"customer_table"}, None)

        self.assertEqual(check.state, "invalid")
        self.assertIn("unknown tables", check.reason)

    def test_missing_marker_fails_closed(self) -> None:
        tables = expected_metadata_tables("mysql") - {METADATA_SCHEMA_MARKER_TABLE}
        check = evaluate_metadata_schema_state("mysql", tables, None)

        self.assertEqual(check.state, "invalid")
        self.assertIn("marker table is missing", check.reason)

    def test_incomplete_shape_fails_closed(self) -> None:
        tables = expected_metadata_tables("mysql") - {"sessions"}
        check = evaluate_metadata_schema_state("mysql", tables, metadata_schema_marker_row("mysql"))

        self.assertEqual(check.state, "invalid")
        self.assertIn("missing tables", check.reason)

    def test_marker_backend_mismatch_fails_closed(self) -> None:
        marker = metadata_schema_marker_row("sqlite")
        check = evaluate_metadata_schema_state("mysql", expected_metadata_tables("mysql"), marker)

        self.assertEqual(check.state, "invalid")
        self.assertIn("backend mismatch", check.reason)

    def test_marker_schema_version_mismatch_fails_closed(self) -> None:
        marker = metadata_schema_marker_row("mysql") | {"schema_version": "old"}
        check = evaluate_metadata_schema_state("mysql", expected_metadata_tables("mysql"), marker)

        self.assertEqual(check.state, "invalid")
        self.assertIn("schema_version mismatch", check.reason)

    def test_marker_fingerprint_mismatch_fails_closed(self) -> None:
        marker = metadata_schema_marker_row("mysql") | {"ddl_fingerprint": "wrong"}
        check = evaluate_metadata_schema_state("mysql", expected_metadata_tables("mysql"), marker)

        self.assertEqual(check.state, "invalid")
        self.assertIn("ddl_fingerprint mismatch", check.reason)

    def test_current_schema_is_recognized(self) -> None:
        marker = metadata_schema_marker_row("mysql")
        check = evaluate_metadata_schema_state("mysql", expected_metadata_tables("mysql"), marker)

        self.assertEqual(check.state, "current")
        self.assertEqual(marker["backend"], "mysql")
        self.assertEqual(marker["schema_version"], METADATA_SCHEMA_VERSION)
        self.assertEqual(marker["ddl_fingerprint"], metadata_ddl_fingerprint("mysql"))


if __name__ == "__main__":
    unittest.main()
