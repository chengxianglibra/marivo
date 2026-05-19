from __future__ import annotations

import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from typing import Any

from marivo.adapters.metadata import MetadataTransaction
from marivo.contracts.errors import ErrorCode
from marivo.contracts.errors import ValidationError as DomainValidationError
from marivo.identity import reset_current_user, set_current_user
from marivo.runtime.semantic.import_export import (
    DatasetBinding,
    DatasourceBinder,
    DatasourceBindingReport,
    ImportModelReport,
    ImportOsiDocumentReport,
    SemanticImportPlan,
    SemanticImportPlanner,
)
from marivo.runtime.semantic.semantic_service import SemanticModelV2Service
from tests.shared_fixtures import ManagedSQLiteMetadataStore, make_temp_metadata_store


def _field(
    name: str,
    expression: str | None = None,
    *,
    is_time: bool = False,
    support_min_granularity: str | None = None,
    data_type: str | None = None,
) -> dict[str, object]:
    field: dict[str, object] = {
        "name": name,
        "expression": {
            "dialects": [
                {"dialect": "ANSI_SQL", "expression": expression or name},
            ],
        },
    }
    if is_time:
        field["dimension"] = {"is_time": True}
        field["custom_extensions"] = [
            {
                "vendor_name": "MARIVO",
                "data": {
                    "support_min_granularity": support_min_granularity or "day",
                    "data_type": data_type or "date",
                },
            }
        ]
    return field


