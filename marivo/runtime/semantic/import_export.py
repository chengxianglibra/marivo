from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from marivo.adapters.metadata import MetadataStore, MetadataTransaction
from marivo.contracts.errors import ErrorCode, NotFoundError, ValidationError
from marivo.contracts.generated import Dataset, Metric, OSIDocument, Relationship, SemanticModel
from marivo.runtime.semantic.osi_storage import (
    _storage_to_dataset,
    _storage_to_metric,
    _storage_to_relationship,
    dataset_to_storage,
    field_to_storage,
    metric_to_storage,
    model_to_storage,
    relationship_to_storage,
    storage_to_model,
)


class ImportCounter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: int = 0
    updated: int = 0
    unchanged: int = 0


class DatasourceBindingReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str
    datasource_id: str
    selection: str = "first_accessible_candidate"


class ImportModelReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    created: bool
    updated: bool
    datasets: ImportCounter = Field(default_factory=ImportCounter)
    fields: ImportCounter = Field(default_factory=ImportCounter)
    metrics: ImportCounter = Field(default_factory=ImportCounter)
    relationships: ImportCounter = Field(default_factory=ImportCounter)
    datasource_bindings: list[DatasourceBindingReport] = Field(default_factory=list)


class ImportErrorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    model: str | None = None
    dataset: str | None = None


class ImportOsiDocumentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[ImportModelReport]
    errors: list[ImportErrorReport] = Field(default_factory=list)


class SemanticValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    json_pointer: str
    severity: Literal["error", "warning"] = "error"
    hint: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class OsiSemanticValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    schema_version: str = "0.1.1"
    errors: list[SemanticValidationIssue] = Field(default_factory=list)
    warnings: list[SemanticValidationIssue] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


class ImportOsiSemanticModelsResponse(OsiSemanticValidationResult):
    import_report: ImportOsiDocumentReport | None = None


