from __future__ import annotations

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


class _FakeDatasourceService:
    def __init__(self, *, columns: list[str] | None = None, fail: bool = False) -> None:
        self.columns = columns or ["order_id", "order_time", "amount"]
        self.fail = fail

    def get_datasource(self, datasource_id: str) -> dict:
        if datasource_id != "ds_001" or self.fail:
            raise KeyError(datasource_id)
        return {"datasource_id": datasource_id, "status": "active"}

    def browse_catalog_columns(
        self, datasource_id: str, schema_name: str, table_name: str
    ) -> list[dict]:
        if datasource_id != "ds_001" or schema_name != "analytics" or table_name != "orders":
            raise KeyError((datasource_id, schema_name, table_name))
        return [{"name": column} for column in self.columns]


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


def test_validate_metric_extension_references_known_additive_dimensions() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["metrics"][0]["custom_extensions"][0]["data"][
        "additive_dimensions"
    ] = ["missing_dimension"]

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert any(issue.code == "UNKNOWN_FIELD" for issue in result.errors)


def test_validate_datasource_grounding_checks_live_columns() -> None:
    doc = _valid_doc()
    service = _FakeDatasourceService(columns=["order_id", "order_time"])

    result = OsiSemanticDocumentValidator(datasource_service=service).validate(doc)

    assert result.valid is False
    assert any(issue.code == "UNKNOWN_PHYSICAL_COLUMN" for issue in result.errors)
    assert any(issue.context.get("column") == "amount" for issue in result.errors)
