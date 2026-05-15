"""Tests for value-expression extraction helper."""

from __future__ import annotations

import unittest

from marivo.core.semantic.value_expr import extract_value_expression


class TestExtractValueExpression(unittest.TestCase):
    def test_sum_simple_column(self) -> None:
        self.assertEqual(extract_value_expression("SUM(revenue)", "sum"), "revenue")

    def test_sum_case_expression(self) -> None:
        self.assertEqual(
            extract_value_expression("SUM(CASE WHEN x THEN y ELSE 0 END)", "sum"),
            "CASE WHEN x THEN y ELSE 0 END",
        )

    def test_sum_with_whitespace(self) -> None:
        self.assertEqual(extract_value_expression("  SUM( revenue )  ", "sum"), "revenue")

    def test_sum_lowercase(self) -> None:
        self.assertEqual(extract_value_expression("sum(revenue)", "sum"), "revenue")

    def test_sum_mixed_case(self) -> None:
        self.assertEqual(extract_value_expression("Sum(revenue)", "sum"), "revenue")

    def test_sum_nested_expression(self) -> None:
        self.assertEqual(
            extract_value_expression("SUM(price * quantity)", "sum"),
            "price * quantity",
        )

    def test_count_returns_none(self) -> None:
        self.assertIsNone(extract_value_expression("COUNT(*)", "sum"))

    def test_avg_returns_none(self) -> None:
        self.assertIsNone(extract_value_expression("AVG(x)", "sum"))

    def test_ratio_semantics_returns_none(self) -> None:
        self.assertIsNone(extract_value_expression("SUM(revenue)", "ratio"))

    def test_weighted_average_returns_none(self) -> None:
        self.assertIsNone(extract_value_expression("SUM(revenue)", "weighted_average"))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(extract_value_expression("", "sum"))

    def test_bare_column_returns_none(self) -> None:
        self.assertIsNone(extract_value_expression("revenue", "sum"))

    def test_multiple_aggregates_returns_none(self) -> None:
        self.assertIsNone(extract_value_expression("SUM(a) + SUM(b)", "sum"))


if __name__ == "__main__":
    unittest.main()
