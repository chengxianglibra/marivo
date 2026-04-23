"""Tests for _extract_predicate_filter_lineage helper in observe.py."""

from __future__ import annotations

import unittest

from app.analysis_core.compiler import CompiledQuery
from app.intents.observe import _extract_predicate_filter_lineage


def _make_ir_bundle(nodes: list[dict]) -> dict:
    return {"plan": {"nodes": nodes}}


def _measurement_node(lineage: dict | None = None) -> dict:
    node: dict = {"node_type": "measurement", "node_id": "measurement:0"}
    if lineage is not None:
        node["predicate_filter_lineage"] = lineage
    return node


def _process_node() -> dict:
    return {"node_type": "process", "node_id": "process:0:proc1"}


def _intent_node() -> dict:
    return {"node_type": "intent", "node_id": "intent:0"}


class TestExtractPredicateFilterLineage(unittest.TestCase):
    def test_no_ir_bundle_returns_none(self):
        cq = CompiledQuery(sql="SELECT 1")
        self.assertIsNone(_extract_predicate_filter_lineage(cq))

    def test_ir_bundle_with_no_nodes_returns_none(self):
        cq = CompiledQuery(sql="SELECT 1", ir_bundle=_make_ir_bundle([]))
        self.assertIsNone(_extract_predicate_filter_lineage(cq))

    def test_ir_bundle_with_no_measurement_node_returns_none(self):
        cq = CompiledQuery(
            sql="SELECT 1",
            ir_bundle=_make_ir_bundle([_process_node(), _intent_node()]),
        )
        self.assertIsNone(_extract_predicate_filter_lineage(cq))

    def test_measurement_node_without_lineage_returns_none(self):
        cq = CompiledQuery(
            sql="SELECT 1",
            ir_bundle=_make_ir_bundle([_measurement_node(lineage=None)]),
        )
        self.assertIsNone(_extract_predicate_filter_lineage(cq))

    def test_measurement_node_with_lineage_returns_lineage(self):
        lineage = {
            "shared_effective_scope": {
                "governance_policy_refs": ["predicate.gov1"],
                "carrier_row_filter_refs": [],
            },
            "metric_default_lineage": {"default_predicate_refs": ["predicate.def1"]},
            "component_qualifier_lineages": [],
            "component_effective_scopes": [],
        }
        cq = CompiledQuery(
            sql="SELECT 1",
            ir_bundle=_make_ir_bundle([_measurement_node(lineage=lineage)]),
        )
        result = _extract_predicate_filter_lineage(cq)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result["shared_effective_scope"]["governance_policy_refs"], ["predicate.gov1"]
        )
        self.assertEqual(
            result["metric_default_lineage"]["default_predicate_refs"], ["predicate.def1"]
        )

    def test_multiple_nodes_returns_measurement_lineage(self):
        lineage = {
            "shared_effective_scope": {
                "governance_policy_refs": [],
                "carrier_row_filter_refs": ["predicate.car1"],
            },
            "metric_default_lineage": {"default_predicate_refs": []},
            "component_qualifier_lineages": [],
            "component_effective_scopes": [],
        }
        cq = CompiledQuery(
            sql="SELECT 1",
            ir_bundle=_make_ir_bundle(
                [
                    _process_node(),
                    _measurement_node(lineage=lineage),
                    _intent_node(),
                ]
            ),
        )
        result = _extract_predicate_filter_lineage(cq)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result["shared_effective_scope"]["carrier_row_filter_refs"], ["predicate.car1"]
        )

    def test_ir_bundle_with_none_plan_returns_none(self):
        cq = CompiledQuery(sql="SELECT 1", ir_bundle={})
        self.assertIsNone(_extract_predicate_filter_lineage(cq))

    def test_ir_bundle_with_none_nodes_returns_none(self):
        cq = CompiledQuery(sql="SELECT 1", ir_bundle={"plan": {}})
        self.assertIsNone(_extract_predicate_filter_lineage(cq))


if __name__ == "__main__":
    unittest.main()
