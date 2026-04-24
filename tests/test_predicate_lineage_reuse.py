"""Tests for predicate filter lineage reuse (task 5.5) and boundary enforcement (task 5.6).

Task 5.5: Compare-like intents reuse frozen predicate lineage.
Task 5.6: Artifact stores refs-only; read surface exposes handles-only.
"""

from __future__ import annotations

import unittest
from typing import Any

from app.evidence_engine.ref_boundary import RefBoundaryError
from app.intents.predicate_lineage_reuse import (
    assert_predicate_lineage_refs_only,
    normalize_predicate_filter_lineage,
    resolve_predicate_lineage_reuse,
    resolve_predicate_lineage_reuse_for_intent,
)


def _error_factory() -> ValueError:
    return ValueError("test: INVALID_ARGUMENT - malformed predicate filter lineage")


def _make_lineage(
    *,
    gov_refs: list[str] | None = None,
    carrier_refs: list[str] | None = None,
    request_scope_ref: str | None = None,
    default_refs: list[str] | None = None,
    component_lineages: list[dict[str, Any]] | None = None,
    component_scopes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a valid predicate_filter_lineage dict for testing."""
    shared: dict[str, Any] = {
        "governance_policy_refs": gov_refs or [],
        "carrier_row_filter_refs": carrier_refs or [],
    }
    if request_scope_ref is not None:
        shared["request_scope_ref"] = request_scope_ref
    return {
        "shared_effective_scope": shared,
        "metric_default_lineage": {"default_predicate_refs": default_refs or []},
        "component_qualifier_lineages": component_lineages or [],
        "component_effective_scopes": component_scopes or [],
    }


def _make_component_scope(
    component_field: str,
    *,
    effective_scope_refs: list[str] | None = None,
    fingerprint: str = "abcd1234efgh5678",
) -> dict[str, Any]:
    return {
        "component_field": component_field,
        "effective_scope_refs": effective_scope_refs or [],
        "scope_fingerprint": fingerprint,
    }


def _make_component_lineage(
    component_field: str,
    *,
    qualifier_refs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "component_field": component_field,
        "qualifier_refs": qualifier_refs or [],
    }


# ---------------------------------------------------------------------------
# Normalization tests
# ---------------------------------------------------------------------------


class TestNormalizePredicateFilterLineage(unittest.TestCase):
    def test_valid_lineage_passes(self) -> None:
        lineage = _make_lineage(default_refs=["predicate.d1"])
        result = normalize_predicate_filter_lineage(lineage, error_factory=_error_factory)
        self.assertIn("shared_effective_scope", result)

    def test_none_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_predicate_filter_lineage(None, error_factory=_error_factory)

    def test_non_dict_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_predicate_filter_lineage("not a dict", error_factory=_error_factory)

    def test_unknown_keys_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_predicate_filter_lineage(
                {"shared_effective_scope": {}, "unknown_key": True},
                error_factory=_error_factory,
            )

    def test_metric_default_lineage_non_dict_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_predicate_filter_lineage(
                {"shared_effective_scope": {}, "metric_default_lineage": "bad"},
                error_factory=_error_factory,
            )

    def test_component_qualifier_lineages_non_list_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_predicate_filter_lineage(
                {"shared_effective_scope": {}, "component_qualifier_lineages": "bad"},
                error_factory=_error_factory,
            )

    def test_component_qualifier_lineages_non_dict_entry_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_predicate_filter_lineage(
                {"shared_effective_scope": {}, "component_qualifier_lineages": ["bad"]},
                error_factory=_error_factory,
            )

    def test_component_effective_scopes_non_list_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_predicate_filter_lineage(
                {"shared_effective_scope": {}, "component_effective_scopes": 42},
                error_factory=_error_factory,
            )

    def test_component_effective_scopes_non_dict_entry_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_predicate_filter_lineage(
                {"shared_effective_scope": {}, "component_effective_scopes": ["bad"]},
                error_factory=_error_factory,
            )

    def test_shared_effective_scope_non_dict_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_predicate_filter_lineage(
                {"shared_effective_scope": "bad"},
                error_factory=_error_factory,
            )

    def test_default_predicate_refs_non_list_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_predicate_filter_lineage(
                {
                    "shared_effective_scope": {},
                    "metric_default_lineage": {"default_predicate_refs": "bad"},
                },
                error_factory=_error_factory,
            )


# ---------------------------------------------------------------------------
# Core reuse resolution tests
# ---------------------------------------------------------------------------


class TestResolvePredicateLineageReuse(unittest.TestCase):
    def test_both_none_no_issues(self) -> None:
        result = resolve_predicate_lineage_reuse(
            left_predicate_filter_lineage=None,
            right_predicate_filter_lineage=None,
            error_factory=_error_factory,
        )
        self.assertEqual(result["issues"], [])
        self.assertIsNone(result["fatal_message"])
        self.assertIsNone(result["reuse_summary"])

    def test_one_none_one_present_is_fatal(self) -> None:
        lineage = _make_lineage()
        result = resolve_predicate_lineage_reuse(
            left_predicate_filter_lineage=lineage,
            right_predicate_filter_lineage=None,
            error_factory=_error_factory,
        )
        self.assertTrue(len(result["issues"]) >= 1)
        self.assertEqual(result["issues"][0]["code"], "predicate_lineage_metadata_mismatch")
        self.assertIsNotNone(result["fatal_message"])
        self.assertIsNone(result["reuse_summary"])

    def test_identical_lineage_no_issues(self) -> None:
        lineage = _make_lineage(
            default_refs=["predicate.d1"],
            carrier_refs=["predicate.c1"],
            component_lineages=[
                _make_component_lineage("numerator", qualifier_refs=["predicate.q1"]),
                _make_component_lineage("denominator"),
            ],
            component_scopes=[
                _make_component_scope("numerator", fingerprint="aaa1111bbb2222"),
                _make_component_scope("denominator", fingerprint="ccc3333ddd4444"),
            ],
        )
        result = resolve_predicate_lineage_reuse(
            left_predicate_filter_lineage=lineage,
            right_predicate_filter_lineage=lineage,
            error_factory=_error_factory,
        )
        self.assertEqual(result["issues"], [])
        self.assertIsNone(result["fatal_message"])
        self.assertIsNotNone(result["reuse_summary"])

    def test_metric_default_mismatch_is_fatal(self) -> None:
        left = _make_lineage(default_refs=["predicate.d1"])
        right = _make_lineage(default_refs=["predicate.d2"])
        result = resolve_predicate_lineage_reuse(
            left_predicate_filter_lineage=left,
            right_predicate_filter_lineage=right,
            error_factory=_error_factory,
        )
        mismatch_issues = [
            i for i in result["issues"] if i["code"] == "metric_default_predicate_mismatch"
        ]
        self.assertEqual(len(mismatch_issues), 1)
        self.assertIsNotNone(result["fatal_message"])
        self.assertIsNone(result["reuse_summary"])

    def test_component_structure_mismatch_is_fatal(self) -> None:
        left = _make_lineage(
            component_lineages=[_make_component_lineage("numerator")],
            component_scopes=[_make_component_scope("numerator")],
        )
        right = _make_lineage(
            component_lineages=[_make_component_lineage("count_target")],
            component_scopes=[_make_component_scope("count_target")],
        )
        result = resolve_predicate_lineage_reuse(
            left_predicate_filter_lineage=left,
            right_predicate_filter_lineage=right,
            error_factory=_error_factory,
        )
        structure_issues = [
            i for i in result["issues"] if i["code"] == "component_structure_mismatch"
        ]
        self.assertEqual(len(structure_issues), 1)
        self.assertIsNotNone(result["fatal_message"])

    def test_scope_divergence_is_warning(self) -> None:
        left = _make_lineage(carrier_refs=["predicate.c1"])
        right = _make_lineage(carrier_refs=["predicate.c2"])
        result = resolve_predicate_lineage_reuse(
            left_predicate_filter_lineage=left,
            right_predicate_filter_lineage=right,
            error_factory=_error_factory,
        )
        divergence_issues = [i for i in result["issues"] if i["code"] == "scope_divergence"]
        self.assertEqual(len(divergence_issues), 1)
        self.assertEqual(divergence_issues[0]["severity"], "warning")
        self.assertFalse(divergence_issues[0]["blocking"])
        self.assertIsNone(result["fatal_message"])
        self.assertIsNotNone(result["reuse_summary"])

    def test_component_fingerprint_divergence_is_warning(self) -> None:
        left = _make_lineage(
            component_lineages=[_make_component_lineage("numerator")],
            component_scopes=[_make_component_scope("numerator", fingerprint="aaa1111bbb2222")],
        )
        right = _make_lineage(
            component_lineages=[_make_component_lineage("numerator")],
            component_scopes=[_make_component_scope("numerator", fingerprint="zzz9999yyy8888")],
        )
        result = resolve_predicate_lineage_reuse(
            left_predicate_filter_lineage=left,
            right_predicate_filter_lineage=right,
            error_factory=_error_factory,
        )
        fp_issues = [
            i for i in result["issues"] if i["code"] == "component_scope_fingerprint_divergence"
        ]
        self.assertEqual(len(fp_issues), 1)
        self.assertEqual(fp_issues[0]["severity"], "warning")
        self.assertFalse(fp_issues[0]["blocking"])
        self.assertIsNone(result["fatal_message"])
        self.assertIsNotNone(result["reuse_summary"])

    def test_reuse_summary_contains_refs_not_expressions(self) -> None:
        lineage = _make_lineage(
            default_refs=["predicate.d1"],
            component_lineages=[
                _make_component_lineage("numerator", qualifier_refs=["predicate.q1"])
            ],
            component_scopes=[_make_component_scope("numerator", fingerprint="aaa1111bbb2222")],
        )
        result = resolve_predicate_lineage_reuse(
            left_predicate_filter_lineage=lineage,
            right_predicate_filter_lineage=lineage,
            error_factory=_error_factory,
        )
        summary = result["reuse_summary"]
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary["reuse_source"], "observation_predicate_filter_lineage")
        self.assertEqual(summary["metric_default_predicate_refs"], ["predicate.d1"])
        self.assertIn("numerator", summary["component_fields"])
        self.assertIn("numerator", summary["left_scope_fingerprints"])
        self.assertNotIn("expression", summary)
        self.assertNotIn("sql", summary)

    def test_convenience_wrapper_for_intent(self) -> None:
        lineage = _make_lineage()
        result = resolve_predicate_lineage_reuse_for_intent(
            intent_name="compare",
            left_predicate_filter_lineage=lineage,
            right_predicate_filter_lineage=lineage,
        )
        self.assertEqual(result["issues"], [])
        self.assertIsNotNone(result["reuse_summary"])

    def test_convenience_wrapper_malformed_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_predicate_lineage_reuse_for_intent(
                intent_name="compare",
                left_predicate_filter_lineage="not a dict",
                right_predicate_filter_lineage=None,
            )
        self.assertIn("malformed predicate filter lineage", str(ctx.exception))

    def test_default_refs_order_independent(self) -> None:
        left = _make_lineage(default_refs=["predicate.d2", "predicate.d1"])
        right = _make_lineage(default_refs=["predicate.d1", "predicate.d2"])
        result = resolve_predicate_lineage_reuse(
            left_predicate_filter_lineage=left,
            right_predicate_filter_lineage=right,
            error_factory=_error_factory,
        )
        mismatch_issues = [
            i for i in result["issues"] if i["code"] == "metric_default_predicate_mismatch"
        ]
        self.assertEqual(len(mismatch_issues), 0)

    def test_request_scope_divergence_is_warning(self) -> None:
        left = _make_lineage(request_scope_ref="predicate.r1")
        right = _make_lineage(request_scope_ref="predicate.r2")
        result = resolve_predicate_lineage_reuse(
            left_predicate_filter_lineage=left,
            right_predicate_filter_lineage=right,
            error_factory=_error_factory,
        )
        divergence_issues = [i for i in result["issues"] if i["code"] == "scope_divergence"]
        self.assertEqual(len(divergence_issues), 1)
        self.assertIsNone(result["fatal_message"])

    def test_request_scope_one_present_one_absent_is_divergence(self) -> None:
        left = _make_lineage(request_scope_ref="predicate.r1")
        right = _make_lineage()
        result = resolve_predicate_lineage_reuse(
            left_predicate_filter_lineage=left,
            right_predicate_filter_lineage=right,
            error_factory=_error_factory,
        )
        divergence_issues = [i for i in result["issues"] if i["code"] == "scope_divergence"]
        self.assertEqual(len(divergence_issues), 1)


# ---------------------------------------------------------------------------
# Boundary assertion tests (task 5.6)
# ---------------------------------------------------------------------------


class TestPredicateLineageRefsOnly(unittest.TestCase):
    def test_valid_lineage_passes(self) -> None:
        lineage = _make_lineage(default_refs=["predicate.d1"])
        assert_predicate_lineage_refs_only(lineage, surface="test_artifact")

    def test_reuse_summary_passes(self) -> None:
        lineage = _make_lineage(
            default_refs=["predicate.d1"],
            component_lineages=[_make_component_lineage("numerator")],
            component_scopes=[_make_component_scope("numerator", fingerprint="aaa1111bbb2222")],
        )
        result = resolve_predicate_lineage_reuse(
            left_predicate_filter_lineage=lineage,
            right_predicate_filter_lineage=lineage,
            error_factory=_error_factory,
        )
        summary = result["reuse_summary"]
        assert summary is not None
        assert_predicate_lineage_refs_only(summary, surface="compare_resolved_input_summary")

    def test_expression_key_raises(self) -> None:
        lineage = _make_lineage()
        lineage["expression"] = {"op": "eq", "field": "x"}
        with self.assertRaises(RefBoundaryError):
            assert_predicate_lineage_refs_only(lineage, surface="test_artifact")

    def test_sql_key_raises(self) -> None:
        lineage = _make_lineage()
        lineage["sql"] = "WHERE x = 1"
        with self.assertRaises(RefBoundaryError):
            assert_predicate_lineage_refs_only(lineage, surface="test_artifact")

    def test_lowering_template_in_nested_structure_raises(self) -> None:
        lineage = _make_lineage()
        lineage["shared_effective_scope"]["lowering_template"] = "col = :val"
        with self.assertRaises(RefBoundaryError):
            assert_predicate_lineage_refs_only(lineage, surface="test_artifact")

    def test_physical_column_in_list_raises(self) -> None:
        lineage = _make_lineage(
            component_scopes=[{"component_field": "numerator", "physical_column": "user_id"}],
        )
        with self.assertRaises(RefBoundaryError):
            assert_predicate_lineage_refs_only(lineage, surface="test_artifact")

    def test_empty_lineage_passes(self) -> None:
        lineage = _make_lineage()
        assert_predicate_lineage_refs_only(lineage, surface="test_artifact")


if __name__ == "__main__":
    unittest.main()