class OsiSemanticDocumentValidator:
    """Validate OSI-Marivo semantic model documents before import."""

    def __init__(self, datasource_service: Any | None = None) -> None:
        self.datasource_service = datasource_service

    def validate(self, document: dict[str, Any]) -> OsiSemanticValidationResult:
        errors: list[SemanticValidationIssue] = []
        try:
            parsed = OSIDocument.model_validate(document)
        except Exception as exc:
            return OsiSemanticValidationResult(
                valid=False,
                schema_version=str(document.get("version") or "0.1.1"),
                errors=[
                    SemanticValidationIssue(
                        code="SCHEMA_VALIDATION_FAILED",
                        message=str(exc),
                        json_pointer="",
                        hint=(
                            "Update the document to match "
                            "osi-marivo-spec/schema/osi-marivo.schema.json."
                        ),
                    )
                ],
                summary=_summarize_document(document),
            )

        semantic_models = document.get("semantic_model") or []
        if not semantic_models:
            errors.append(
                SemanticValidationIssue(
                    code="EMPTY_SEMANTIC_MODEL",
                    message="semantic_model must contain at least one semantic model.",
                    json_pointer="/semantic_model",
                    hint="Add a complete semantic model object before validating or importing.",
                )
            )
        errors.extend(_duplicate_name_issues(semantic_models, "semantic model", "/semantic_model"))
        for model_index, model in enumerate(semantic_models):
            if not isinstance(model, dict):
                continue
            model_pointer = f"/semantic_model/{model_index}"
            errors.extend(
                _duplicate_name_issues(
                    model.get("datasets") or [],
                    "dataset",
                    f"{model_pointer}/datasets",
                )
            )
            errors.extend(
                _duplicate_name_issues(
                    model.get("metrics") or [],
                    "metric",
                    f"{model_pointer}/metrics",
                )
            )
            errors.extend(
                _duplicate_name_issues(
                    model.get("relationships") or [],
                    "relationship",
                    f"{model_pointer}/relationships",
                )
            )
            for dataset_index, dataset in enumerate(model.get("datasets") or []):
                if isinstance(dataset, dict):
                    errors.extend(
                        _duplicate_name_issues(
                            dataset.get("fields") or [],
                            "field",
                            f"{model_pointer}/datasets/{dataset_index}/fields",
                        )
                    )
            errors.extend(_reference_issues(model, model_pointer))
            if self.datasource_service is not None:
                errors.extend(self._datasource_issues(model, model_pointer))

        return OsiSemanticValidationResult(
            valid=not errors,
            schema_version=parsed.version or "0.1.1",
            errors=errors,
            summary=_summarize_document(document),
        )

    def _datasource_issues(
        self,
        model: dict[str, Any],
        model_pointer: str,
    ) -> list[SemanticValidationIssue]:
        assert self.datasource_service is not None
        issues: list[SemanticValidationIssue] = []
        for dataset_index, dataset in enumerate(model.get("datasets") or []):
            if not isinstance(dataset, dict):
                continue
            dataset_name = str(dataset.get("name") or "")
            datasource_id = _extract_marivo_datasource_id(dataset)
            source = str(dataset.get("source") or "")
            parsed_source = DatasourceBinder._parse_source(source)
            if datasource_id is None:
                issues.append(
                    SemanticValidationIssue(
                        code="MISSING_DATASOURCE_ID",
                        message=f"Dataset {dataset_name!r} has no MARIVO datasource_id extension.",
                        json_pointer=f"{model_pointer}/datasets/{dataset_index}/custom_extensions",
                        hint="Add a MARIVO custom extension with data.datasource_id.",
                        context={"dataset": dataset_name},
                    )
                )
                continue
            if parsed_source is None:
                issues.append(
                    SemanticValidationIssue(
                        code="INVALID_DATASET_SOURCE",
                        message=(
                            f"Dataset {dataset_name!r} source must be schema.table "
                            "or catalog.schema.table."
                        ),
                        json_pointer=f"{model_pointer}/datasets/{dataset_index}/source",
                        hint="Use a datasource-local relation FQN such as analytics.orders.",
                        context={"dataset": dataset_name, "source": source},
                    )
                )
                continue
            schema_name, table_name = parsed_source
            try:
                self.datasource_service.get_datasource(datasource_id)
                rows = self.datasource_service.browse_catalog_columns(
                    datasource_id,
                    schema_name,
                    table_name,
                )
            except (KeyError, ValueError) as exc:
                issues.append(
                    SemanticValidationIssue(
                        code="DATASOURCE_GROUNDING_FAILED",
                        message=str(exc),
                        json_pointer=f"{model_pointer}/datasets/{dataset_index}",
                        hint="Check datasource_id and dataset.source against live datasource metadata.",
                        context={
                            "dataset": dataset_name,
                            "datasource_id": datasource_id,
                            "schema": schema_name,
                            "table": table_name,
                        },
                    )
                )
                continue
            physical_columns = {
                str(row.get("name") or row.get("column_name") or "")
                for row in rows
                if isinstance(row, dict)
            }
            for field_index, field_data in enumerate(dataset.get("fields") or []):
                if not isinstance(field_data, dict):
                    continue
                expression = _first_ansi_expression(field_data)
                if expression and _looks_like_column_reference(expression):
                    column = expression.strip()
                    if column not in physical_columns:
                        issues.append(
                            SemanticValidationIssue(
                                code="UNKNOWN_PHYSICAL_COLUMN",
                                message=(
                                    f"Field {field_data.get('name')!r} references physical column "
                                    f"{column!r}, but it is not present on {source!r}."
                                ),
                                json_pointer=(
                                    f"{model_pointer}/datasets/{dataset_index}/fields/"
                                    f"{field_index}/expression"
                                ),
                                hint=(
                                    "Update the field expression or choose an existing "
                                    "datasource column."
                                ),
                                context={
                                    "dataset": dataset_name,
                                    "datasource_id": datasource_id,
                                    "schema": schema_name,
                                    "table": table_name,
                                    "column": column,
                                },
                            )
                        )
        return issues


@dataclass(frozen=True)
class DatasetBinding:
    model_name: str
    dataset_name: str
    datasource_id: str
    selection: str = "first_accessible_candidate"


