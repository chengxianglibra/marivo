"""Tests for dataset-native semantic runtime repository behavior."""

from __future__ import annotations

import json
from typing import Any, cast

from marivo.adapters.metadata import MetadataStore
from marivo.runtime.evidence.semantic_repository import SemanticRuntimeRepository


class _FakeMetadata:
    def __init__(self, *, dataset_ids: list[int]) -> None:
        self.dataset_ids = dataset_ids

    def query_rows(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        if "FROM semantic_metrics m" in sql:
            return [
                {
                    "model_id": 1,
                    "expression": json.dumps(
                        {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(amount)"}]}
                    ),
                    "decomposition_semantics": "sum",
                    "created_at": "",
                    "updated_at": "",
                    "visibility": "public",
                }
            ]
        if "SELECT source, datasource_id FROM semantic_datasets" in sql:
            return [{"source": "analytics.orders", "datasource_id": "ds_001"}]
        if "SELECT dataset_id FROM semantic_datasets" in sql:
            return [{"dataset_id": dataset_id} for dataset_id in self.dataset_ids]
        if "WHERE d.model_id = ?" in sql:
            return [{"name": "order_date"}, {"name": "region"}, {"name": "unrelated_dim"}]
        raise AssertionError(f"Unexpected query: {sql}")


def test_resolve_metric_dimensions_returns_all_declared_dimensions() -> None:
    repo = SemanticRuntimeRepository(cast("MetadataStore", _FakeMetadata(dataset_ids=[10])))

    assert repo.resolve_metric_dimensions("revenue") == ["order_date", "region", "unrelated_dim"]


def test_resolve_metric_dimensions_returns_all_dimensions_for_multi_dataset() -> None:
    repo = SemanticRuntimeRepository(cast("MetadataStore", _FakeMetadata(dataset_ids=[10, 20])))

    assert repo.resolve_metric_dimensions("revenue") == ["order_date", "region", "unrelated_dim"]
