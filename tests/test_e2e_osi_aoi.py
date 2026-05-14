"""E2E: semantic model creation and retrieval through generated OSI models."""

from __future__ import annotations

import contextlib

import pytest

from marivo.identity import reset_current_user, set_current_user
from marivo.runtime.semantic.semantic_service import SemanticModelV2Service
from tests.shared_fixtures import ManagedSQLiteMetadataStore, make_temp_metadata_store


@pytest.fixture
def service() -> SemanticModelV2Service:
    store = make_temp_metadata_store(prefix="marivo_osi_aoi_e2e_")
    return SemanticModelV2Service(store)


def _close_service_store(service: SemanticModelV2Service) -> None:
    store = service.store
    if isinstance(store, ManagedSQLiteMetadataStore):
        store.close()


@contextlib.contextmanager
def _as_user(user: str):
    token = set_current_user(user)
    try:
        yield
    finally:
        reset_current_user(token)


def _make_model_payload() -> dict:
    return {
        "name": "test_model",
        "datasets": [
            {
                "name": "orders",
                "source": "test.orders",
                "fields": [
                    {
                        "name": "order_date",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "order_date"}]
                        },
                        "dimension": {"is_time": True},
                    },
                    {
                        "name": "amount",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "amount"}]
                        },
                    },
                    {
                        "name": "region",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "region"}]
                        },
                        "dimension": {"is_time": False},
                    },
                ],
                "custom_extensions": [
                    {"vendor_name": "MARIVO", "data": {"datasource_id": "ds_test"}}
                ],
            }
        ],
        "metrics": [
            {
                "name": "revenue",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(amount)"}]},
                "custom_extensions": [
                    {
                        "vendor_name": "MARIVO",
                        "data": {"additive_dimensions": ["region"], "aggregation_semantics": "sum"},
                    }
                ],
            }
        ],
    }


def test_import_semantic_model_with_generated_osi(service: SemanticModelV2Service) -> None:
    try:
        with _as_user("test_user"):
            result = service.import_osi_semantic_models(
                {"version": "0.1.1", "semantic_model": [_make_model_payload()]}
            )
            imported = service.export_osi_semantic_models("test_model")["semantic_model"][0]

        assert result["valid"] is True
        assert imported["name"] == "test_model"
        assert len(imported["datasets"]) == 1
        assert len(imported.get("metrics", [])) == 1

        metric = imported["metrics"][0]
        marivo_ext = None
        for ext in metric.get("custom_extensions", []):
            if ext.get("vendor_name") == "MARIVO":
                marivo_ext = ext["data"]
                break
        assert marivo_ext is not None
        assert marivo_ext.get("additive_dimensions") == ["region"]
        assert marivo_ext.get("aggregation_semantics") == "sum"
    finally:
        _close_service_store(service)


def test_get_semantic_model_roundtrip(service: SemanticModelV2Service) -> None:
    try:
        with _as_user("test_user"):
            service.import_osi_semantic_models(
                {"version": "0.1.1", "semantic_model": [_make_model_payload()]}
            )
        fetched = service.get_semantic_model("test_model", requesting_user="test_user")
        assert fetched["name"] == "test_model"
        assert len(fetched["datasets"]) == 1
        assert fetched["datasets"][0]["name"] == "orders"
    finally:
        _close_service_store(service)