def _dataset(
    name: str = "orders",
    *,
    source: str = "analytics.orders",
    description: str | None = None,
    primary_key: list[str] | None = None,
    fields: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    dataset: dict[str, object] = {
        "name": name,
        "source": source,
        "custom_extensions": [
            {
                "vendor_name": "MARIVO",
                "data": {"datasource_id": "ds_001"},
            }
        ],
        "fields": fields if fields is not None else [_field("order_id")],
    }
    if description is not None:
        dataset["description"] = description
    if primary_key is not None:
        dataset["primary_key"] = primary_key
    return dataset


def _metric(
    name: str = "revenue",
    expression: str = "SUM(amount)",
) -> dict[str, object]:
    return {
        "name": name,
        "expression": {
            "dialects": [
                {"dialect": "ANSI_SQL", "expression": expression},
            ],
        },
    }


def _relationship(name: str = "orders_to_customers") -> dict[str, object]:
    return {
        "name": name,
        "from": "orders",
        "to": "customers",
        "from_columns": ["customer_id"],
        "to_columns": ["customer_id"],
    }


def _doc(
    model_name: str = "commerce",
    *,
    description: str | None = "Commerce model",
    datasets: list[dict[str, object]] | None = None,
    metrics: list[dict[str, object]] | None = None,
    relationships: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    model: dict[str, object] = {
        "name": model_name,
        "datasets": datasets if datasets is not None else [_dataset()],
    }
    if description is not None:
        model["description"] = description
    if metrics is not None:
        model["metrics"] = metrics
    if relationships is not None:
        model["relationships"] = relationships
    return {"version": "0.1.1", "semantic_model": [model]}


class SemanticImportExportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store: ManagedSQLiteMetadataStore = make_temp_metadata_store(
            prefix="marivo_import_export_service_"
        )
        self.service = SemanticModelV2Service(self.store)

    def tearDown(self) -> None:
        self.store.close()

    def test_import_creates_current_users_private_working_copy(self) -> None:
        token = set_current_user("alice")
        try:
            self.service.import_osi_semantic_models(_doc())
            row = self.store.query_one(
                "SELECT visibility, owner_user FROM semantic_models WHERE name = ?",
                ["commerce"],
            )
            self.assertEqual(row, {"visibility": "private", "owner_user": "alice"})
            exported = self.service.export_osi_semantic_models("commerce")
            model = exported["semantic_model"][0]
            self.assertEqual(model["name"], "commerce")
            self.assertEqual(len(model["datasets"][0]["fields"]), 1)
        finally:
            reset_current_user(token)

    def test_import_replaces_whole_model_graph(
        self,
    ) -> None:
        token = set_current_user("alice")
        try:
            self.service.import_osi_semantic_models(
                _doc(
                    description="original",
                    datasets=[
                        _dataset(
                            "orders",
                            fields=[_field("order_id"), _field("customer_id"), _field("amount")],
                        ),
                        _dataset("customers", fields=[_field("customer_id")]),
                    ],
                    metrics=[_metric("revenue", "SUM(amount)")],
                    relationships=[_relationship()],
                )
            )
            self.service.import_osi_semantic_models(
                _doc(
                    description="updated",
                    datasets=[
                        _dataset(
                            "orders",
                            source="analytics.orders_v2",
                            fields=[_field("amount", "gross_amount"), _field("created_at")],
                        ),
                    ],
                    metrics=[_metric("revenue", "SUM(gross_amount)")],
                    relationships=[],
                )
            )
            exported = self.service.export_osi_semantic_models("commerce")
        finally:
            reset_current_user(token)

        model = exported["semantic_model"][0]
        self.assertEqual(model["description"], "updated")
        datasets = {dataset["name"]: dataset for dataset in model["datasets"]}
        self.assertEqual(datasets["orders"]["source"], "analytics.orders_v2")
        self.assertNotIn("customers", datasets)
        self.assertEqual(len(datasets["orders"]["fields"]), 2)
        self.assertEqual(
            [field["name"] for field in datasets["orders"]["fields"]],
            ["amount", "created_at"],
        )
        self.assertEqual(
            model["metrics"][0]["expression"]["dialects"][0]["expression"],
            "SUM(gross_amount)",
        )
        self.assertNotIn("relationships", model)

    def test_export_without_name_returns_only_current_users_private_models(self) -> None:
        token = set_current_user("alice")
        try:
            self.service.import_osi_semantic_models(_doc("alice_model"))
        finally:
            reset_current_user(token)

        token = set_current_user("bob")
        try:
            self.service.import_osi_semantic_models(_doc("bob_model"))
        finally:
            reset_current_user(token)

        token = set_current_user("alice")
        try:
            exported = self.service.export_osi_semantic_models()
        finally:
            reset_current_user(token)

        self.assertEqual(
            [model["name"] for model in exported["semantic_model"]],
            ["alice_model"],
        )

    def test_import_replaces_existing_model_description_when_omitted(self) -> None:
        token = set_current_user("alice")
        try:
            self.service.import_osi_semantic_models(_doc(description="Retained model description"))
            self.service.import_osi_semantic_models(
                _doc(
                    description=None,
                    datasets=[
                        _dataset(
                            "orders",
                            source="analytics.orders_v2",
                            fields=[_field("order_id"), _field("amount")],
                        )
                    ],
                )
            )
            exported = self.service.export_osi_semantic_models("commerce")
        finally:
            reset_current_user(token)

        model = exported["semantic_model"][0]
        self.assertNotIn("description", model)
        self.assertEqual(model["datasets"][0]["source"], "analytics.orders_v2")

    def test_import_replaces_existing_dataset_optional_attributes_when_omitted(self) -> None:
        token = set_current_user("alice")
        try:
            self.service.import_osi_semantic_models(
                _doc(
                    datasets=[
                        _dataset(
                            "orders",
                            description="Retained dataset description",
                            primary_key=["order_id"],
                            fields=[_field("order_id")],
                        )
                    ]
                )
            )
            self.service.import_osi_semantic_models(
                _doc(
                    datasets=[
                        _dataset(
                            "orders",
                            source="analytics.orders_v2",
                            fields=[_field("order_id"), _field("amount")],
                        )
                    ]
                )
            )
            exported = self.service.export_osi_semantic_models("commerce")
        finally:
            reset_current_user(token)

        dataset = exported["semantic_model"][0]["datasets"][0]
        self.assertNotIn("description", dataset)
        self.assertNotIn("primary_key", dataset)
        self.assertEqual(dataset["source"], "analytics.orders_v2")
        self.assertEqual(
            dataset["custom_extensions"][0]["data"]["datasource_id"],
            "ds_001",
        )

    def test_import_export_round_trips_time_field_support_min_granularity(self) -> None:
        token = set_current_user("alice")
        try:
            self.service.import_osi_semantic_models(
                _doc(
                    datasets=[
                        _dataset(
                            "orders",
                            fields=[
                                _field("order_id"),
                                _field(
                                    "order_time",
                                    "order_time",
                                    is_time=True,
                                    support_min_granularity="hour",
                                    data_type="timestamp",
                                ),
                            ],
                        )
                    ]
                )
            )
            exported = self.service.export_osi_semantic_models("commerce")
        finally:
            reset_current_user(token)

        fields = exported["semantic_model"][0]["datasets"][0]["fields"]
        time_field = next(field for field in fields if field["name"] == "order_time")
        self.assertEqual(
            time_field["custom_extensions"][0]["data"]["support_min_granularity"],
            "hour",
        )

    def test_import_rolls_back_existing_model_when_mid_replace_field_insert_fails(self) -> None:
        token = set_current_user("alice")
        try:
            self.service.import_osi_semantic_models(
                _doc(
                    description="original",
                    datasets=[
                        _dataset(
                            "orders",
                            description="original dataset",
                            fields=[_field("order_id")],
                        )
                    ],
                )
            )
            failing_service = SemanticModelV2Service(
                _FailingFieldInsertStore(
                    self.store,
                    fail_after_field_inserts=1,
                )
            )
            with self.assertRaises(RuntimeError):
                failing_service.import_osi_semantic_models(
                    _doc(
                        description="mutated",
                        datasets=[
                            _dataset(
                                "orders",
                                source="analytics.orders_v2",
                                description="mutated dataset",
                                fields=[_field("order_id", "order_id_v2"), _field("amount")],
                            )
                        ],
                    )
                )
            exported = self.service.export_osi_semantic_models("commerce")
        finally:
            reset_current_user(token)

        model = exported["semantic_model"][0]
        dataset = model["datasets"][0]
        self.assertEqual(model["description"], "original")
        self.assertEqual(dataset["source"], "analytics.orders")
        self.assertEqual(dataset["description"], "original dataset")
        self.assertEqual(len(dataset["fields"]), 1)
        self.assertEqual(dataset["fields"][0]["name"], "order_id")
        self.assertEqual(
            dataset["fields"][0]["expression"]["dialects"][0]["expression"],
            "order_id",
        )

    def test_export_named_missing_private_returns_not_found_with_missing_name(self) -> None:
        token = set_current_user("alice")
        try:
            with self.assertRaises(Exception) as raised:
                self.service.export_osi_semantic_models("missing_model")
        finally:
            reset_current_user(token)

        self.assertEqual(raised.exception.code, ErrorCode.NOT_FOUND_SEMANTIC_MODEL)
        self.assertIn("missing_model", raised.exception.message)

    def test_import_requires_transport_injected_user(self) -> None:
        token = set_current_user(None)
        try:
            with self.assertRaises(RuntimeError):
                self.service.import_osi_semantic_models(_doc())
        finally:
            reset_current_user(token)

    def test_export_requires_transport_injected_user(self) -> None:
        token = set_current_user(None)
        try:
            with self.assertRaises(RuntimeError):
                self.service.export_osi_semantic_models()
        finally:
            reset_current_user(token)


class ImportExportContractTests(unittest.TestCase):
    def test_import_report_model_counts_and_bindings(self) -> None:
        report = ImportOsiDocumentReport(
            models=[
                {
                    "name": "sales",
                    "created": True,
                    "updated": False,
                    "datasets": {"created": 1, "updated": 0, "unchanged": 0},
                    "fields": {"created": 2, "updated": 0, "unchanged": 0},
                    "metrics": {"created": 0, "updated": 0, "unchanged": 0},
                    "relationships": {"created": 0, "updated": 0, "unchanged": 0},
                    "datasource_bindings": [
                        {
                            "dataset": "orders",
                            "datasource_id": "ds_001",
                            "selection": "first_accessible_candidate",
                        }
                    ],
                }
            ],
            errors=[],
        )

        dumped = report.model_dump()
        self.assertEqual(dumped["models"][0]["name"], "sales")
        self.assertEqual(dumped["models"][0]["fields"]["created"], 2)
        self.assertEqual(
            dumped["models"][0]["datasource_bindings"][0]["selection"],
            "first_accessible_candidate",
        )

    def test_import_model_report_defaults_counts_and_bindings(self) -> None:
        report = ImportModelReport(name="sales", created=True, updated=False)

        dumped = report.model_dump()
        self.assertEqual(dumped["datasets"], {"created": 0, "updated": 0, "unchanged": 0})
        self.assertEqual(dumped["fields"], {"created": 0, "updated": 0, "unchanged": 0})
        self.assertEqual(dumped["metrics"], {"created": 0, "updated": 0, "unchanged": 0})
        self.assertEqual(dumped["relationships"], {"created": 0, "updated": 0, "unchanged": 0})
        self.assertEqual(report.datasource_bindings, [])

    def test_datasource_binding_report_defaults_selection(self) -> None:
        report = DatasourceBindingReport(dataset="orders", datasource_id="ds_a")

        self.assertEqual(report.selection, "first_accessible_candidate")

    def test_import_document_report_defaults_errors(self) -> None:
        report = ImportOsiDocumentReport(models=[])

        self.assertEqual(report.errors, [])

    def test_semantic_import_plan_defaults_bindings(self) -> None:
        plan = SemanticImportPlan(document={})

        self.assertEqual(plan.bindings, [])

    def test_semantic_import_plan_is_frozen(self) -> None:
        plan = SemanticImportPlan(document={})

        with self.assertRaises(FrozenInstanceError):
            plan.document = {"version": "0.1.1"}

    def test_dataset_binding_contract_fields(self) -> None:
        binding = DatasetBinding(
            model_name="sales",
            dataset_name="orders",
            datasource_id="ds_a",
        )

        self.assertEqual(binding.model_name, "sales")
        self.assertEqual(binding.dataset_name, "orders")
        self.assertEqual(binding.datasource_id, "ds_a")
        self.assertEqual(binding.selection, "first_accessible_candidate")

    def test_dataset_binding_is_frozen(self) -> None:
        binding = DatasetBinding(
            model_name="sales",
            dataset_name="orders",
            datasource_id="ds_a",
        )

        with self.assertRaises(FrozenInstanceError):
            binding.model_name = "marketing"

    def test_empty_document_is_validation_error(self) -> None:
        planner = SemanticImportPlanner()

        with self.assertRaises(DomainValidationError) as raised:
            planner.preflight({"version": "0.1.1", "semantic_model": []})

        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION)
        self.assertIn("semantic_model", raised.exception.message)

    def test_duplicate_dataset_names_are_validation_error(self) -> None:
        planner = SemanticImportPlanner()
        doc = {
            "version": "0.1.1",
            "semantic_model": [
                {
                    "name": "sales",
                    "datasets": [
                        {"name": "orders", "source": "analytics.orders"},
                        {"name": "orders", "source": "analytics.orders_v2"},
                    ],
                }
            ],
        }

        with self.assertRaises(DomainValidationError) as raised:
            planner.preflight(doc)

        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION)
        self.assertIn("duplicate dataset name", raised.exception.message)

    def test_duplicate_dataset_names_are_compared_after_strip(self) -> None:
        planner = SemanticImportPlanner()
        doc = {
            "version": "0.1.1",
            "semantic_model": [
                {
                    "name": "sales",
                    "datasets": [
                        {"name": "orders", "source": "analytics.orders"},
                        {"name": " orders ", "source": "analytics.orders_v2"},
                    ],
                }
            ],
        }

        with self.assertRaises(DomainValidationError) as raised:
            planner.preflight(doc)

        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION)
        self.assertIn("duplicate dataset name 'orders'", raised.exception.message)
        self.assertEqual(raised.exception.detail["name"], "orders")

    def test_duplicate_semantic_model_names_are_validation_error(self) -> None:
        planner = SemanticImportPlanner()
        doc = {
            "version": "0.1.1",
            "semantic_model": [
                {"name": "sales"},
                {"name": "sales"},
            ],
        }

        with self.assertRaises(DomainValidationError) as raised:
            planner.preflight(doc)

        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION)
        self.assertIn("duplicate semantic model name", raised.exception.message)

    def test_duplicate_dataset_field_names_are_validation_error(self) -> None:
        planner = SemanticImportPlanner()
        doc = {
            "version": "0.1.1",
            "semantic_model": [
                {
                    "name": "sales",
                    "datasets": [
                        {
                            "name": "orders",
                            "fields": [
                                {"name": "amount", "expression": "amount"},
                                {"name": "amount", "expression": "gross_amount"},
                            ],
                        }
                    ],
                }
            ],
        }

        with self.assertRaises(DomainValidationError) as raised:
            planner.preflight(doc)

        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION)
        self.assertIn("duplicate field name", raised.exception.message)

    def test_duplicate_metric_names_are_validation_error(self) -> None:
        planner = SemanticImportPlanner()
        doc = {
            "version": "0.1.1",
            "semantic_model": [
                {
                    "name": "sales",
                    "metrics": [
                        {"name": "revenue", "measure": "amount"},
                        {"name": "revenue", "measure": "gross_amount"},
                    ],
                }
            ],
        }

        with self.assertRaises(DomainValidationError) as raised:
            planner.preflight(doc)

        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION)
        self.assertIn("duplicate metric name", raised.exception.message)

    def test_duplicate_relationship_names_are_validation_error(self) -> None:
        planner = SemanticImportPlanner()
        doc = {
            "version": "0.1.1",
            "semantic_model": [
                {
                    "name": "sales",
                    "relationships": [
                        {"name": "orders_to_users"},
                        {"name": "orders_to_users"},
                    ],
                }
            ],
        }

        with self.assertRaises(DomainValidationError) as raised:
            planner.preflight(doc)

        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION)
        self.assertIn("duplicate relationship name", raised.exception.message)


