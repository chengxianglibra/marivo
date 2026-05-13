from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from marivo.adapters.metadata import MetadataStore, MetadataTransaction
from marivo.contracts.errors import ErrorCode, NotFoundError, ValidationError
from marivo.contracts.generated import Dataset, Metric, Relationship, SemanticModel
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
                (model_id, name, expression, description, ai_context, additive_dimensions)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                metric_storage["model_id"],
                metric_storage["name"],
                metric_storage["expression"],
                metric_storage["description"],
                metric_storage["ai_context"],
                metric_storage["additive_dimensions"],
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
