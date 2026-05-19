from __future__ import annotations

from typing import Any

from marivo.runtime.semantic.import_export import OsiSemanticDocumentValidator


def _valid_doc() -> dict:
    return {
        "version": "0.1.1",
        "semantic_model": [
            {
                "name": "commerce",
                "datasets": [
                    {
                        "name": "orders",
                        "source": "analytics.orders",
                        "primary_key": ["order_id"],
                        "custom_extensions": [
                            {"vendor_name": "MARIVO", "data": {"datasource_id": "ds_001"}}
                        ],
                        "fields": [
                            {
                                "name": "order_id",
                                "expression": {
                                    "dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]
                                },
                            },
                            {
                                "name": "order_time",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "order_time"}
                                    ]
                                },
                                "dimension": {"is_time": True},
                                "custom_extensions": [
                                    {
                                        "vendor_name": "MARIVO",
                                        "data": {
                                            "support_min_granularity": "hour",
                                            "data_type": "timestamp",
                                        },
                                    }
                                ],
                            },
                            {
                                "name": "amount",
                                "expression": {
                                    "dialects": [{"dialect": "ANSI_SQL", "expression": "amount"}]
                                },
                            },
                        ],
                    }
                ],
                "metrics": [
                    {
                        "name": "revenue",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(amount)"}]
                        },
                        "custom_extensions": [
                            {
                                "vendor_name": "MARIVO",
                                "data": {
                                    "additive_dimensions": ["order_id"],
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }


class _FakeEngine:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.queries: list[str] = []

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict]:
        _ = params
        self.queries.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise ValueError(f"dry-run failed for {self.fail_on}")
        return []


class _FakeDatasourceService:
    def __init__(
        self,
        *,
        columns: list[str] | None = None,
        fail: bool = False,
        engine: _FakeEngine | None = None,
        engines_by_datasource: dict[str, _FakeEngine] | None = None,
    ) -> None:
        self.columns = columns or ["order_id", "order_time", "amount", "customer_id"]
        self.columns_by_table = {
            ("analytics", "orders"): self.columns,
            ("analytics", "customers"): ["customer_id", "segment"],
        }
        self.fail = fail
        self.engine = engine or _FakeEngine()
        self.engines_by_datasource = engines_by_datasource or {"ds_001": self.engine}

    def get_datasource(self, datasource_id: str) -> dict:
        if datasource_id not in {"ds_001", "ds_002"} or self.fail:
            raise KeyError(datasource_id)
        return {
            "datasource_id": datasource_id,
            "status": "active",
            "readiness_status": "ready",
            "datasource_type": "duckdb",
        }

    def browse_catalog_columns(
        self, datasource_id: str, schema_name: str, table_name: str
    ) -> list[dict]:
        if datasource_id not in {"ds_001", "ds_002"}:
            raise KeyError((datasource_id, schema_name, table_name))
        columns = self.columns_by_table.get((schema_name, table_name))
        if columns is None:
            raise KeyError((datasource_id, schema_name, table_name))
        return [{"name": column} for column in columns]

    def build_analytics_engine(self, datasource_id: str) -> _FakeEngine:
        if datasource_id not in {"ds_001", "ds_002"}:
            raise KeyError(datasource_id)
        return self.engines_by_datasource.setdefault(datasource_id, _FakeEngine())


def test_validate_valid_document_returns_summary() -> None:
    result = OsiSemanticDocumentValidator().validate(_valid_doc())

    assert result.valid is True
    assert result.schema_version == "0.1.1"
    assert result.errors == []
    assert result.summary == {
        "models": 1,
        "datasets": 1,
        "fields": 3,
        "metrics": 1,
        "relationships": 0,
    }


def test_validate_empty_document_returns_structured_error() -> None:
    result = OsiSemanticDocumentValidator().validate({"version": "0.1.1", "semantic_model": []})

    assert result.valid is False
    assert result.errors[0].code == "EMPTY_SEMANTIC_MODEL"
    assert result.errors[0].json_pointer == "/semantic_model"


def test_validate_schema_failure_returns_structured_error() -> None:
    result = OsiSemanticDocumentValidator().validate({"version": "0.1.1"})

    assert result.valid is False
    assert result.errors[0].code == "SCHEMA_VALIDATION_FAILED"
    assert result.schema_version == "0.1.1"


def test_validate_time_field_requires_support_min_granularity_extension() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["datasets"][0]["fields"][1].pop("custom_extensions")

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert result.errors[0].code == "MISSING_TIME_FIELD_EXTENSION"
    assert (
        result.errors[0].json_pointer == "/semantic_model/0/datasets/0/fields/1/custom_extensions"
    )


def test_validate_non_time_field_rejects_field_extension() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["datasets"][0]["fields"][0]["custom_extensions"] = [
        {"vendor_name": "MARIVO", "data": {"support_min_granularity": "day"}}
    ]

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert result.errors[0].code == "FIELD_EXTENSION_NOT_ALLOWED"


def test_validate_duplicate_dataset_names_returns_json_pointer() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["datasets"].append(dict(doc["semantic_model"][0]["datasets"][0]))

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert result.errors[0].code == "DUPLICATE_NAME"
    assert result.errors[0].json_pointer == "/semantic_model/0/datasets/1/name"
    assert "orders" in result.errors[0].message
    assert result.errors[0].hint


def test_validate_primary_key_must_reference_dataset_field() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["datasets"][0]["primary_key"] = ["missing_id"]

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert any(issue.code == "UNKNOWN_FIELD" for issue in result.errors)
    assert any(issue.json_pointer.endswith("/primary_key/0") for issue in result.errors)


def test_validate_relationship_must_reference_known_datasets_and_fields() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["relationships"] = [
        {
            "name": "orders_to_customers",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["customer_id"],
        }
    ]

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert any(issue.code == "UNKNOWN_DATASET" for issue in result.errors)


def test_validate_metric_extension_allows_unknown_additive_dimensions() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["metrics"][0]["custom_extensions"][0]["data"][
        "additive_dimensions"
    ] = ["missing_dimension"]

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is True
    assert result.errors == []


def test_validate_metric_extension_all_additive_dimensions_sentinel() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["metrics"][0]["custom_extensions"][0]["data"][
        "additive_dimensions"
    ] = ["__all"]

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is True
    assert result.errors == []


def test_validate_metric_extension_rejects_mixed_all_additive_dimensions_sentinel() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["metrics"][0]["custom_extensions"][0]["data"][
        "additive_dimensions"
    ] = ["__all", "order_time"]

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert any("__all" in issue.message for issue in result.errors)


def test_validate_datasource_grounding_checks_live_columns() -> None:
    doc = _valid_doc()
    service = _FakeDatasourceService(columns=["order_id", "order_time"])

    result = OsiSemanticDocumentValidator(datasource_service=service).validate(doc)

    assert result.valid is False
    assert any(issue.code == "UNKNOWN_PHYSICAL_COLUMN" for issue in result.errors)
    assert any(issue.context.get("column") == "amount" for issue in result.errors)


def test_validate_datasource_grounding_dry_runs_field_and_metric_expressions() -> None:
    doc = _valid_doc()
    service = _FakeDatasourceService()

    result = OsiSemanticDocumentValidator(datasource_service=service).validate(doc)

    assert result.valid is True
    assert service.engine.queries == [
        "SELECT order_id AS value FROM analytics.orders LIMIT 10",
        "SELECT order_time AS value FROM analytics.orders LIMIT 10",
        "SELECT amount AS value FROM analytics.orders LIMIT 10",
        (
            "SELECT SUM(amount) AS value FROM "
            "(SELECT * FROM analytics.orders LIMIT 10) __marivo_sample"
        ),
    ]


def test_validate_field_expression_dry_run_failure_returns_structured_issue() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["datasets"][0]["fields"][1]["expression"]["dialects"][0][
        "expression"
    ] = "CAST(order_time AS NOT_A_TYPE)"
    service = _FakeDatasourceService(engine=_FakeEngine(fail_on="NOT_A_TYPE"))

    result = OsiSemanticDocumentValidator(datasource_service=service).validate(doc)

    assert result.valid is False
    issue = next(
        issue for issue in result.errors if issue.code == "FIELD_EXPRESSION_DRY_RUN_FAILED"
    )
    assert issue.json_pointer == "/semantic_model/0/datasets/0/fields/1/expression"
    assert issue.context["field"] == "order_time"


def test_validate_metric_expression_dry_run_failure_returns_structured_issue() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["metrics"][0]["expression"]["dialects"][0]["expression"] = (
        "SUM(missing_amount)"
    )
    service = _FakeDatasourceService(engine=_FakeEngine(fail_on="missing_amount"))

    result = OsiSemanticDocumentValidator(datasource_service=service).validate(doc)

    assert result.valid is False
    issue = next(
        issue for issue in result.errors if issue.code == "METRIC_EXPRESSION_DRY_RUN_FAILED"
    )
    assert issue.json_pointer == "/semantic_model/0/metrics/0/expression"
    assert issue.context["metric"] == "revenue"


def test_validate_metric_dry_run_uses_sampled_subquery_before_aggregation() -> None:
    doc = _valid_doc()
    service = _FakeDatasourceService()

    OsiSemanticDocumentValidator(datasource_service=service).validate(doc)

    metric_sql = service.engine.queries[-1]
    assert "FROM (SELECT * FROM analytics.orders LIMIT 10) __marivo_sample" in metric_sql
    assert metric_sql != "SELECT SUM(amount) AS value FROM analytics.orders LIMIT 10"


def test_validate_multi_dataset_metric_does_not_require_observed_dataset() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["datasets"].append(
        {
            "name": "customers",
            "source": "analytics.customers",
            "custom_extensions": [{"vendor_name": "MARIVO", "data": {"datasource_id": "ds_001"}}],
            "fields": [
                {
                    "name": "customer_id",
                    "expression": {
                        "dialects": [{"dialect": "ANSI_SQL", "expression": "customer_id"}]
                    },
                }
            ],
        }
    )

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is True
    assert result.errors == []


def test_validate_multi_dataset_metric_dry_run_joins_relationship_graph() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["datasets"][0]["fields"].append(
        {
            "name": "customer_id",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "customer_id"}]},
        }
    )
    doc["semantic_model"][0]["datasets"].append(
        {
            "name": "customers",
            "source": "analytics.customers",
            "custom_extensions": [{"vendor_name": "MARIVO", "data": {"datasource_id": "ds_001"}}],
            "fields": [
                {
                    "name": "customer_id",
                    "expression": {
                        "dialects": [{"dialect": "ANSI_SQL", "expression": "customer_id"}]
                    },
                },
                {
                    "name": "segment",
                    "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "segment"}]},
                },
            ],
        }
    )
    doc["semantic_model"][0]["relationships"] = [
        {
            "name": "orders_to_customers",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["customer_id"],
        }
    ]
    service = _FakeDatasourceService()

    result = OsiSemanticDocumentValidator(datasource_service=service).validate(doc)

    assert result.valid is True
    metric_sql = service.engine.queries[-1]
    assert metric_sql == (
        "SELECT SUM(amount) AS value FROM "
        "(SELECT * FROM analytics.orders LIMIT 10) orders "
        "JOIN (SELECT * FROM analytics.customers LIMIT 10) customers "
        "ON customers.customer_id = orders.customer_id"
    )


