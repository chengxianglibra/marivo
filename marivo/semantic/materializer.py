"""Materializer for marivo.semantic v1.1.

Handles backend instantiation, entity/dimension/metric materialization,
SQL view detection, and cross-datasource enforcement.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import ibis
import ibis.expr.types as ir
from ibis.expr.operations.relations import SQLQueryResult

from marivo.datasource.errors import DatasourceConfigError
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise
from marivo.semantic.ir import EntityProvenance, FileSourceIR, MetricIR, TableSourceIR
from marivo.semantic.validator import Registry, Sidecar

__all__ = [
    "EntityRuntimeMetadata",
    "Materializer",
]

# Type alias for an ibis backend (duckdb, etc.)
IbisBackend = Any  # ibis backends don't share a common typing protocol yet


@dataclass(frozen=True)
class EntityRuntimeMetadata:
    """Runtime metadata detected after materializing an entity.

    Stored on SemanticProject._runtime_metadata, not in frozen IR.
    """

    entity_provenance: EntityProvenance
    raw_sql_snippet: str | None
    detected_at: datetime


class Materializer:
    """Materializes semantic objects against an ibis backend.

    Each ``project.materialize_*`` call creates a fresh Materializer
    instance; the backend_factory is never held on the project.
    """

    def __init__(
        self,
        project: Any,
        backend_factory: Callable[[str], IbisBackend],
        *,
        sample_size: int | None = None,
    ) -> None:
        self._project = project
        self._backend_factory = backend_factory
        self._sample_size = sample_size
        self._backend_cache: dict[str, IbisBackend] = {}
        self._entity_cache: dict[str, ibis.Table] = {}
        self._dimension_cache: dict[str, ir.Value] = {}
        self._metric_cache: dict[str, ir.Value] = {}

    # -- backend management ---------------------------------------------------

    def _get_backend(self, datasource_semantic_id: str) -> IbisBackend:
        """Get or create a backend for the given datasource."""
        if datasource_semantic_id not in self._backend_cache:
            self._backend_cache[datasource_semantic_id] = self._backend_factory(
                datasource_semantic_id
            )
        return self._backend_cache[datasource_semantic_id]

    def _get_registry_and_sidecar(self) -> tuple[Registry, Sidecar]:
        """Get registry and sidecar, raising if project is not loaded."""
        if self._project is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "Cannot materialize: project is not loaded.",
                cls=SemanticRuntimeError,
            )
        registry = self._project._registry
        sidecar = self._project._sidecar

        if registry is None or sidecar is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "Cannot materialize: project is not loaded.",
                cls=SemanticRuntimeError,
            )
        return registry, sidecar

    # -- entity --------------------------------------------------------------

    def entity(self, semantic_id: str) -> ibis.Table:
        """Materialize an entity, returning an ibis Table expression."""
        if semantic_id in self._entity_cache:
            return self._entity_cache[semantic_id]

        registry, _sidecar = self._get_registry_and_sidecar()

        ds_ir = registry.datasets.get(semantic_id)  # Registry key still "datasets"
        if ds_ir is None:
            _raise(
                ErrorKind.ENTITY_NOT_FOUND,
                f"Entity {semantic_id!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        backend = self._get_backend(ds_ir.datasource)

        try:
            table = self._materialize_dataset_source(semantic_id, backend, ds_ir.source)
        except DatasourceConfigError:
            raise
        except SemanticRuntimeError:
            raise
        except Exception as exc:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Entity {semantic_id!r} source materialization raised: {exc}",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        # Apply pre-aggregate row limit when sample_size is set
        if self._sample_size is not None:
            table = table.limit(self._sample_size)

        # Cache the result
        self._entity_cache[semantic_id] = table

        # Detect SQL view provenance
        self._detect_and_store_provenance(semantic_id, table)

        return table

    def _materialize_dataset_source(
        self,
        semantic_id: str,
        backend: IbisBackend,
        source: TableSourceIR | FileSourceIR,
    ) -> ibis.Table:
        if isinstance(source, TableSourceIR):
            if source.database is None:
                return backend.table(source.table)
            return backend.table(source.table, database=source.database)

        if isinstance(source, FileSourceIR):
            reader_name = "read_parquet" if source.format == "parquet" else "read_csv"
            reader = getattr(backend, reader_name, None)
            if reader is None:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    (
                        f"Entity {semantic_id!r} datasource backend does not support "
                        f"{source.format} file sources."
                    ),
                    cls=SemanticRuntimeError,
                    refs=(semantic_id,),
                    details={"source_kind": source.kind, "format": source.format},
                )
            return reader(source.path, **source.options)

        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"Entity {semantic_id!r} has unsupported source kind.",
            cls=SemanticRuntimeError,
            refs=(semantic_id,),
        )

    def _detect_and_store_provenance(self, semantic_id: str, table: ibis.Table) -> None:
        """Walk the ibis expression tree to detect SQL views and store metadata."""
        op = table.op()
        sql_nodes = op.find(lambda n: isinstance(n, SQLQueryResult))

        if sql_nodes:
            provenance = EntityProvenance.SQL_VIEW
            raw_sql = sql_nodes[0].query
        else:
            provenance = EntityProvenance.IBIS_TABLE
            raw_sql = None

        meta = EntityRuntimeMetadata(
            entity_provenance=provenance,
            raw_sql_snippet=raw_sql,
            detected_at=datetime.now(tz=UTC),
        )
        self._project._runtime_metadata[semantic_id] = meta

    # -- dimension ---------------------------------------------------------------

    def dimension(self, semantic_id: str) -> ir.Value:
        """Materialize a dimension, returning an ibis Value expression."""
        if semantic_id in self._dimension_cache:
            return self._dimension_cache[semantic_id]

        registry, sidecar = self._get_registry_and_sidecar()

        field_ir = registry.fields.get(semantic_id)  # Registry key still "fields"
        if field_ir is None:
            _raise(
                ErrorKind.DIMENSION_NOT_FOUND,
                f"Dimension {semantic_id!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        callable_ = sidecar.get(semantic_id)
        if callable_ is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Dimension {semantic_id!r} has no sidecar callable.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        # Materialize parent entity first
        parent_table = self.entity(field_ir.entity)

        # Call the sidecar callable with the parent table
        try:
            value = callable_(parent_table)
        except Exception as exc:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Dimension {semantic_id!r} callable raised: {exc}",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        self._dimension_cache[semantic_id] = value
        return value

    # -- metric ---------------------------------------------------------------

    def metric(self, semantic_id: str) -> ir.Value:
        """Materialize a metric, returning an ibis Value expression.

        Handles both base and derived metrics.
        """
        if semantic_id in self._metric_cache:
            return self._metric_cache[semantic_id]

        registry, sidecar = self._get_registry_and_sidecar()

        metric_ir = registry.metrics.get(semantic_id)
        if metric_ir is None:
            _raise(
                ErrorKind.METRIC_NOT_FOUND,
                f"Metric {semantic_id!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        if metric_ir.is_derived:
            value = self._materialize_derived_metric(semantic_id, metric_ir)
        else:
            value = self._materialize_base_metric(semantic_id, metric_ir, sidecar, registry)

        self._metric_cache[semantic_id] = value
        return value

    def _materialize_base_metric(
        self,
        semantic_id: str,
        metric_ir: MetricIR,
        sidecar: Sidecar,
        registry: Registry,
    ) -> ir.Value:
        """Materialize a base (non-derived) metric."""
        callable_ = sidecar.get(semantic_id)
        if callable_ is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Metric {semantic_id!r} has no sidecar callable.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        # Cross-datasource check: all entities must share the same datasource
        self._check_single_datasource(metric_ir, registry)

        # Materialize all entities in order
        tables: list[ibis.Table] = []
        for ds_ref in metric_ir.entities:
            table = self.entity(ds_ref)
            tables.append(table)

        # Call the sidecar callable with entity tables as positional args
        try:
            value = callable_(*tables)
        except Exception as exc:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Metric {semantic_id!r} callable raised: {exc}",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        return value

    def _materialize_derived_metric(
        self,
        semantic_id: str,
        metric_ir: MetricIR,
    ) -> ir.Value:
        """Materialize a body-free derived metric from decomposition components."""
        components = metric_ir.decomposition.components
        if metric_ir.decomposition.kind == "ratio":
            numerator = components.get("numerator")
            denominator = components.get("denominator")
            if numerator is None or denominator is None:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    f"Derived metric {semantic_id!r} ratio decomposition is missing components.",
                    cls=SemanticRuntimeError,
                    refs=(semantic_id,),
                )
            return self.metric(numerator) / self.metric(denominator)

        if metric_ir.decomposition.kind == "weighted_average":
            numerator = components.get("numerator")
            weight = components.get("weight")
            if numerator is None or weight is None:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    f"Derived metric {semantic_id!r} weighted_average decomposition is missing components.",
                    cls=SemanticRuntimeError,
                    refs=(semantic_id,),
                )
            return self.metric(numerator) / self.metric(weight)

        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"Derived metric {semantic_id!r} has unsupported decomposition kind "
            f"{metric_ir.decomposition.kind!r}.",
            cls=SemanticRuntimeError,
            refs=(semantic_id,),
        )

    def _check_single_datasource(self, metric_ir: MetricIR, registry: Any) -> None:
        """All entities in a base metric must share the same datasource."""
        if not metric_ir.entities:
            return

        datasource_ids: set[str] = set()
        for ds_ref in metric_ir.entities:
            ds_ir = registry.datasets.get(ds_ref)  # Registry key still "datasets"
            if ds_ir is not None:
                datasource_ids.add(ds_ir.datasource)

        if len(datasource_ids) > 1:
            _raise(
                ErrorKind.CROSS_DATASOURCE_NOT_SUPPORTED,
                f"Metric {metric_ir.semantic_id!r} references entities from "
                f"multiple datasources: {datasource_ids}. "
                "All entities in a metric must share the same datasource.",
                cls=SemanticRuntimeError,
                refs=(metric_ir.semantic_id,),
            )
