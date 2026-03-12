"""Tests for the SQL dialect translation module (app/dialect.py)."""

from __future__ import annotations

import unittest

from app.dialect import translate


class DialectPassthroughTests(unittest.TestCase):
    """DuckDB target should return SQL unchanged."""

    def test_duckdb_passthrough(self) -> None:
        sql = "SELECT AVG(x) FILTER (WHERE y) FROM t"
        self.assertEqual(translate(sql, "duckdb"), sql)

    def test_unsupported_dialect_raises(self) -> None:
        with self.assertRaises(ValueError):
            translate("SELECT 1", "mysql")


class CastRewriteTests(unittest.TestCase):
    """Tests for ``expr::TYPE`` → ``CAST(expr AS TYPE)``."""

    def test_cast_rewrite_spark(self) -> None:
        sql = "SELECT preroll_timeout::DOUBLE FROM t"
        result = translate(sql, "spark")
        self.assertIn("CAST(preroll_timeout AS DOUBLE)", result)
        self.assertNotIn("::", result)

    def test_cast_rewrite_trino(self) -> None:
        sql = "SELECT preroll_timeout::DOUBLE FROM t"
        result = translate(sql, "trino")
        self.assertIn("CAST(preroll_timeout AS DOUBLE)", result)
        self.assertNotIn("::", result)

    def test_complex_filter_with_cast(self) -> None:
        """AVG(x::DOUBLE) should become AVG(CAST(x AS DOUBLE)) for Spark."""
        sql = "SELECT AVG(preroll_timeout::DOUBLE) FROM t"
        result = translate(sql, "spark")
        self.assertIn("CAST(preroll_timeout AS DOUBLE)", result)
        self.assertNotIn("::", result)

    def test_chained_filter_cast(self) -> None:
        """SUM(x) FILTER (WHERE ...)::DOUBLE should rewrite both FILTER and cast."""
        sql = "SELECT SUM(x) FILTER (WHERE period = 'current')::DOUBLE FROM t"
        result = translate(sql, "spark")
        # FILTER should be rewritten
        self.assertNotIn("FILTER", result)
        # Cast should be rewritten
        self.assertNotIn("::", result)
        self.assertIn("CAST(", result)
        self.assertIn("CASE WHEN", result)


class FilterRewriteTests(unittest.TestCase):
    """Tests for AGG(…) FILTER (WHERE …) rewriting."""

    def test_filter_rewrite_spark(self) -> None:
        sql = "SELECT AVG(x) FILTER (WHERE y > 0) FROM t"
        result = translate(sql, "spark")
        self.assertNotIn("FILTER", result)
        self.assertIn("CASE WHEN y > 0 THEN x END", result)

    def test_filter_not_rewritten_trino(self) -> None:
        """Trino supports FILTER natively — should not be rewritten."""
        sql = "SELECT AVG(x) FILTER (WHERE y > 0) FROM t"
        result = translate(sql, "trino")
        self.assertIn("FILTER", result)

    def test_count_star_filter(self) -> None:
        """COUNT(*) FILTER (WHERE …) → COUNT(CASE WHEN … THEN 1 END)."""
        sql = "SELECT COUNT(*) FILTER (WHERE period = 'current') FROM t"
        result = translate(sql, "spark")
        self.assertNotIn("FILTER", result)
        self.assertIn("COUNT(CASE WHEN period = 'current' THEN 1 END)", result)

    def test_multiple_filters_in_same_query(self) -> None:
        sql = (
            "SELECT "
            "AVG(x) FILTER (WHERE period = 'current') AS c, "
            "AVG(x) FILTER (WHERE period = 'baseline') AS b "
            "FROM t"
        )
        result = translate(sql, "spark")
        self.assertNotIn("FILTER", result)
        self.assertEqual(result.count("CASE WHEN"), 2)