def test_validate_multi_dataset_metric_dry_run_reports_disconnected_graph() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["datasets"].append(
        {
            "name": "customers",
            "source": "analytics.customers",
            "custom_extensions": [{"vendor_name": "MARIVO", "data": {"datasource_id": "ds_001"}}],
            "fields": [
                {
                    "name": "customer_id",
                    "expression": {
                        "dialects": [{"dialect": "ANSI_SQL", "expression": "customer_id"}]
                    },
                }
            ],
        }
    )

    result = OsiSemanticDocumentValidator(datasource_service=_FakeDatasourceService()).validate(doc)

    assert result.valid is False
    issue = next(
        issue for issue in result.errors if issue.code == "METRIC_DRY_RUN_JOIN_GRAPH_DISCONNECTED"
    )
    assert issue.json_pointer == "/semantic_model/0/metrics/0/expression"


def test_validate_cross_datasource_metric_skips_joined_dry_run() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["datasets"][0]["fields"].append(
        {
            "name": "customer_id",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "customer_id"}]},
        }
    )
    doc["semantic_model"][0]["datasets"].append(
        {
            "name": "customers",
            "source": "analytics.customers",
            "custom_extensions": [{"vendor_name": "MARIVO", "data": {"datasource_id": "ds_002"}}],
            "fields": [
                {
                    "name": "customer_id",
                    "expression": {
                        "dialects": [{"dialect": "ANSI_SQL", "expression": "customer_id"}]
                    },
                }
            ],
        }
    )
    doc["semantic_model"][0]["relationships"] = [
        {
            "name": "orders_to_customers",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["customer_id"],
        }
    ]
    ds1_engine = _FakeEngine()
    ds2_engine = _FakeEngine()
    service = _FakeDatasourceService(
        engines_by_datasource={"ds_001": ds1_engine, "ds_002": ds2_engine}
    )

    result = OsiSemanticDocumentValidator(datasource_service=service).validate(doc)

    assert result.valid is True
    assert not any("JOIN" in query for query in ds1_engine.queries + ds2_engine.queries)


