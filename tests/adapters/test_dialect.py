"""Tests for the SQL dialect translation module (app/dialect.py)."""

from __future__ import annotations

import unittest

from marivo.dialect import translate


class DialectPassthroughTests(unittest.TestCase):
    """DuckDB target should return SQL unchanged."""

    def test_duckdb_passthrough(self) -> None:
        sql = "SELECT AVG(x) FILTER (WHERE y) FROM t"
        self.assertEqual(translate(sql, "duckdb"), sql)

    def test_unsupported_dialect_raises(self) -> None:
        with self.assertRaises(ValueError):
            translate("SELECT 1", "mysql")

    def test_spark_unsupported(self) -> None:
        with self.assertRaises(ValueError):
            translate("SELECT 1", "spark")


class CastRewriteTests(unittest.TestCase):
    """Tests for ``expr::TYPE`` → ``CAST(expr AS TYPE)``."""

    def test_cast_rewrite_trino(self) -> None:
        sql = "SELECT preroll_timeout::DOUBLE FROM t"
        result = translate(sql, "trino")
        self.assertIn("CAST(preroll_timeout AS DOUBLE)", result)
        self.assertNotIn("::", result)

    def test_complex_cast_trino(self) -> None:
        """AVG(x::DOUBLE) should become AVG(CAST(x AS DOUBLE)) for Trino."""
        sql = "SELECT AVG(preroll_timeout::DOUBLE) FROM t"
        result = translate(sql, "trino")
        self.assertIn("CAST(preroll_timeout AS DOUBLE)", result)
        self.assertNotIn("::", result)

    def test_filter_preserved_trino(self) -> None:
        """Trino supports FILTER natively — should not be rewritten."""
        sql = "SELECT AVG(x) FILTER (WHERE y > 0) FROM t"
        result = translate(sql, "trino")
        self.assertIn("FILTER", result)

    def test_schema_ddl_trino_unchanged(self) -> None:
        sql = "CREATE SCHEMA IF NOT EXISTS analytics"
        result = translate(sql, "trino")
        self.assertIn("CREATE SCHEMA", result)

    def test_paren_cast_trino(self) -> None:
        """SUM(x) FILTER (WHERE ...)::DOUBLE should rewrite the cast."""
        sql = "SELECT SUM(x) FILTER (WHERE period = 'current')::DOUBLE FROM t"
        result = translate(sql, "trino")
        self.assertNotIn("::", result)
        self.assertIn("CAST(", result)
        # FILTER should be preserved for Trino
        self.assertIn("FILTER", result)


if __name__ == "__main__":
    unittest.main()