class _FakeDatasourceService:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.browse_calls: list[tuple[str, str, str]] = []

    def list_datasources(self) -> list[dict[str, object]]:
        return list(self.rows)

    def browse_catalog_columns(
        self, datasource_id: str, schema_name: str, table_name: str
    ) -> list[dict[str, object]]:
        self.browse_calls.append((datasource_id, schema_name, table_name))
        for row in self.rows:
            if row["datasource_id"] == datasource_id and row.get("has_table"):
                return [{"name": "id", "type": "integer"}]
        raise KeyError(table_name)


class _AccessDeniedDatasourceService(_FakeDatasourceService):
    def browse_catalog_columns(
        self, datasource_id: str, schema_name: str, table_name: str
    ) -> list[dict[str, object]]:
        self.browse_calls.append((datasource_id, schema_name, table_name))
        raise ValueError("access denied")


class _FailingFieldInsertStore:
    def __init__(self, inner: ManagedSQLiteMetadataStore, *, fail_after_field_inserts: int) -> None:
        self.inner = inner
        self.fail_after_field_inserts = fail_after_field_inserts
        self.field_inserts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    @contextmanager
    def transaction(self) -> Iterator[_FailingFieldInsertTransaction]:
        with self.inner.transaction() as txn:
            yield _FailingFieldInsertTransaction(self, txn)