class DatasourceBinder:
    """Bind imported datasets to the first stable accessible datasource."""

    def __init__(self, datasource_service: Any | None) -> None:
        self.datasource_service = datasource_service
        self._catalog_cache: dict[tuple[str, str, str], bool] = {}

    def bind_dataset(self, *, model_name: str, dataset: dict[str, Any]) -> DatasetBinding:
        dataset_name = str(dataset.get("name") or "<unnamed>")
        source = str(dataset.get("source") or "").strip()
        parsed = self._parse_source(source)
        if parsed is None:
            raise ValidationError(
                code=ErrorCode.DATASOURCE_BINDING_FAILED,
                message=(
                    f"Dataset {dataset_name} source {source!r} "
                    "is not a schema.table or catalog.schema.table FQN"
                ),
                detail={"model": model_name, "dataset": dataset_name, "source": source},
            )

        schema_name, table_name = parsed
        for candidate in self._candidate_datasources():
            datasource_id = str(candidate.get("datasource_id") or "")
            if not datasource_id:
                continue
            if self._has_table(datasource_id, schema_name, table_name):
                return DatasetBinding(
                    model_name=model_name,
                    dataset_name=dataset_name,
                    datasource_id=datasource_id,
                )

        raise ValidationError(
            code=ErrorCode.DATASOURCE_BINDING_FAILED,
            message=(
                f"Dataset {dataset_name} source {source!r} "
                "could not be bound to an accessible datasource"
            ),
            detail={"model": model_name, "dataset": dataset_name, "source": source},
        )

    def _candidate_datasources(self) -> list[dict[str, Any]]:
        if self.datasource_service is None:
            return []

        rows = [dict(row) for row in self.datasource_service.list_datasources()]
        active = [row for row in rows if str(row.get("status") or "active") == "active"]
        return sorted(
            active,
            key=lambda row: (
                str(row.get("display_name") or row.get("name") or ""),
                str(row.get("datasource_id") or ""),
            ),
        )

    def _has_table(self, datasource_id: str, schema_name: str, table_name: str) -> bool:
        key = (datasource_id, schema_name, table_name)
        if key in self._catalog_cache:
            return self._catalog_cache[key]

        assert self.datasource_service is not None
        try:
            self.datasource_service.browse_catalog_columns(datasource_id, schema_name, table_name)
        except KeyError:
            self._catalog_cache[key] = False
            return False
        except ValueError as exc:
            raise ValidationError(
                code=ErrorCode.DATASET_ACCESS_DENIED,
                message=str(exc),
                detail={
                    "datasource_id": datasource_id,
                    "schema": schema_name,
                    "table": table_name,
                },
            ) from exc

        self._catalog_cache[key] = True
        return True

    @staticmethod
    def _parse_source(source: str) -> tuple[str, str] | None:
        parts = source.split(".")
        if any(part == "" for part in parts):
            return None
        if len(parts) == 2:
            return parts[0], parts[1]
        if len(parts) == 3:
            return parts[1], parts[2]
        return None


@dataclass(frozen=True)
class SemanticMergePlan:
    document: dict[str, Any]
    bindings: list[DatasetBinding] = field(default_factory=list)


class SemanticMergePlanner:
    def __init__(self, binder: DatasourceBinder | None = None) -> None:
        self.binder = binder

    def preflight(self, document: dict[str, Any]) -> SemanticMergePlan:
        semantic_models = document.get("semantic_model")
        if not isinstance(semantic_models, list) or not semantic_models:
            raise ValidationError(
                code=ErrorCode.VALIDATION,
                message="semantic_model must contain at least one semantic model",
                detail={"field": "semantic_model"},
            )

        _reject_duplicates(semantic_models, "semantic model", "semantic_model")
        for model in semantic_models:
            if not isinstance(model, dict):
                continue
            model_name = str(model.get("name") or "<unnamed>")
            model_path = f"semantic_model[{model_name}]"
            _reject_duplicates(model.get("datasets", []), "dataset", f"{model_path}.datasets")
            _reject_duplicates(model.get("metrics", []), "metric", f"{model_path}.metrics")
            _reject_duplicates(
                model.get("relationships", []),
                "relationship",
                f"{model_path}.relationships",
            )

            datasets = model.get("datasets", [])
            if not isinstance(datasets, list):
                continue
            for dataset in datasets:
                if not isinstance(dataset, dict):
                    continue
                dataset_name = str(dataset.get("name") or "<unnamed>")
                _reject_duplicates(
                    dataset.get("fields", []),
                    "field",
                    f"{model_path}.datasets[{dataset_name}].fields",
                )

        bindings = []
        if self.binder is not None:
            bindings = self._bind_datasets(semantic_models)

        return SemanticMergePlan(document=document, bindings=bindings)

    def _bind_datasets(self, semantic_models: list[object]) -> list[DatasetBinding]:
        assert self.binder is not None
        bindings: list[DatasetBinding] = []
        for model in semantic_models:
            if not isinstance(model, dict):
                continue
            model_name = str(model.get("name") or "<unnamed>")
            datasets = model.get("datasets", [])
            if not isinstance(datasets, list):
                continue
            for dataset in datasets:
                if not isinstance(dataset, dict):
                    continue
                if _extract_marivo_datasource_id(dataset):
                    continue
                binding = self.binder.bind_dataset(model_name=model_name, dataset=dataset)
                _set_marivo_datasource_id(dataset, binding.datasource_id)
                bindings.append(binding)
        return bindings