def _composite_time_doc(
    *,
    log_date_format: str = "yyyymmdd",
    log_hour_format: str | None = None,
    log_date_granularity: str = "day",
    log_hour_granularity: str | None = None,
    log_date_data_type: str = "string",
    log_hour_data_type: str | None = None,
    required_prefix: str | None = None,
    extra_required_prefix: str | None = None,
) -> dict:
    """Build a document with composite log_date + log_hour time fields."""
    doc: dict = {
        "version": "0.1.1",
        "semantic_model": [
            {
                "name": "commerce",
                "datasets": [
                    {
                        "name": "orders",
                        "source": "analytics.orders",
                        "primary_key": ["order_id"],
                        "custom_extensions": [
                            {"vendor_name": "MARIVO", "data": {"datasource_id": "ds_001"}}
                        ],
                        "fields": [
                            {
                                "name": "order_id",
                                "expression": {
                                    "dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]
                                },
                            },
                            {
                                "name": "log_date",
                                "expression": {
                                    "dialects": [{"dialect": "ANSI_SQL", "expression": "log_date"}]
                                },
                                "dimension": {"is_time": True},
                                "custom_extensions": [
                                    {
                                        "vendor_name": "MARIVO",
                                        "data": {
                                            "support_min_granularity": log_date_granularity,
                                            "data_type": log_date_data_type,
                                            "format": log_date_format,
                                            **(
                                                {"required_prefix": extra_required_prefix}
                                                if extra_required_prefix is not None
                                                else {}
                                            ),
                                        },
                                    }
                                ],
                            },
                        ],
                    }
                ],
                "metrics": [
                    {
                        "name": "revenue",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(amount)"}]
                        },
                        "custom_extensions": [
                            {"vendor_name": "MARIVO", "data": {"additive_dimensions": ["__all"]}}
                        ],
                    }
                ],
            }
        ],
    }
    if log_hour_format is not None:
        doc["semantic_model"][0]["datasets"][0]["fields"].append(
            {
                "name": "log_hour",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "log_hour"}]},
                "dimension": {"is_time": True},
                "custom_extensions": [
                    {
                        "vendor_name": "MARIVO",
                        "data": {
                            "support_min_granularity": log_hour_granularity or "hour",
                            "data_type": log_hour_data_type or "string",
                            "format": log_hour_format,
                            **(
                                {"required_prefix": required_prefix}
                                if required_prefix is not None
                                else {}
                            ),
                        },
                    }
                ],
            }
        )
    return doc