class _FailingFieldInsertTransaction:
    def __init__(
        self,
        store: _FailingFieldInsertStore,
        inner: MetadataTransaction,
    ) -> None:
        self.store = store
        self.inner = inner

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        if "INSERT INTO semantic_fields" in sql:
            self.store.field_inserts += 1
            if self.store.field_inserts > self.store.fail_after_field_inserts:
                raise RuntimeError("forced semantic field insert failure")
        self.inner.execute(sql, params)

    def execute_many(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        self.inner.execute_many(sql, rows)

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        return self.inner.query_rows(sql, params)

    def query_one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        return self.inner.query_one(sql, params)


class DatasourceBinderTests(unittest.TestCase):
    def test_selects_first_accessible_candidate_by_stable_order(self) -> None:
        service = _FakeDatasourceService(
            [
                {
                    "datasource_id": "ds_b",
                    "display_name": "warehouse_b",
                    "status": "active",
                    "has_table": True,
                },
                {
                    "datasource_id": "ds_a",
                    "display_name": "warehouse_a",
                    "status": "active",
                    "has_table": True,
                },
            ]
        )
        binder = DatasourceBinder(service)

        binding = binder.bind_dataset(
            model_name="sales",
            dataset={"name": "orders", "source": "analytics.orders"},
        )

        self.assertEqual(binding.datasource_id, "ds_a")
        self.assertEqual(service.browse_calls, [("ds_a", "analytics", "orders")])

    def test_display_name_takes_precedence_over_name_for_stable_order(self) -> None:
        service = _FakeDatasourceService(
            [
                {
                    "datasource_id": "ds_a",
                    "display_name": "warehouse_b",
                    "name": "aaa_source_name",
                    "status": "active",
                    "has_table": True,
                },
                {
                    "datasource_id": "ds_b",
                    "display_name": "warehouse_a",
                    "name": "zzz_source_name",
                    "status": "active",
                    "has_table": True,
                },
            ]
        )
        binder = DatasourceBinder(service)

        binding = binder.bind_dataset(
            model_name="sales",
            dataset={"name": "orders", "source": "analytics.orders"},
        )

        self.assertEqual(binding.datasource_id, "ds_b")
        self.assertEqual(service.browse_calls, [("ds_b", "analytics", "orders")])

    def test_name_is_stable_order_fallback_when_display_name_missing(self) -> None:
        service = _FakeDatasourceService(
            [
                {
                    "datasource_id": "ds_b",
                    "name": "warehouse_b",
                    "status": "active",
                    "has_table": True,
                },
                {
                    "datasource_id": "ds_a",
                    "name": "warehouse_a",
                    "status": "active",
                    "has_table": True,
                },
            ]
        )
        binder = DatasourceBinder(service)

        binding = binder.bind_dataset(
            model_name="sales",
            dataset={"name": "orders", "source": "analytics.orders"},
        )

        self.assertEqual(binding.datasource_id, "ds_a")
        self.assertEqual(service.browse_calls, [("ds_a", "analytics", "orders")])

    def test_inactive_datasources_are_skipped(self) -> None:
        service = _FakeDatasourceService(
            [
                {
                    "datasource_id": "ds_inactive",
                    "display_name": "warehouse_a",
                    "status": "inactive",
                    "has_table": True,
                },
                {
                    "datasource_id": "ds_active",
                    "display_name": "warehouse_b",
                    "status": "active",
                    "has_table": True,
                },
            ]
        )
        binder = DatasourceBinder(service)

        binding = binder.bind_dataset(
            model_name="sales",
            dataset={"name": "orders", "source": "analytics.orders"},
        )

        self.assertEqual(binding.datasource_id, "ds_active")
        self.assertEqual(service.browse_calls, [("ds_active", "analytics", "orders")])

    def test_missing_status_defaults_to_active(self) -> None:
        service = _FakeDatasourceService(
            [
                {
                    "datasource_id": "ds_default_active",
                    "display_name": "warehouse_a",
                    "has_table": True,
                },
                {
                    "datasource_id": "ds_active",
                    "display_name": "warehouse_b",
                    "status": "active",
                    "has_table": True,
                },
            ]
        )
        binder = DatasourceBinder(service)

        binding = binder.bind_dataset(
            model_name="sales",
            dataset={"name": "orders", "source": "analytics.orders"},
        )

        self.assertEqual(binding.datasource_id, "ds_default_active")
        self.assertEqual(service.browse_calls, [("ds_default_active", "analytics", "orders")])

    def test_first_candidate_without_table_is_skipped(self) -> None:
        service = _FakeDatasourceService(
            [
                {
                    "datasource_id": "ds_without_table",
                    "display_name": "warehouse_a",
                    "status": "active",
                    "has_table": False,
                },
                {
                    "datasource_id": "ds_with_table",
                    "display_name": "warehouse_b",
                    "status": "active",
                    "has_table": True,
                },
            ]
        )
        binder = DatasourceBinder(service)

        binding = binder.bind_dataset(
            model_name="sales",
            dataset={"name": "orders", "source": "analytics.orders"},
        )

        self.assertEqual(binding.datasource_id, "ds_with_table")
        self.assertEqual(
            service.browse_calls,
            [
                ("ds_without_table", "analytics", "orders"),
                ("ds_with_table", "analytics", "orders"),
            ],
        )

    def test_binding_failure_when_no_candidate_matches(self) -> None:
        service = _FakeDatasourceService(
            [
                {
                    "datasource_id": "ds_a",
                    "name": "warehouse_a",
                    "status": "active",
                    "has_table": False,
                }
            ]
        )
        binder = DatasourceBinder(service)

        with self.assertRaises(DomainValidationError) as raised:
            binder.bind_dataset(
                model_name="sales",
                dataset={"name": "orders", "source": "analytics.orders"},
            )

        self.assertEqual(raised.exception.code, ErrorCode.DATASOURCE_BINDING_FAILED)
        self.assertIn("orders", raised.exception.message)

    def test_binding_cache_avoids_repeated_catalog_checks(self) -> None:
        service = _FakeDatasourceService(
            [
                {
                    "datasource_id": "ds_a",
                    "name": "warehouse_a",
                    "status": "active",
                    "has_table": True,
                }
            ]
        )
        binder = DatasourceBinder(service)

        binder.bind_dataset(
            model_name="sales",
            dataset={"name": "orders", "source": "analytics.orders"},
        )
        binder.bind_dataset(
            model_name="sales",
            dataset={"name": "orders", "source": "analytics.orders"},
        )

        self.assertEqual(service.browse_calls, [("ds_a", "analytics", "orders")])

    def test_invalid_fqn_is_binding_failure(self) -> None:
        service = _FakeDatasourceService(
            [
                {
                    "datasource_id": "ds_a",
                    "name": "warehouse_a",
                    "status": "active",
                    "has_table": True,
                }
            ]
        )
        binder = DatasourceBinder(service)

        for source in ["schema..table", ".schema.table", "schema.table."]:
            with self.subTest(source=source):
                with self.assertRaises(DomainValidationError) as raised:
                    binder.bind_dataset(
                        model_name="sales",
                        dataset={"name": "orders", "source": source},
                    )

                self.assertEqual(raised.exception.code, ErrorCode.DATASOURCE_BINDING_FAILED)

        self.assertEqual(service.browse_calls, [])

    def test_access_denied_from_catalog_check_is_validation_error(self) -> None:
        service = _AccessDeniedDatasourceService(
            [
                {
                    "datasource_id": "ds_a",
                    "name": "warehouse_a",
                    "status": "active",
                    "has_table": True,
                }
            ]
        )
        binder = DatasourceBinder(service)

        with self.assertRaises(DomainValidationError) as raised:
            binder.bind_dataset(
                model_name="sales",
                dataset={"name": "orders", "source": "analytics.orders"},
            )

        self.assertEqual(raised.exception.code, ErrorCode.DATASET_ACCESS_DENIED)
        self.assertIn("access denied", raised.exception.message)
