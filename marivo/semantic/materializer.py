"""Materializer for marivo.semantic v1.1.

Binds physical Entity and field expressions for bounded semantic reads.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import ibis
import ibis.expr.types as ir
from ibis.expr.operations.relations import Relation, SQLQueryResult

from marivo._compat import UTC
from marivo.datasource import credentials as cr
from marivo.datasource.errors import DatasourceError
from marivo.datasource.ir import DatasourceIR, QueryParamScalar, QueryParamScalarList
from marivo.datasource.json_source import read_json_source
from marivo.datasource.source import AuthoringScope, PartitionScope
from marivo.datasource.table_source import table_source_expression
from marivo.refs import EntityKind, Ref, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.semantic._expression_binding import (
    CompiledExpressionSidecar,
    evaluate_expression_body,
)
from marivo.semantic._physical_contracts import validate_bound_field
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise
from marivo.semantic.ir import (
    CsvSourceIR,
    EntityIR,
    EntityProvenance,
    EntitySourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    TableSourceIR,
)
from marivo.semantic.typing import IbisBackend
from marivo.semantic.validator import Registry

__all__ = [
    "EntityRuntimeMetadata",
    "Materializer",
]


@dataclass(frozen=True)
class EntityRuntimeMetadata:
    """Runtime metadata detected after materializing an entity.

    Stored on SemanticProject._runtime_metadata, not in frozen IR.
    """

    entity_provenance: EntityProvenance
    raw_sql_snippet: str | None
    detected_at: datetime


def _relations_outside_boundary(op: object, boundary: object) -> list[object]:
    """Return the relation leaves an expression reaches around the boundary.

    Traverses the op tree from ``op`` downward and stops at the boundary
    node: the boundary and everything beneath it belong to the injected
    Source and is trusted. A relation leaf reached on a path that never
    passes through the boundary is reported, which covers captured
    memtables, detached tables, file readers, and op-level navigation that
    re-emerges above the boundary (for example ``raw.op().parent``).
    Derived non-leaf relation nodes (joins, aggregations, unions) are
    traversed, so same-source branches stay allowed.
    """
    relation_type = Relation
    outside: list[object] = []
    seen: set[int] = set()
    stack: list[object] = [op]
    while stack:
        node = stack.pop()
        node_id = id(node)
        if node_id in seen:
            continue
        seen.add(node_id)
        if node is boundary:
            continue
        children = list(getattr(node, "__children__", ()))
        if relation_type in type(node).__mro__ and not any(
            relation_type in type(child).__mro__ for child in children
        ):
            outside.append(node)
            continue
        stack.extend(children)
    return outside


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
        entity_scopes: Mapping[str, AuthoringScope] | None = None,
        source_bindings: (
            Mapping[str, Mapping[str, QueryParamScalar | QueryParamScalarList]] | None
        ) = None,
        source_binder: (
            Callable[
                [
                    str,
                    str,
                    DatasourceIR,
                    EntitySourceIR,
                    dict[str, QueryParamScalar | QueryParamScalarList] | None,
                ],
                ir.Table,
            ]
            | None
        ) = None,
    ) -> None:
        self._project = project
        self._backend_factory = backend_factory
        self._sample_size = sample_size
        self._entity_scopes = dict(entity_scopes or {})
        self._source_bindings = {
            entity_id: dict(params) for entity_id, params in (source_bindings or {}).items()
        }
        self._source_binder = source_binder
        self._backend_by_datasource: dict[str, IbisBackend] = {}
        self._entity_cache: dict[str, ibis.Table] = {}
        self._dimension_cache: dict[str, ir.Value] = {}
        self._measure_cache: dict[str, ir.Value] = {}

    # -- backend management ---------------------------------------------------

    def _get_backend(self, datasource_semantic_id: str) -> IbisBackend:
        """Get or create a backend for the given datasource."""
        return self._backend_factory(datasource_semantic_id)

    def _get_registry_and_sidecar(self) -> tuple[Registry, CompiledExpressionSidecar]:
        """Get registry and sidecar, raising if project is not loaded."""
        if self._project is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "Cannot materialize: project is not loaded.",
                cls=SemanticRuntimeError,
            )
        registry = self._project._registry
        sidecar = self._project._expression_sidecar

        if registry is None or sidecar is None:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "Cannot materialize: project is not loaded.",
                cls=SemanticRuntimeError,
            )
        return registry, sidecar

    def _evaluate_expression(
        self,
        owning_ref: Ref[SemanticKindTag],
        entity_refs: tuple[Ref[EntityKind], ...],
        aliases: tuple[ibis.Table, ...],
        *,
        table_result: bool = False,
    ) -> ir.Value:
        """Evaluate one compiled body through the task-local binding runtime."""
        registry, sidecar = self._get_registry_and_sidecar()
        body = sidecar.bodies.get(owning_ref)
        if body is None:
            _raise(
                ErrorKind.BINDING_TARGET_MISSING,
                f"Ref {owning_ref.key!r} has no compiled expression body.",
                cls=SemanticRuntimeError,
                refs=(owning_ref.key,),
            )
        compiled_state = self._project._compiled_state
        if compiled_state is None:
            raise RuntimeError("semantic project has no immutable compiled state")
        result = evaluate_expression_body(
            catalog_definition_fingerprint=compiled_state.definition_fingerprint,
            expression_sidecar=sidecar,
            owning_ref=owning_ref,
            body=body,
            entity_refs=entity_refs,
            aliases=aliases,
            table_result=table_result,
        )
        validate_bound_field(result, ref=owning_ref, registry=registry)
        return result

    @staticmethod
    def _dimension_ref(semantic_id: str, *, is_time_dimension: bool) -> Ref[SemanticKindTag]:
        if is_time_dimension:
            return cast("Ref[SemanticKindTag]", ref_factory.time_dimension(semantic_id))
        return cast("Ref[SemanticKindTag]", ref_factory.dimension(semantic_id))

    # -- entity --------------------------------------------------------------

    def entity(self, semantic_id: str) -> ibis.Table:
        """Materialize an entity through the shared output-relation boundary.

        Resolves the declared physical Source, applies an explicitly requested
        input scope over Source-exposed columns, evaluates the expression
        body once (identity for direct declarations), verifies the single
        Source boundary, and then applies output row budgets to the Entity
        relation.
        """
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
            if self._source_binder is None:
                source_table = self._materialize_dataset_source(semantic_id, backend, ds_ir.source)
            else:
                source_table = self._source_binder(
                    ds_ir.datasource,
                    semantic_id,
                    registry.datasources[ds_ir.datasource],
                    ds_ir.source,
                    self._source_bindings.get(semantic_id),
                )
        except DatasourceError:
            raise
        except SemanticRuntimeError:
            raise
        except Exception as exc:
            values = cr.injected_values(backend)
            message = cr.redact(str(exc), values)
            try:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    f"Entity {semantic_id!r} source materialization raised: {message}",
                    cls=SemanticRuntimeError,
                    refs=(semantic_id,),
                )
            except SemanticRuntimeError as error:
                if values:
                    raise error from None
                raise

        # Explicit physical input scope over Source-exposed columns, before
        # any authored Entity body. Its row budget stays with the scope.
        source_table = self._apply_input_scope(semantic_id, source_table)

        table = self._materialize_entity_output(semantic_id, ds_ir, source_table)

        # Row budgets bound Entity output, after the authored relation, so a
        # full aggregate or deduplication completes before truncation.
        scope = self._entity_scopes.get(semantic_id)
        if scope is not None:
            # Preview needs one extra row to distinguish an exhaustive scope
            # from a bounded sample without performing another query.
            table = table.limit(scope.max_rows + 1)
        if self._sample_size is not None:
            table = table.limit(self._sample_size)

        # Cache the result
        self._entity_cache[semantic_id] = table

        self._detect_and_store_provenance(semantic_id, ds_ir.source, source_table)

        return table

    def _apply_input_scope(self, semantic_id: str, source_table: ibis.Table) -> ibis.Table:
        """Filter the physical Source by an explicitly requested input scope."""
        scope = self._entity_scopes.get(semantic_id)
        if scope is None or not isinstance(scope, PartitionScope):
            return source_table
        if scope._time_range is not None:
            predicate = scope._time_range
            return source_table.filter(
                (source_table[predicate.column] >= predicate.start)
                & (source_table[predicate.column] < predicate.end)
            )
        filtered = source_table
        for column, value in scope.values:
            filtered = filtered.filter(filtered[column] == value)
        return filtered

    def _materialize_entity_output(
        self,
        semantic_id: str,
        ds_ir: EntityIR,
        source_table: ibis.Table,
    ) -> ibis.Table:
        registry, sidecar = self._get_registry_and_sidecar()
        compiled_state = self._project._compiled_state
        if compiled_state is None:
            raise RuntimeError("semantic project has no immutable compiled state")
        return materialize_entity_output(
            registry=registry,
            sidecar=sidecar,
            entity=ds_ir,
            source_table=source_table,
            catalog_definition_fingerprint=compiled_state.definition_fingerprint,
        )

    @staticmethod
    def _require_output_keys(
        *,
        semantic_id: str,
        ds_ir: EntityIR,
        result: ibis.Table,
    ) -> None:
        """Require declared primary-key components on the output schema.

        The output schema is authoritative for downstream consumers: keys,
        versioning axes, and fields must resolve against it, not against the
        physical Source schema. The check reports the declared keys and the
        bounded actual output columns so the author can either expose the
        missing key or update the declaration to the intended grain.
        """
        if not ds_ir.primary_key:
            return
        output_columns = tuple(result.schema().names)
        missing = tuple(key for key in ds_ir.primary_key if key not in output_columns)
        if not missing:
            return
        display_limit = 8
        rendered_keys = ", ".join(repr(key) for key in missing)
        rendered_columns = ", ".join(repr(column) for column in output_columns[:display_limit])
        omitted = len(output_columns) - min(len(output_columns), display_limit)
        if omitted > 0:
            rendered_columns += f", ... (+{omitted} more)"
        _raise(
            ErrorKind.BINDING_RESULT_INVALID,
            (
                f"Entity {semantic_id!r} output is missing declared primary_key "
                f"component(s) {rendered_keys}. The output schema is authoritative "
                f"for downstream keys and fields."
            ),
            cls=SemanticRuntimeError,
            refs=(semantic_id, *(f"{semantic_id}.{key}" for key in missing)),
            expected=f"primary_key {rendered_keys} present on the Entity output schema",
            received=f"output columns: {rendered_columns}",
            hint=(
                "Expose the declared key column(s) in the Entity body output, or "
                "update primary_key to columns that identify the transformed grain."
            ),
        )

    @staticmethod
    def _require_single_source_boundary(
        *,
        semantic_id: str,
        source_table: ibis.Table,
        result: ibis.Table,
    ) -> None:
        """Verify the output derives only from the injected Source boundary.

        The injected Source relation is the trusted, opaque boundary of the
        traversal: every relation leaf the output reaches must be reached
        through that node, so same-source branches, self-joins, and unions
        stay allowed while captured, detached, or additional relation roots
        are rejected even when their names or schemas coincide. Walking above
        the boundary (for example ``raw.op().parent``) would expose the
        unscoped physical relation beneath an explicit input scope and is
        rejected the same way. Internal nodes of the datasource-owned Source
        preparation (typed projections, file readers) are encapsulated by the
        boundary and never inspected separately.
        """
        boundary = source_table.op()
        foreign = _relations_outside_boundary(result.op(), boundary)
        if foreign:
            _raise(
                ErrorKind.SQL_ESCAPE_HATCH,
                (
                    f"Entity {semantic_id!r} body returned a relation reaching "
                    f"{len(foreign)} relation node(s) outside the injected Source "
                    f"boundary ({', '.join(sorted({type(leaf).__name__ for leaf in foreign}))}). "
                    "Derive the Entity output from the single injected Source parameter."
                ),
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
                expected="one relation derived only through the injected Source boundary",
                received=", ".join(sorted({type(leaf).__name__ for leaf in foreign})),
                hint=(
                    "Replace captured Tables, ibis.table(...), ibis.memtable(...), file "
                    "readers, and op-level navigation such as raw.op().parent with "
                    "expressions over the injected parameter; same-source branches and "
                    "self-joins remain allowed."
                ),
            )

    def _materialize_dataset_source(
        self,
        semantic_id: str,
        backend: IbisBackend,
        source: EntitySourceIR,
    ) -> ibis.Table:
        if isinstance(source, TableSourceIR):
            return table_source_expression(backend, source)

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
            table = reader(source.path, **csv_kwargs)
            if source.columns:
                table = table.select(
                    *(
                        table[source_name].name(output_name)
                        for output_name, source_name in source.columns
                    )
                )
            return table

        if isinstance(source, JsonSourceIR):
            reader = getattr(backend, "read_json", None)
            is_http = source.path.lower().startswith(("http://", "https://"))
            can_register_arrow = callable(getattr(backend, "create_table", None))
            if (not is_http and not callable(reader)) or (is_http and not can_register_arrow):
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    (
                        f"Entity {semantic_id!r} datasource backend does not support "
                        f"json file sources."
                    ),
                    cls=SemanticRuntimeError,
                    refs=(semantic_id,),
                    details={"source_kind": source.kind},
                )
            return read_json_source(
                backend,
                source,
                source_params=self._source_bindings.get(semantic_id),
            )

        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"Entity {semantic_id!r} has unsupported source kind.",
            cls=SemanticRuntimeError,
            refs=(semantic_id,),
        )

    def _detect_and_store_provenance(
        self,
        semantic_id: str,
        source: EntitySourceIR,
        source_table: ibis.Table,
    ) -> None:
        """Classify physical-Source provenance and store metadata.

        Provenance describes the declared physical origin, not the authored
        Entity transformation: an expression Entity over a plain table keeps
        the table classification even though its output relation is derived.
        """
        op = source_table.op()
        sql_nodes = op.find(lambda n: isinstance(n, SQLQueryResult))

        if isinstance(source, TableSourceIR) and source.columns:
            provenance = EntityProvenance.TABLE_PROJECTION
            raw_sql = None
        elif sql_nodes:
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

        registry, _sidecar = self._get_registry_and_sidecar()

        field_ir = registry.dimensions.get(semantic_id)
        if field_ir is None:
            _raise(
                ErrorKind.DIMENSION_NOT_FOUND,
                f"Dimension {semantic_id!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )

        # Materialize parent entity first
        parent_table = self.entity(field_ir.entity)
        value = self._evaluate_expression(
            self._dimension_ref(
                semantic_id,
                is_time_dimension=field_ir.is_time_dimension,
            ),
            (ref_factory.entity(field_ir.entity),),
            (parent_table,),
        )

        self._dimension_cache[semantic_id] = value
        return value

    def dimension_on(self, semantic_id: str, table: ibis.Table) -> ir.Value:
        """Apply a dimension callable to a caller-supplied table without caching."""
        registry, _sidecar = self._get_registry_and_sidecar()
        field_ir = registry.dimensions.get(semantic_id)
        if field_ir is None:
            _raise(
                ErrorKind.DIMENSION_NOT_FOUND,
                f"Dimension {semantic_id!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )
        return self._evaluate_expression(
            self._dimension_ref(
                semantic_id,
                is_time_dimension=field_ir.is_time_dimension,
            ),
            (ref_factory.entity(field_ir.entity),),
            (table,),
        )

    # -- measure ---------------------------------------------------------------

    def measure(self, semantic_id: str) -> ir.Value:
        """Materialize a measure, returning an ibis Value expression."""
        if semantic_id in self._measure_cache:
            return self._measure_cache[semantic_id]

        registry, _sidecar = self._get_registry_and_sidecar()
        measure_ir = registry.measures.get(semantic_id)
        if measure_ir is None:
            _raise(
                ErrorKind.DIMENSION_NOT_FOUND,
                f"Measure {semantic_id!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )
        parent_table = self.entity(measure_ir.entity)
        value = self._evaluate_expression(
            cast("Ref[SemanticKindTag]", ref_factory.measure(semantic_id)),
            (ref_factory.entity(measure_ir.entity),),
            (parent_table,),
        )
        self._measure_cache[semantic_id] = value
        return value

    def measure_on(self, semantic_id: str, table: ibis.Table) -> ir.Value:
        """Apply a measure callable to a caller-supplied table without caching.

        Unlike ``measure``, this does not materialize or cache the parent
        entity table; the caller supplies the table to evaluate against.
        Used by current Metric preview lowering on its scoped parent table.
        """
        registry, _sidecar = self._get_registry_and_sidecar()
        measure_ir = registry.measures.get(semantic_id)
        if measure_ir is None:
            _raise(
                ErrorKind.DIMENSION_NOT_FOUND,
                f"Measure {semantic_id!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(semantic_id,),
            )
        return self._evaluate_expression(
            cast("Ref[SemanticKindTag]", ref_factory.measure(semantic_id)),
            (ref_factory.entity(measure_ir.entity),),
            (table,),
        )


def materialize_entity_output(
    *,
    registry: Registry,
    sidecar: CompiledExpressionSidecar | None,
    entity: EntityIR,
    source_table: ibis.Table,
    catalog_definition_fingerprint: str,
) -> ibis.Table:
    """Resolve one Entity relation over its exact governed Source boundary."""
    owning_ref = ref_factory.entity(entity.semantic_id)
    body = sidecar.bodies.get(owning_ref) if sidecar is not None else None
    if body is None or sidecar is None:
        return source_table
    result = evaluate_expression_body(
        catalog_definition_fingerprint=catalog_definition_fingerprint,
        expression_sidecar=sidecar,
        owning_ref=owning_ref,
        body=body,
        entity_refs=(owning_ref,),
        aliases=(source_table,),
        table_result=True,
    )
    if not isinstance(result, ibis.Table):
        raise TypeError("Entity expression must return an Ibis Table")
    Materializer._require_single_source_boundary(
        semantic_id=entity.semantic_id, source_table=source_table, result=result
    )
    Materializer._require_output_keys(semantic_id=entity.semantic_id, ds_ir=entity, result=result)
    from marivo.semantic.validator import _expression_entity_output_schema_errors

    errors = _expression_entity_output_schema_errors(
        registry=registry,
        sidecar=sidecar,
        entity=entity,
        output_columns=tuple(result.columns),
    )
    if errors:
        raise errors[0]
    return result