class SemanticMergeExecutor:
    """Apply a validated OSI document to private semantic working copies."""

    def __init__(self, store: MetadataStore) -> None:
        self.store = store

    def execute(
        self,
        *,
        document: dict[str, Any],
        owner_user: str,
        bindings: list[DatasetBinding] | None = None,
    ) -> ImportOsiDocumentReport:
        binding_lookup: dict[tuple[str, str], DatasetBinding] = {
            (binding.model_name, binding.dataset_name): binding for binding in bindings or []
        }
        reports: list[ImportModelReport] = []
        with self.store.transaction() as txn:
            for model_data in document.get("semantic_model") or []:
                model = SemanticModel.model_validate(model_data)
                report = self._merge_model(
                    txn,
                    model,
                    owner_user=owner_user,
                    binding_lookup=binding_lookup,
                )
                reports.append(report)
        return ImportOsiDocumentReport(models=reports)

    def _merge_model(
        self,
        txn: MetadataTransaction,
        model: SemanticModel,
        *,
        owner_user: str,
        binding_lookup: dict[tuple[str, str], DatasetBinding],
    ) -> ImportModelReport:
        existing = txn.query_one(
            "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'private' AND owner_user = ?",
            [model.name, owner_user],
        )
        storage_data = model_to_storage(model, owner_user=owner_user, visibility="private")
        if existing is None:
            txn.execute(
                """
                INSERT INTO semantic_models
                    (name, description, ai_context, visibility, owner_user)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    storage_data["name"],
                    storage_data["description"],
                    storage_data["ai_context"],
                    storage_data["visibility"],
                    storage_data["owner_user"],
                ],
            )
            row = txn.query_one(
                "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'private' AND owner_user = ?",
                [model.name, owner_user],
            )
            assert row is not None
            created = True
        else:
            row = existing
            txn.execute(
                """
                UPDATE semantic_models
                SET description = ?, ai_context = ?, updated_at = datetime('now')
                WHERE model_id = ?
                """,
                [storage_data["description"], storage_data["ai_context"], row["model_id"]],
            )
            created = False

        model_id = int(row["model_id"])
        self._delete_model_children(txn, model_id)
        report = ImportModelReport(name=model.name, created=created, updated=not created)
        for dataset in model.datasets:
            dataset_report = self._merge_dataset(txn, dataset, model_id=model_id)
            _add_counter(report.datasets, dataset_report)
            _add_counter(report.fields, dataset_report.fields)
            binding = binding_lookup.get((model.name, dataset.name))
            if binding is not None:
                report.datasource_bindings.append(
                    DatasourceBindingReport(
                        dataset=binding.dataset_name,
                        datasource_id=binding.datasource_id,
                        selection=binding.selection,
                    )
                )

        for metric in model.metrics or []:
            _add_counter(report.metrics, self._replace_metric(txn, metric, model_id=model_id))

        for relationship in model.relationships or []:
            _add_counter(
                report.relationships,
                self._replace_relationship(txn, relationship, model_id=model_id),
            )

        self._ensure_readiness_row(txn, model_id)
        return report

    def _delete_model_children(self, txn: MetadataTransaction, model_id: int) -> None:
        dataset_rows = txn.query_rows(
            "SELECT dataset_id FROM semantic_datasets WHERE model_id = ?",
            [model_id],
        )
        for dataset_row in dataset_rows:
            txn.execute(
                "DELETE FROM semantic_fields WHERE dataset_id = ?",
                [dataset_row["dataset_id"]],
            )
        txn.execute("DELETE FROM semantic_metrics WHERE model_id = ?", [model_id])
        txn.execute("DELETE FROM semantic_relationships WHERE model_id = ?", [model_id])
        txn.execute("DELETE FROM semantic_datasets WHERE model_id = ?", [model_id])

    def _merge_dataset(
        self, txn: MetadataTransaction, dataset: Dataset, *, model_id: int
    ) -> _DatasetMergeResult:
        existing = txn.query_one(
            "SELECT * FROM semantic_datasets WHERE model_id = ? AND name = ?",
            [model_id, dataset.name],
        )
        ds_storage = dataset_to_storage(dataset, model_id)
        if existing is None:
            txn.execute(
                """
                INSERT INTO semantic_datasets
                    (model_id, name, source, primary_key, unique_keys, description,
                     ai_context, datasource_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ds_storage["model_id"],
                    ds_storage["name"],
                    ds_storage["source"],
                    ds_storage["primary_key"],
                    ds_storage["unique_keys"],
                    ds_storage["description"],
                    ds_storage["ai_context"],
                    ds_storage["datasource_id"],
                ],
            )
            row = txn.query_one(
                "SELECT * FROM semantic_datasets WHERE model_id = ? AND name = ?",
                [model_id, dataset.name],
            )
            assert row is not None
            count = _CountDelta(created=1)
        else:
            row = existing
            txn.execute(
                """
                UPDATE semantic_datasets
                SET source = ?, primary_key = ?, unique_keys = ?, description = ?,
                    ai_context = ?, datasource_id = ?, updated_at = datetime('now')
                WHERE dataset_id = ?
                """,
                [
                    ds_storage["source"],
                    ds_storage["primary_key"],
                    ds_storage["unique_keys"],
                    ds_storage["description"],
                    ds_storage["ai_context"],
                    ds_storage["datasource_id"],
                    row["dataset_id"],
                ],
            )
            count = _CountDelta(updated=1)

        fields = _CountDelta()
        dataset_id = int(row["dataset_id"])
        for pos, field_model in enumerate(dataset.fields or []):
            _add_delta(
                fields,
                self._replace_field(txn, field_model, dataset_id=dataset_id, position=pos),
            )
        return _DatasetMergeResult(
            created=count.created,
            updated=count.updated,
            unchanged=count.unchanged,
            fields=fields,
        )

    def _replace_field(
        self,
        txn: MetadataTransaction,
        field_model: Any,
        *,
        dataset_id: int,
        position: int,
    ) -> _CountDelta:
        existing = txn.query_one(
            "SELECT field_id FROM semantic_fields WHERE dataset_id = ? AND name = ?",
            [dataset_id, field_model.name],
        )
        if existing is not None:
            txn.execute(
                "DELETE FROM semantic_fields WHERE field_id = ?",
                [existing["field_id"]],
            )
        f_storage = field_to_storage(field_model, dataset_id, position)
        txn.execute(
            """
            INSERT INTO semantic_fields
                (dataset_id, name, expression, is_time, is_dimension, label, description,
                 ai_context, data_type, position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                f_storage["dataset_id"],
                f_storage["name"],
                f_storage["expression"],
                f_storage["is_time"],
                f_storage["is_dimension"],
                f_storage["label"],
                f_storage["description"],
                f_storage["ai_context"],
                f_storage["data_type"],
                f_storage["position"],
            ],
        )
        return _CountDelta(updated=1) if existing is not None else _CountDelta(created=1)

    def _replace_metric(
        self, txn: MetadataTransaction, metric: Metric, *, model_id: int
    ) -> _CountDelta:
        existing = txn.query_one(
            "SELECT metric_id FROM semantic_metrics WHERE model_id = ? AND name = ?",
            [model_id, metric.name],
        )
        if existing is not None:
            txn.execute(
                "DELETE FROM semantic_metrics WHERE metric_id = ?",
                [existing["metric_id"]],
            )
        metric_storage = metric_to_storage(metric, model_id)
        txn.execute(
            """
            INSERT INTO semantic_metrics
                (model_id, name, expression, description, ai_context, additive_dimensions, sample_kind)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                metric_storage["model_id"],
                metric_storage["name"],
                metric_storage["expression"],
                metric_storage["description"],
                metric_storage["ai_context"],
                metric_storage["additive_dimensions"],
                metric_storage["sample_kind"],
            ],
        )
        return _CountDelta(updated=1) if existing is not None else _CountDelta(created=1)

    def _replace_relationship(
        self,
        txn: MetadataTransaction,
        relationship: Relationship,
        *,
        model_id: int,
    ) -> _CountDelta:
        existing = txn.query_one(
            "SELECT relationship_id FROM semantic_relationships WHERE model_id = ? AND name = ?",
            [model_id, relationship.name],
        )
        if existing is not None:
            txn.execute(
                "DELETE FROM semantic_relationships WHERE relationship_id = ?",
                [existing["relationship_id"]],
            )
        rel_storage = relationship_to_storage(relationship, model_id)
        txn.execute(
            """
            INSERT INTO semantic_relationships
                (model_id, name, from_dataset, to_dataset, from_columns, to_columns,
                 ai_context, cardinality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                rel_storage["model_id"],
                rel_storage["name"],
                rel_storage["from_dataset"],
                rel_storage["to_dataset"],
                rel_storage["from_columns"],
                rel_storage["to_columns"],
                rel_storage["ai_context"],
                rel_storage["cardinality"],
            ],
        )
        return _CountDelta(updated=1) if existing is not None else _CountDelta(created=1)

    def _ensure_readiness_row(self, txn: MetadataTransaction, model_id: int) -> None:
        existing = txn.query_one(
            "SELECT 1 FROM semantic_readiness_status WHERE model_id = ?",
            [model_id],
        )
        if existing is None:
            txn.execute(
                """
                INSERT INTO semantic_readiness_status (model_id, status, blockers)
                VALUES (?, 'not_ready', '[]')
                """,
                [model_id],
            )


class OsiDocumentExporter:
    """Export private semantic working copies as an OSI document."""

    def __init__(self, store: MetadataStore) -> None:
        self.store = store

    def export(self, *, owner_user: str, semantic_model_name: str | None = None) -> dict[str, Any]:
        if semantic_model_name is None:
            rows = self.store.query_rows(
                """
                SELECT * FROM semantic_models
                WHERE visibility = 'private' AND owner_user = ?
                ORDER BY name
                """,
                [owner_user],
            )
        else:
            row = self.store.query_one(
                """
                SELECT * FROM semantic_models
                WHERE name = ? AND visibility = 'private' AND owner_user = ?
                """,
                [semantic_model_name, owner_user],
            )
            if row is None:
                raise NotFoundError(
                    ErrorCode.NOT_FOUND_SEMANTIC_MODEL,
                    f"Private semantic model '{semantic_model_name}' not found",
                )
            rows = [row]

        return {
            "version": "0.1.1",
            "semantic_model": [self._assemble_model(row) for row in rows],
        }

    def _assemble_model(self, model_row: dict[str, Any]) -> dict[str, Any]:
        model_id = model_row["model_id"]
        ds_rows = self.store.query_rows(
            "SELECT * FROM semantic_datasets WHERE model_id = ? ORDER BY dataset_id",
            [model_id],
        )
        datasets: list[dict[str, Any]] = []
        for ds_row in ds_rows:
            field_rows = self.store.query_rows(
                "SELECT * FROM semantic_fields WHERE dataset_id = ? ORDER BY position, field_id",
                [ds_row["dataset_id"]],
            )
            ds_dict = dict(ds_row)
            ds_dict["_fields"] = [dict(field_row) for field_row in field_rows]
            datasets.append(_storage_to_dataset(ds_dict))

        rel_rows = self.store.query_rows(
            "SELECT * FROM semantic_relationships WHERE model_id = ? ORDER BY relationship_id",
            [model_id],
        )
        relationships = [_storage_to_relationship(dict(row)) for row in rel_rows]

        metric_rows = self.store.query_rows(
            "SELECT * FROM semantic_metrics WHERE model_id = ? ORDER BY metric_id",
            [model_id],
        )
        metrics = [_storage_to_metric(dict(row)) for row in metric_rows]

        return storage_to_model(dict(model_row), datasets, relationships, metrics)


def _reject_duplicates(items: object, label: str, path: str) -> None:
    if not isinstance(items, list):
        return

    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        name = name.strip()
        if not name:
            continue
        if name in seen:
            raise ValidationError(
                code=ErrorCode.VALIDATION,
                message=f"duplicate {label} name {name!r} in {path}",
                detail={"field": path, "name": name},
            )
        seen.add(name)


def _summarize_document(document: dict[str, Any]) -> dict[str, int]:
    models = document.get("semantic_model") if isinstance(document, dict) else []
    if not isinstance(models, list):
        models = []
    dataset_count = 0
    field_count = 0
    metric_count = 0
    relationship_count = 0
    for model in models:
        if not isinstance(model, dict):
            continue
        datasets = model.get("datasets") or []
        metrics = model.get("metrics") or []
        relationships = model.get("relationships") or []
        if isinstance(datasets, list):
            dataset_count += len(datasets)
            for dataset in datasets:
                if isinstance(dataset, dict) and isinstance(dataset.get("fields"), list):
                    field_count += len(dataset["fields"])
        if isinstance(metrics, list):
            metric_count += len(metrics)
        if isinstance(relationships, list):
            relationship_count += len(relationships)
    return {
        "models": len(models),
        "datasets": dataset_count,
        "fields": field_count,
        "metrics": metric_count,
        "relationships": relationship_count,
    }


def _duplicate_name_issues(
    items: object,
    label: str,
    pointer: str,
) -> list[SemanticValidationIssue]:
    if not isinstance(items, list):
        return []
    seen: set[str] = set()
    issues: list[SemanticValidationIssue] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        normalized = name.strip()
        if normalized in seen:
            issues.append(
                SemanticValidationIssue(
                    code="DUPLICATE_NAME",
                    message=f"Duplicate {label} name {normalized!r}.",
                    json_pointer=f"{pointer}/{index}/name",
                    hint=f"Rename this {label} or remove the duplicate object.",
                )
            )
        seen.add(normalized)
    return issues


def _reference_issues(model: dict[str, Any], model_pointer: str) -> list[SemanticValidationIssue]:
    issues: list[SemanticValidationIssue] = []
    datasets_raw = model.get("datasets")
    datasets: list[Any] = datasets_raw if isinstance(datasets_raw, list) else []
    dataset_fields: dict[str, set[str]] = {}
    for dataset_index, dataset in enumerate(datasets):
        if not isinstance(dataset, dict):
            continue
        dataset_name = str(dataset.get("name") or "")
        fields_raw = dataset.get("fields")
        fields: list[Any] = fields_raw if isinstance(fields_raw, list) else []
        field_names = {
            str(field.get("name"))
            for field in fields
            if isinstance(field, dict) and isinstance(field.get("name"), str)
        }
        dataset_fields[dataset_name] = field_names
        for pk_index, field_name in enumerate(dataset.get("primary_key") or []):
            if field_name not in field_names:
                issues.append(
                    _unknown_field_issue(
                        field_name=str(field_name),
                        dataset=dataset_name,
                        json_pointer=(
                            f"{model_pointer}/datasets/{dataset_index}/primary_key/{pk_index}"
                        ),
                    )
                )
        for uk_index, unique_key in enumerate(dataset.get("unique_keys") or []):
            for field_index, field_name in enumerate(unique_key):
                if field_name not in field_names:
                    issues.append(
                        _unknown_field_issue(
                            field_name=str(field_name),
                            dataset=dataset_name,
                            json_pointer=(
                                f"{model_pointer}/datasets/{dataset_index}/unique_keys/"
                                f"{uk_index}/{field_index}"
                            ),
                        )
                    )

    relationships_raw = model.get("relationships")
    relationships: list[Any] = relationships_raw if isinstance(relationships_raw, list) else []
    for rel_index, relationship in enumerate(relationships):
        if not isinstance(relationship, dict):
            continue
        from_dataset = str(relationship.get("from") or "")
        to_dataset = str(relationship.get("to") or "")
        if from_dataset not in dataset_fields:
            issues.append(
                _unknown_dataset_issue(
                    dataset=from_dataset,
                    json_pointer=f"{model_pointer}/relationships/{rel_index}/from",
                )
            )
        if to_dataset not in dataset_fields:
            issues.append(
                _unknown_dataset_issue(
                    dataset=to_dataset,
                    json_pointer=f"{model_pointer}/relationships/{rel_index}/to",
                )
            )
        for column_index, field_name in enumerate(relationship.get("from_columns") or []):
            if from_dataset in dataset_fields and field_name not in dataset_fields[from_dataset]:
                issues.append(
                    _unknown_field_issue(
                        field_name=str(field_name),
                        dataset=from_dataset,
                        json_pointer=(
                            f"{model_pointer}/relationships/{rel_index}/from_columns/{column_index}"
                        ),
                    )
                )
        for column_index, field_name in enumerate(relationship.get("to_columns") or []):
            if to_dataset in dataset_fields and field_name not in dataset_fields[to_dataset]:
                issues.append(
                    _unknown_field_issue(
                        field_name=str(field_name),
                        dataset=to_dataset,
                        json_pointer=(
                            f"{model_pointer}/relationships/{rel_index}/to_columns/{column_index}"
                        ),
                    )
                )

    metrics_raw = model.get("metrics")
    metrics: list[Any] = metrics_raw if isinstance(metrics_raw, list) else []
    for metric_index, metric in enumerate(metrics):
        if isinstance(metric, dict):
            issues.extend(
                _metric_extension_issues(metric, metric_index, dataset_fields, model_pointer)
            )
    return issues


def _metric_extension_issues(
    metric: dict[str, Any],
    metric_index: int,
    dataset_fields: dict[str, set[str]],
    model_pointer: str,
) -> list[SemanticValidationIssue]:
    issues: list[SemanticValidationIssue] = []
    extension_data = _extract_marivo_extension_data(metric)
    if extension_data is None:
        return issues
    observed_dataset = extension_data.get("observed_dataset")
    if not isinstance(observed_dataset, str) and len(dataset_fields) == 1:
        observed_dataset = next(iter(dataset_fields))
    if isinstance(observed_dataset, str) and observed_dataset not in dataset_fields:
        issues.append(
            _unknown_dataset_issue(
                dataset=observed_dataset,
                json_pointer=f"{model_pointer}/metrics/{metric_index}/custom_extensions",
            )
        )
        return issues
    primary_time_field = extension_data.get("primary_time_field")
    if (
        isinstance(observed_dataset, str)
        and observed_dataset in dataset_fields
        and isinstance(primary_time_field, str)
        and primary_time_field not in dataset_fields[observed_dataset]
    ):
        issues.append(
            _unknown_field_issue(
                field_name=primary_time_field,
                dataset=observed_dataset,
                json_pointer=f"{model_pointer}/metrics/{metric_index}/custom_extensions",
            )
        )
    for dimension in extension_data.get("additive_dimensions") or []:
        if (
            isinstance(observed_dataset, str)
            and observed_dataset in dataset_fields
            and isinstance(dimension, str)
            and dimension not in dataset_fields[observed_dataset]
        ):
            issues.append(
                _unknown_field_issue(
                    field_name=dimension,
                    dataset=observed_dataset,
                    json_pointer=f"{model_pointer}/metrics/{metric_index}/custom_extensions",
                )
            )
    sample_kind = extension_data.get("sample_kind")
    if isinstance(sample_kind, str) and sample_kind not in {
        "numeric",
        "rate",
    }:
        issues.append(
            SemanticValidationIssue(
                code="INVALID_SAMPLE_KIND",
                message=f"sample_kind '{sample_kind}' is not a valid enum value.",
                json_pointer=f"{model_pointer}/metrics/{metric_index}/custom_extensions",
                hint="Valid values: 'numeric', 'rate'.",
                context={"sample_kind": sample_kind},
            )
        )
    return issues


def _unknown_dataset_issue(dataset: str, json_pointer: str) -> SemanticValidationIssue:
    return SemanticValidationIssue(
        code="UNKNOWN_DATASET",
        message=f"Dataset {dataset!r} is not defined in this semantic model.",
        json_pointer=json_pointer,
        hint="Add the dataset to this semantic model or update the reference.",
        context={"dataset": dataset},
    )


def _unknown_field_issue(
    *,
    field_name: str,
    dataset: str,
    json_pointer: str,
) -> SemanticValidationIssue:
    return SemanticValidationIssue(
        code="UNKNOWN_FIELD",
        message=f"Field {field_name!r} is not defined on dataset {dataset!r}.",
        json_pointer=json_pointer,
        hint="Add the field to the dataset or update the reference.",
        context={"dataset": dataset, "field": field_name},
    )


def _extract_marivo_extension_data(obj: dict[str, Any]) -> dict[str, Any] | None:
    for extension in obj.get("custom_extensions") or []:
        if isinstance(extension, dict) and extension.get("vendor_name") == "MARIVO":
            data = extension.get("data")
            if isinstance(data, str):
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
            return data if isinstance(data, dict) else None
    return None


def _first_ansi_expression(field: dict[str, Any]) -> str | None:
    expression = field.get("expression")
    if not isinstance(expression, dict):
        return None
    dialects = expression.get("dialects")
    if not isinstance(dialects, list):
        return None
    for dialect in dialects:
        if not isinstance(dialect, dict):
            continue
        if dialect.get("dialect") == "ANSI_SQL" and isinstance(dialect.get("expression"), str):
            return str(dialect["expression"])
    return None


def _looks_like_column_reference(expression: str) -> bool:
    stripped = expression.strip()
    return stripped.replace("_", "").isalnum() and "." not in stripped


@dataclass
class _CountDelta:
    created: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass
class _DatasetMergeResult(_CountDelta):
    fields: _CountDelta = field(default_factory=_CountDelta)


def _add_counter(counter: ImportCounter, delta: _CountDelta) -> None:
    counter.created += delta.created
    counter.updated += delta.updated
    counter.unchanged += delta.unchanged


def _add_delta(target: _CountDelta, delta: _CountDelta) -> None:
    target.created += delta.created
    target.updated += delta.updated
    target.unchanged += delta.unchanged


def _extract_marivo_datasource_id(dataset: dict[str, Any]) -> str | None:
    for extension in dataset.get("custom_extensions") or []:
        if not isinstance(extension, dict) or extension.get("vendor_name") != "MARIVO":
            continue
        data = extension.get("data")
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, dict):
            datasource_id = str(data.get("datasource_id") or "").strip()
            if datasource_id:
                return datasource_id
    return None


def _set_marivo_datasource_id(dataset: dict[str, Any], datasource_id: str) -> None:
    extensions = dataset.setdefault("custom_extensions", [])
    if not isinstance(extensions, list):
        dataset["custom_extensions"] = extensions = []
    for extension in extensions:
        if isinstance(extension, dict) and extension.get("vendor_name") == "MARIVO":
            data = extension.get("data")
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                data = {}
            data["datasource_id"] = datasource_id
            extension["data"] = data
            return
    extensions.append(
        {
            "vendor_name": "MARIVO",
            "data": {"datasource_id": datasource_id},
        }
    )
