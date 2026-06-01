"""Materializer for marivo.semantic v1.1.

Handles backend instantiation, dataset/field/metric materialization,
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
from marivo.semantic.authoring import _BinOpSentinel, _ComponentSentinel, _UnaryNegSentinel
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise
from marivo.semantic.ir import DatasetProvenance, FileSourceIR, MetricIR, TableSourceIR
from marivo.semantic.validator import Registry, Sidecar

__all__ = [
    "DatasetRuntimeMetadata",
    "Materializer",
]

# Type alias for an ibis backend (duckdb, etc.)
IbisBackend = Any  # ibis backends don't share a common typing protocol yet


@dataclass(frozen=True)
class DatasetRuntimeMetadata:
    """Runtime metadata detected after materializing a dataset.

    Stored on SemanticProject._runtime_metadata, not in frozen IR.
    """

    dataset_provenance: DatasetProvenance
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
    ) -> None:
        self._project = project
        self._backend_factory = backend_factory
        self._backend_cache: dict[str, IbisBackend] = {}
        self._dataset_cache: dict[str, ibis.Table] = {}
        self._field_cache: dict[str, ir.Value] = {}
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
        registry = self._project.registry()
        sidecar = self._project.sidecar()

        if registry is None or sidecar is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "Cannot materialize: project is not loaded.",
                cls=SemanticRuntimeError,
            )
        return registry, sidecar

    # -- dataset --------------------------------------------------------------

    def dataset(self, semantic_id: str) -> ibis.Table:
        """Materialize a dataset, returning an ibis Table expression."""
        if semantic_id in self._dataset_cache:
            return self._dataset_cache[semantic_id]

        registry, _sidecar = self._get_registry_and_sidecar()

        ds_ir = registry.datasets.get(semantic_id)
        if ds_ir is None:
            _raise(
                ErrorKind.METRIC_NOT_FOUND,
                f"Dataset {semantic_id!r} not found in registry.",
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
                f"Dataset {semantic_id!r} source materialization raised: {exc}",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        # Cache the result
        self._dataset_cache[semantic_id] = table

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
                        f"Dataset {semantic_id!r} datasource backend does not support "
                        f"{source.format} file sources."
                    ),
                    cls=SemanticRuntimeError,
                    refs=(semantic_id,),
                    details={"source_kind": source.kind, "format": source.format},
                )
            return reader(source.path, **source.options)

        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"Dataset {semantic_id!r} has unsupported source kind.",
            cls=SemanticRuntimeError,
            refs=(semantic_id,),
        )

    def _detect_and_store_provenance(self, semantic_id: str, table: ibis.Table) -> None:
        """Walk the ibis expression tree to detect SQL views and store metadata."""
        op = table.op()
        sql_nodes = op.find(lambda n: isinstance(n, SQLQueryResult))

        if sql_nodes:
            provenance = DatasetProvenance.SQL_VIEW
            raw_sql = sql_nodes[0].query
        else:
            provenance = DatasetProvenance.IBIS_TABLE
            raw_sql = None

        meta = DatasetRuntimeMetadata(
            dataset_provenance=provenance,
            raw_sql_snippet=raw_sql,
            detected_at=datetime.now(tz=UTC),
        )
        self._project._runtime_metadata[semantic_id] = meta

    # -- field ----------------------------------------------------------------

    def field(self, semantic_id: str) -> ir.Value:
        """Materialize a field, returning an ibis Value expression."""
        if semantic_id in self._field_cache:
            return self._field_cache[semantic_id]

        registry, sidecar = self._get_registry_and_sidecar()

        field_ir = registry.fields.get(semantic_id)
        if field_ir is None:
            _raise(
                ErrorKind.METRIC_NOT_FOUND,
                f"Field {semantic_id!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        callable_ = sidecar.get(semantic_id)
        if callable_ is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Field {semantic_id!r} has no sidecar callable.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        # Materialize parent dataset first
        parent_table = self.dataset(field_ir.dataset)

        # Call the sidecar callable with the parent table
        try:
            value = callable_(parent_table)
        except Exception as exc:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Field {semantic_id!r} callable raised: {exc}",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        self._field_cache[semantic_id] = value
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
            value = self._materialize_derived_metric(semantic_id, metric_ir, sidecar)
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

        # Cross-datasource check: all datasets must share the same datasource
        self._check_single_datasource(metric_ir, registry)

        # Materialize all datasets in order
        tables: list[ibis.Table] = []
        for ds_ref in metric_ir.datasets:
            table = self.dataset(ds_ref)
            tables.append(table)

        # Call the sidecar callable with dataset tables as positional args
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
        sidecar: Sidecar,
    ) -> ir.Value:
        """Materialize a derived metric by walking the sentinel tree."""
        sentinel_tree = sidecar.get(semantic_id)
        if sentinel_tree is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Derived metric {semantic_id!r} has no sentinel tree in sidecar.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        return self._eval_sentinel(sentinel_tree, metric_ir)

    def _eval_sentinel(
        self,
        node: Any,
        metric_ir: MetricIR,
    ) -> ir.Value:
        """Recursively evaluate a sentinel tree into an ibis expression."""
        if isinstance(node, _ComponentSentinel):
            # Look up the component metric's semantic_id from decomposition
            component_metric_id = metric_ir.decomposition.components.get(node.name)
            if component_metric_id is None:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    f"Component {node.name!r} not found in decomposition of "
                    f"metric {metric_ir.semantic_id!r}.",
                    cls=SemanticRuntimeError,
                    refs=(metric_ir.semantic_id,),
                )
            # Recursively materialize the component metric
            return self.metric(component_metric_id)

        if isinstance(node, _BinOpSentinel):
            left = self._eval_sentinel_or_literal(node.left, metric_ir)
            right = self._eval_sentinel_or_literal(node.right, metric_ir)
            if node.op == "+":
                return left + right
            elif node.op == "-":
                return left - right
            elif node.op == "*":
                return left * right
            elif node.op == "/":
                return left / right
            else:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    f"Unsupported binary operator {node.op!r} in derived metric.",
                    cls=SemanticRuntimeError,
                    refs=(metric_ir.semantic_id,),
                )

        if isinstance(node, _UnaryNegSentinel):
            operand = self._eval_sentinel(node.operand, metric_ir)
            return -operand

        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"Unexpected sentinel node type {type(node).__name__} in derived metric.",
            cls=SemanticRuntimeError,
            refs=(metric_ir.semantic_id,),
        )

    def _eval_sentinel_or_literal(
        self,
        node: Any,
        metric_ir: MetricIR,
    ) -> ir.Value:
        """Evaluate a sentinel node or a numeric literal into an ibis expression."""
        if isinstance(node, (int, float)):
            return ibis.literal(node)
        return self._eval_sentinel(node, metric_ir)

    def _check_single_datasource(self, metric_ir: MetricIR, registry: Any) -> None:
        """All datasets in a base metric must share the same datasource."""
        if not metric_ir.datasets:
            return

        datasource_ids: set[str] = set()
        for ds_ref in metric_ir.datasets:
            ds_ir = registry.datasets.get(ds_ref)
            if ds_ir is not None:
                datasource_ids.add(ds_ir.datasource)

        if len(datasource_ids) > 1:
            _raise(
                ErrorKind.CROSS_DATASOURCE_NOT_SUPPORTED,
                f"Metric {metric_ir.semantic_id!r} references datasets from "
                f"multiple datasources: {datasource_ids}. "
                "All datasets in a metric must share the same datasource.",
                cls=SemanticRuntimeError,
                refs=(metric_ir.semantic_id,),
            )
