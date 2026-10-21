"""Shared value rules keep capture and execution service dependencies outside core."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "module",
    (
        "value_types",
        "data_validation",
        "frame_expression",
        "frame_structure",
        "frame_data",
        "attribute",
        "artifact_data",
        "frame_source_facts",
    ),
)
def test_shared_value_rules_have_only_named_runtime_leaf_exceptions(module: str) -> None:
    path = Path(__file__).resolve().parents[3] / "marivo" / "analysis" / "core" / f"{module}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    annotation_nodes = {
        id(child)
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and (
            (isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING")
            or (
                isinstance(node.test, ast.Attribute)
                and isinstance(node.test.value, ast.Name)
                and node.test.value.id == "typing"
                and node.test.attr == "TYPE_CHECKING"
            )
        )
        for statement in node.body
        for child in ast.walk(statement)
    }
    service_roots = (
        "marivo.analysis.compiler",
        "marivo.analysis.dsl",
        "marivo.analysis.materialization",
        "marivo.analysis.methods",
        "marivo.analysis.session",
        "marivo.datasource.runtime",
        "marivo.semantic",
    )
    exceptions = {
        ("data_validation", "marivo.analysis.materialization.errors.MaterializationError"),
        ("value_types", "marivo.semantic._numeric_lowering.numeric_failure"),
        ("value_types", "marivo.semantic._numeric_lowering.NumericValue"),
        ("value_types", "marivo.semantic._numeric_lowering.numeric_binary"),
        ("artifact_data", "marivo.analysis.materialization.contracts.LocalReceipt"),
        ("frame_source_facts", "marivo.analysis.materialization.contracts.canonical_json"),
    }
    for node in ast.walk(tree):
        if id(node) in annotation_nodes or not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for alias in node.names:
            target = (
                f"{node.module}.{alias.name}" if isinstance(node, ast.ImportFrom) else alias.name
            )
            if any(target == root or target.startswith(f"{root}.") for root in service_roots):
                assert (module, target) in exceptions, f"{path.name}:{node.lineno}: {target}"