def test_validate_hour_only_format_with_required_prefix_is_valid() -> None:
    """format 'hh' with required_prefix referencing a time field is valid."""
    doc = _composite_time_doc(
        log_hour_format="hh", log_hour_data_type="string", required_prefix="log_date"
    )

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is True
    assert result.errors == []


def test_validate_hour_only_format_without_required_prefix_is_invalid() -> None:
    """format 'hh' without required_prefix is invalid (MISSING_REQUIRED_PREFIX)."""
    doc = _composite_time_doc(log_hour_format="hh", log_hour_data_type="string")

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert result.errors[0].code == "MISSING_REQUIRED_PREFIX"
    assert "log_hour" in result.errors[0].message
    assert result.errors[0].hint


def test_validate_complete_format_with_required_prefix_is_invalid() -> None:
    """Complete format like 'yyyymmdd' must not have required_prefix."""
    doc = _composite_time_doc(
        extra_required_prefix="log_hour",
    )

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert result.errors[0].code == "INVALID_REQUIRED_PREFIX_FORMAT"
    assert "log_date" in result.errors[0].message


def test_validate_required_prefix_references_nonexistent_field_is_invalid() -> None:
    """required_prefix must reference a time field on the same dataset."""
    doc = _composite_time_doc(
        log_hour_format="hh", log_hour_data_type="string", required_prefix="missing_date"
    )

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert any(issue.code == "REQUIRED_PREFIX_FIELD_NOT_FOUND" for issue in result.errors)
    issue = next(
        issue for issue in result.errors if issue.code == "REQUIRED_PREFIX_FIELD_NOT_FOUND"
    )
    assert "missing_date" in issue.message
