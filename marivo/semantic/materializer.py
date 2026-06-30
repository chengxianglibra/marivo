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
from marivo.semantic.ir import (
    CsvSourceIR,
    EntityProvenance,
    MetricIR,
    ParquetSourceIR,
    TableSourceIR,
)
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

    Callers create fresh Materializer instances through resolver/runtime
    helpers; the backend_factory is never held on the project.
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
        self._backend_by_datasource: dict[str, IbisBackend] = {}
        self._entity_cache: dict[str, ibis.Table] = {}
        self._dimension_cache: dict[str, ir.Value] = {}
        self._measure_cache: dict[str, ir.Value] = {}
        self._metric_cache: dict[str, ir.Value] = {}

    # -- backend management ---------------------------------------------------

    def _get_backend(self, datasource_semantic_id: str) -> IbisBackend:
        """Get or create a backend for the given datasource."""
        if datasource_semantic_id not in self._backend_by_datasource:
            self._backend_by_datasource[datasource_semantic_id] = self._backend_factory(
                datasource_semantic_id
            )
        return self._backend_by_datasource[datasource_semantic_id]

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

        ds_ir = registry.entities.get(semantic_id)
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
        source: TableSourceIR | ParquetSourceIR | CsvSourceIR,
    ) -> ibis.Table:
        if isinstance(source, TableSourceIR):
            if source.database is None:
                return backend.table(source.table)
            return backend.table(source.table, database=source.database)

        if isinstance(source, ParquetSourceIR):
            reader = getattr(backend, "read_parquet", None)
            if reader is None:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    (
                        f"Entity {semantic_id!r} datasource backend does not support "
                        f"parquet file sources."
                    ),
                    cls=SemanticRuntimeError,
                    refs=(semantic_id,),
                    details={"source_kind": source.kind},
                )
            pq_kwargs: dict[str, object] = {}
            if source.hive_partitioning:
                pq_kwargs["hive_partitioning"] = source.hive_partitioning
            if source.columns is not None:
                pq_kwargs["columns"] = list(source.columns)
            return reader(source.path, **pq_kwargs)

        if isinstance(source, CsvSourceIR):
            reader = getattr(backend, "read_csv", None)
            if reader is None:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    (
                        f"Entity {semantic_id!r} datasource backend does not support "
                        f"csv file sources."
                    ),
                    cls=SemanticRuntimeError,
                    refs=(semantic_id,),
                    details={"source_kind": source.kind},
                )
            csv_kwargs: dict[str, object] = {}
            if not source.header:
                csv_kwargs["header"] = source.header
            if source.delimiter != ",":
                csv_kwargs["delimiter"] = source.delimiter
            if source.columns is not None:
                csv_kwargs["columns"] = list(source.columns)
            return reader(source.path, **csv_kwargs)

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

        field_ir = registry.dimensions.get(semantic_id)
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

        value = self._call_field_callable(semantic_id, field_ir.name, callable_, parent_table)

        self._dimension_cache[semantic_id] = value
        return value

    def dimension_on(self, semantic_id: str, table: ibis.Table) -> ir.Value:
        """Apply a dimension callable to a caller-supplied table without caching."""
        registry, sidecar = self._get_registry_and_sidecar()
        field_ir = registry.dimensions.get(semantic_id)
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
        return self._call_field_callable(semantic_id, field_ir.name, callable_, table)

    # -- measure ---------------------------------------------------------------

    def measure(self, semantic_id: str) -> ir.Value:
        """Materialize a measure, returning an ibis Value expression."""
        if semantic_id in self._measure_cache:
            return self._measure_cache[semantic_id]

        registry, sidecar = self._get_registry_and_sidecar()
        measure_ir = registry.measures.get(semantic_id)
        if measure_ir is None:
            _raise(
                ErrorKind.DIMENSION_NOT_FOUND,
                f"Measure {semantic_id!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )
        callable_ = sidecar.get(semantic_id)
        if callable_ is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Measure {semantic_id!r} has no sidecar callable.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )
        parent_table = self.entity(measure_ir.entity)
        value = self._call_field_callable(semantic_id, measure_ir.name, callable_, parent_table)
        self._measure_cache[semantic_id] = value
        return value

    def _call_field_callable(
        self,
        semantic_id: str,
        column_name: str,
        callable_: Callable[[ibis.Table], Any],
        table: ibis.Table,
    ) -> ir.Value:
        try:
            value = callable_(table)
        except NameError as exc:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Dimension {semantic_id!r} callable raised NameError: {exc}. "
                f"Ensure 'import ibis' is in the module where the decorator body is defined.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )
        except Exception as exc:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Dimension {semantic_id!r} callable raised: {exc}",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        if not isinstance(value, (ir.Value, ibis.Table)):
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Dimension {semantic_id!r} callable returned "
                f"{type(value).__name__!r} instead of an ibis expression. "
                f"This usually happens when a dimension name shadows an ibis "
                f"Table method. Use bracket notation: "
                f'table["{column_name}"] instead of table.{column_name}.',
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        return value

    # -- metric ---------------------------------------------------------------

    def metric(self, semantic_id: str) -> ir.Value:
        """Materialize a metric, returning an ibis Value expression.

        Handles tier-1 (aggregate), tier-2 (body), and derived metrics.
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

        if metric_ir.metric_type == "derived":
            value = self._materialize_derived_metric(semantic_id, metric_ir)
        elif metric_ir.aggregation is not None:
            value = self._materialize_tier1_metric(semantic_id, metric_ir, sidecar, registry)
        else:
            value = self._materialize_base_metric(semantic_id, metric_ir, sidecar, registry)

        self._metric_cache[semantic_id] = value
        return value

    def _materialize_tier1_metric(
        self,
        semantic_id: str,
        metric_ir: MetricIR,
        sidecar: Sidecar,
        registry: Registry,
    ) -> ir.Value:
        """Materialize a tier-1 metric over a measure, entity, or dimension."""
        target_kind = metric_ir.aggregation_target_kind or (
            "measure" if metric_ir.measure is not None else None
        )
        target_id = metric_ir.aggregation_target or metric_ir.measure
        if target_id is None or not metric_ir.entities:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Tier-1 metric {semantic_id!r} is missing target/entity.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )
        datasource_id = self._resolve_single_datasource(metric_ir, registry)
        backend_type = self._backend_type_for_datasource(datasource_id, registry)
        if target_kind == "entity":
            return self.entity(target_id).count()
        if target_kind == "measure":
            column = self.measure(target_id)
            return self._apply_agg(
                semantic_id,
                column,
                metric_ir.aggregation,
                backend_type=backend_type,
            )
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"Tier-1 metric {semantic_id!r} has unsupported target kind {target_kind!r}.",
            cls=SemanticRuntimeError,
            refs=(semantic_id,),
        )

    def _apply_agg(
        self,
        semantic_id: str,
        column: ir.Value,
        agg: Any,
        *,
        backend_type: str | None = None,
    ) -> ir.Value:
        agg_name = agg[0] if isinstance(agg, tuple) else agg
        if agg_name == "sum":
            return column.sum()
        if agg_name == "count":
            return column.count()
        if agg_name == "count_distinct":
            return column.nunique()
        if agg_name == "min":
            return column.min()
        if agg_name == "max":
            return column.max()
        if agg_name == "mean":
            return column.mean()
        if agg_name == "median":
            return column.median()
        if agg_name == "percentile":
            if backend_type == "trino":
                return column.approx_quantile(agg[1])
            return column.quantile(agg[1])
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"Metric {semantic_id!r} has unsupported aggregation {agg!r}.",
            cls=SemanticRuntimeError,
            refs=(semantic_id,),
        )

    def _materialize_base_metric(
        self,
        semantic_id: str,
        metric_ir: MetricIR,
        sidecar: Sidecar,
        registry: Registry,
    ) -> ir.Value:
        """Materialize a tier-2 body metric."""
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

        return self._call_metric_callable(semantic_id, callable_, tuple(tables))

    def metric_on(self, semantic_id: str, *tables: ibis.Table) -> ir.Value:
        """Apply a simple metric callable to caller-supplied tables without caching."""
        registry, sidecar = self._get_registry_and_sidecar()
        metric_ir = registry.metrics.get(semantic_id)
        if metric_ir is None:
            _raise(
                ErrorKind.METRIC_NOT_FOUND,
                f"Metric {semantic_id!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )
        if metric_ir.metric_type == "derived":
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Cannot apply derived metric {semantic_id!r} with metric_on(); "
                "drive its composition component-by-component.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )
        if len(tables) != len(metric_ir.entities):
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Metric {semantic_id!r} expects {len(metric_ir.entities)} tables, "
                f"got {len(tables)}.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
                details={
                    "expected_tables": len(metric_ir.entities),
                    "got_tables": len(tables),
                },
            )
        if metric_ir.aggregation is not None:
            datasource_id = self._resolve_single_datasource(metric_ir, registry)
            backend_type = self._backend_type_for_datasource(datasource_id, registry)
            target_kind = metric_ir.aggregation_target_kind or (
                "measure" if metric_ir.measure is not None else None
            )
            target_id = metric_ir.aggregation_target or metric_ir.measure
            if target_id is None:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    f"Tier-1 metric {semantic_id!r} has no aggregation target.",
                    cls=SemanticRuntimeError,
                    refs=(semantic_id,),
                )
            if target_kind == "entity":
                return tables[0].count()
            measure_ir = registry.measures.get(target_id)
            if measure_ir is None:
                _raise(
                    ErrorKind.DIMENSION_NOT_FOUND,
                    f"Measure {target_id!r} not found in registry.",
                    cls=SemanticRuntimeError,
                    refs=(target_id,),
                )
            measure_callable = sidecar.get(target_id)
            if measure_callable is None:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    f"Tier-1 metric {semantic_id!r} measure {target_id!r} has no sidecar callable.",
                    cls=SemanticRuntimeError,
                    refs=(semantic_id, target_id),
                )
            column = self._call_field_callable(
                target_id, measure_ir.name, measure_callable, tables[0]
            )
            return self._apply_agg(
                semantic_id,
                column,
                metric_ir.aggregation,
                backend_type=backend_type,
            )
        callable_ = sidecar.get(semantic_id)
        if callable_ is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Metric {semantic_id!r} has no sidecar callable.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )
        return self._call_metric_callable(semantic_id, callable_, tuple(tables))

    def _call_metric_callable(
        self,
        semantic_id: str,
        callable_: Callable[..., Any],
        tables: tuple[ibis.Table, ...],
    ) -> ir.Value:
        try:
            value = callable_(*tables)
        except NameError as exc:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Metric {semantic_id!r} callable raised NameError: {exc}. "
                f"Ensure 'import ibis' is in the module where the decorator body is defined.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )
        except Exception as exc:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Metric {semantic_id!r} callable raised: {exc}",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        if not isinstance(value, (ir.Value, ibis.Table)):
            col_name = semantic_id.rsplit(".", 1)[-1]
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Metric {semantic_id!r} callable returned "
                f"{type(value).__name__!r} instead of an ibis expression. "
                f"This usually happens when a column name shadows an ibis "
                f"Table method. Use bracket notation: "
                f'table["{col_name}"] instead of table.{col_name}.',
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        return value

    def _materialize_derived_metric(
        self,
        semantic_id: str,
        metric_ir: MetricIR,
    ) -> ir.Value:
        """Materialize a body-free derived metric scalar from its composition."""
        from marivo.semantic.ir import (
            LinearComposition,
            RatioComposition,
            WeightedAverageComposition,
        )

        comp = metric_ir.composition
        if isinstance(comp, RatioComposition):
            return self.metric(comp.numerator) / self.metric(comp.denominator)
        if isinstance(comp, WeightedAverageComposition):
            # Scalar form is value/weight; the weighted mix is applied in the
            # analysis component frame, not in the metric scalar.
            return self.metric(comp.value) / self.metric(comp.weight)
        if isinstance(comp, LinearComposition):
            terms = list(comp.terms)
            acc = self.metric(terms[0].metric)
            if terms[0].sign == "-":
                acc = -acc
            for term in terms[1:]:
                value = self.metric(term.metric)
                acc = acc + value if term.sign == "+" else acc - value
            return acc
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"Derived metric {semantic_id!r} has unsupported composition {type(comp).__name__!r}.",
            cls=SemanticRuntimeError,
            refs=(semantic_id,),
        )

    def _resolve_single_datasource(self, metric_ir: MetricIR, registry: Registry) -> str | None:
        """Return the metric datasource id, preserving cross-datasource enforcement."""
        if not metric_ir.entities:
            return None

        datasource_ids: set[str] = set()
        for entity_id in metric_ir.entities:
            entity_ir = registry.entities.get(entity_id)
            if entity_ir is not None:
                datasource_ids.add(entity_ir.datasource)

        if len(datasource_ids) > 1:
            _raise(
                ErrorKind.CROSS_DATASOURCE_NOT_SUPPORTED,
                f"Metric {metric_ir.semantic_id!r} references entities from "
                f"multiple datasources: {datasource_ids}. "
                "All entities in a metric must share the same datasource.",
                cls=SemanticRuntimeError,
                refs=(metric_ir.semantic_id,),
            )
        return next(iter(datasource_ids), None)

    def _backend_type_for_datasource(
        self, datasource_id: str | None, registry: Registry
    ) -> str | None:
        if datasource_id is None:
            return None
        datasource_ir = registry.datasources.get(datasource_id)
        if datasource_ir is None:
            return None
        return datasource_ir.backend_type

    def _check_single_datasource(self, metric_ir: MetricIR, registry: Registry) -> None:
        """All entities in a base metric must share the same datasource."""
        self._resolve_single_datasource(metric_ir, registry)
