"""Tests for predicate lowering responsibility boundary enforcement."""

from __future__ import annotations

import unittest

from app.analysis_core.predicate_lowering_boundary import (
    assert_predicate_uses_no_physical_names,
)
from app.evidence_engine.ref_boundary import RefBoundaryError


class TestAssertPredicateUsesNoPhysicalNames(unittest.TestCase):
    """Tests for assert_predicate_uses_no_physical_names()."""

    def test_clean_data_passes(self) -> None:
        data = {
            "shared_scope_atoms": [
                {
                    "target_ref": "dimension.country",
                    "op": "eq",
                    "value": "CN",
                    "source_ref": "predicate.test",
                }
            ],
            "component_inputs": [],
        }
        assert_predicate_uses_no_physical_names(data, surface="test")

    def test_physical_name_raises(self) -> None:
        data = {"physical_name": "country_code"}
        with self.assertRaises(RefBoundaryError):
            assert_predicate_uses_no_physical_names(data, surface="test")

    def test_physical_column_raises(self) -> None:
        data = {"physical_column": "country_code"}
        with self.assertRaises(RefBoundaryError):
            assert_predicate_uses_no_physical_names(data, surface="test")

    def test_column_name_raises(self) -> None:
        data = {"column_name": "country_code"}
        with self.assertRaises(RefBoundaryError):
            assert_predicate_uses_no_physical_names(data, surface="test")

    def test_sql_expression_raises(self) -> None:
        data = {"sql_expression": "country_code = 'CN'"}
        with self.assertRaises(RefBoundaryError):
            assert_predicate_uses_no_physical_names(data, surface="test")

    def test_lowering_template_raises(self) -> None:
        data = {"lowering_template": "{col} = {val}"}
        with self.assertRaises(RefBoundaryError):
            assert_predicate_uses_no_physical_names(data, surface="test")

    def test_sql_key_raises(self) -> None:
        data = {"sql": "WHERE country = 'CN'"}
        with self.assertRaises(RefBoundaryError):
            assert_predicate_uses_no_physical_names(data, surface="test")

    def test_nested_forbidden_key_raises(self) -> None:
        data = {
            "component_inputs": [
                {
                    "component_field": "numerator",
                    "shared_scope_atoms": [
                        {
                            "target_ref": "dimension.country",
                            "op": "eq",
                            "value": "CN",
                            "sql": "country = 'CN'",
                        }
                    ],
                }
            ]
        }
        with self.assertRaises(RefBoundaryError):
            assert_predicate_uses_no_physical_names(data, surface="test")

    def test_normalized_predicate_input_passes(self) -> None:
        data = {
            "shared_scope_atoms": [
                {
                    "target_ref": "dimension.country",
                    "op": "eq",
                    "value": "CN",
                    "source_ref": "predicate.test",
                    "source_layer": "governance_policy",
                }
            ],
            "shared_scope_refs": ["predicate.test"],
            "default_atoms": [],
            "default_refs": [],
            "component_inputs": [
                {
                    "component_field": "numerator",
                    "shared_scope_atoms": [],
                    "default_atoms": [],
                    "qualifier_atoms": [
                        {
                            "target_ref": "dimension.status",
                            "op": "eq",
                            "value": "active",
                            "source_ref": "predicate.numerator_qualifier",
                            "source_layer": "component_qualifier",
                            "component_field": "numerator",
                        }
                    ],
                    "effective_scope_refs": ["predicate.test", "predicate.numerator_qualifier"],
                    "scope_fingerprint": "abcd1234efgh5678",
                }
            ],
        }
        assert_predicate_uses_no_physical_names(data, surface="normalized_predicate_input")

    def test_empty_data_passes(self) -> None:
        assert_predicate_uses_no_physical_names({}, surface="test")

    def test_list_data_passes(self) -> None:
        assert_predicate_uses_no_physical_names([], surface="test")

    def test_list_with_forbidden_key_raises(self) -> None:
        data = [{"physical_name": "col"}]
        with self.assertRaises(RefBoundaryError):
            assert_predicate_uses_no_physical_names(data, surface="test")

    def test_allowed_target_ref_passes(self) -> None:
        data = {"target_ref": "dimension.country", "op": "eq", "value": "CN"}
        assert_predicate_uses_no_physical_names(data, surface="test")

    def test_tuple_with_forbidden_key_raises(self) -> None:
        data = ({"physical_name": "col"},)
        with self.assertRaises(RefBoundaryError):
            assert_predicate_uses_no_physical_names(data, surface="test")

    def test_tuple_of_clean_dicts_passes(self) -> None:
        data = ({"target_ref": "dimension.country", "op": "eq", "value": "CN"},)
        assert_predicate_uses_no_physical_names(data, surface="test")

    def test_nested_tuple_with_forbidden_key_raises(self) -> None:
        data = {"items": ({"sql": "WHERE 1=1"},)}
        with self.assertRaises(RefBoundaryError):
            assert_predicate_uses_no_physical_names(data, surface="test")


if __name__ == "__main__":
    unittest.main()