class SchemaDDLTests(unittest.TestCase):
    """Tests for CREATE SCHEMA → CREATE DATABASE rewriting."""

    def test_schema_ddl_spark(self) -> None:
        sql = "CREATE SCHEMA IF NOT EXISTS analytics"
        result = translate(sql, "spark")
        self.assertIn("CREATE DATABASE IF NOT EXISTS analytics", result)

    def test_schema_ddl_trino_unchanged(self) -> None:
        sql = "CREATE SCHEMA IF NOT EXISTS analytics"
        result = translate(sql, "trino")
        self.assertIn("CREATE SCHEMA", result)


class RealQueryTests(unittest.TestCase):
    """Test dialect translation against real queries from step runners."""

    WATCH_TIME_QUERY = """
        WITH periodized AS (
            SELECT
                CASE
                    WHEN event_date BETWEEN ? AND ? THEN 'current'
                    WHEN event_date BETWEEN ? AND ? THEN 'baseline'
                END AS period,
                platform, app_version, network_type, content_type,
                play_duration_seconds
            FROM analytics.watch_events
            WHERE event_date BETWEEN ? AND ?
        ),
        aggregated AS (
            SELECT
                platform, app_version, network_type, content_type,
                AVG(play_duration_seconds) FILTER (WHERE period = 'current') AS current_watch_time,
                AVG(play_duration_seconds) FILTER (WHERE period = 'baseline') AS baseline_watch_time,
                COUNT(*) FILTER (WHERE period = 'current') AS current_sessions,
                COUNT(*) FILTER (WHERE period = 'baseline') AS baseline_sessions
            FROM periodized
            GROUP BY 1, 2, 3, 4
        )
        SELECT platform, app_version, network_type, content_type,
            ROUND(current_watch_time, 2) AS current_watch_time,
            ROUND(baseline_watch_time, 2) AS baseline_watch_time,
            ROUND(((current_watch_time - baseline_watch_time) / baseline_watch_time) * 100, 2) AS delta_pct,
            current_sessions, baseline_sessions
        FROM aggregated
        ORDER BY delta_pct ASC
        LIMIT 3
    """

    AD_QUERY = """
        SELECT
            AVG(preroll_timeout::DOUBLE) FILTER (WHERE period = 'current') AS current_timeout_rate,
            AVG(preroll_timeout::DOUBLE) FILTER (WHERE period = 'baseline') AS baseline_timeout_rate,
            COUNT(*) FILTER (WHERE period = 'current') AS current_sessions,
            COUNT(*) FILTER (WHERE period = 'baseline') AS baseline_sessions
        FROM periodized
        GROUP BY 1, 2, 3, 4
    """

    RECOMMENDATION_QUERY = """
        SELECT
            SUM(clicks) FILTER (WHERE period = 'current')::DOUBLE / SUM(impressions) FILTER (WHERE period = 'current') AS current_ctr,
            SUM(clicks) FILTER (WHERE period = 'baseline')::DOUBLE / SUM(impressions) FILTER (WHERE period = 'baseline') AS baseline_ctr
        FROM periodized
        GROUP BY 1, 2, 3, 4
    """

    def test_real_watch_time_query_spark(self) -> None:
        result = translate(self.WATCH_TIME_QUERY, "spark")
        self.assertNotIn("FILTER", result)
        self.assertIn("CASE WHEN", result)
        # No casts in this query
        self.assertNotIn("::", result)

    def test_real_ad_query_spark(self) -> None:
        result = translate(self.AD_QUERY, "spark")
        self.assertNotIn("FILTER", result)
        self.assertNotIn("::", result)
        self.assertIn("CAST(", result)
        self.assertIn("CASE WHEN", result)

    def test_real_recommendation_query_spark(self) -> None:
        result = translate(self.RECOMMENDATION_QUERY, "spark")
        self.assertNotIn("FILTER", result)
        self.assertNotIn("::", result)
        self.assertIn("CAST(", result)
        self.assertIn("CASE WHEN", result)


if __name__ == "__main__":
    unittest.main()
