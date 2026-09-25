"""Resolve admitted DSL source tables through the project's datasource authority."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager

import ibis.expr.types as ir

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.dsl_j1_artifact import J1Node, _context
from marivo.analysis.materialization.source_stage import J1IbisBackend
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.table_source import table_source_expression
from marivo.semantic.ir import TableSourceIR
from marivo.semantic.metric_graph_lowering import normalize_target_metric
from marivo.semantic.validator import normalize_target_entity


def _invalid(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="one admitted DuckDB datasource and table-backed Entity closure",
        received=received,
        repair="Use governed table-backed Entities on one configured DuckDB datasource.",
        location="analysis.source",
    )


def _entity_paths(node: J1Node) -> tuple[str, ...]:
    context = _context(node)
    found: set[str] = set()
    seen: set[LogicalRootHandle] = set()

    def visit(root: LogicalRootHandle) -> None:
        if root in seen:
            return
        seen.add(root)
        params = root.parameters
        if not isinstance(params, tuple):
            raise _invalid("invalid source definition")
        if root.operator_id == "dsl.j1.members":
            if not params or not isinstance(params[0], str):
                raise _invalid("member Entity is missing")
            found.add(params[0])
        if root.operator_id in ("dsl.j1.observe", "dsl.j1.ratio_observe"):
            if not params or not isinstance(params[0], str):
                raise _invalid("Metric input is missing")
            metric = normalize_target_metric(context.registry, params[0], sidecar=context.sidecar)
            found.update(item.path for item in metric.computation_roots)
            if (
                root.operator_id == "dsl.j1.observe"
                and len(params) >= 3
                and isinstance(params[2], str)
            ):
                relation = context.registry.relationships.get(params[2])
                if relation is None:
                    raise _invalid("observation Relationship is missing")
                found.update((relation.from_entity, relation.to_entity))
            if (
                root.operator_id == "dsl.j1.ratio_observe"
                and len(params) >= 3
                and isinstance(params[2], str)
            ):
                routes = json.loads(params[2])
                for route in routes:
                    for relationship_path in route[1]:
                        relation = context.registry.relationships.get(relationship_path)
                        if relation is None:
                            raise _invalid("ratio Relationship is missing")
                        found.update((relation.from_entity, relation.to_entity))
        for item in root.inputs:
            if isinstance(item.root, LogicalRootHandle):
                visit(item.root)

    visit(node.root)
    if not found:
        raise _invalid("no governed Entity source")
    return tuple(sorted(found))


@contextmanager
def public_j1_source(
    node: J1Node, project_root: str
) -> Iterator[tuple[J1IbisBackend, Mapping[str, ir.Table]]]:
    """Open the one governed DuckDB backend only inside an admitted Run."""
    context = _context(node)
    entities = tuple(
        normalize_target_entity(context.registry, path) for path in _entity_paths(node)
    )
    datasource_ids = {entity.datasource_ref.path for entity in entities}
    if len(datasource_ids) != 1:
        raise _invalid("cross-datasource source closure")
    datasource_id = next(iter(datasource_ids))
    datasource = context.registry.datasources.get(datasource_id)
    if datasource is None or datasource.backend_type != "duckdb":
        raise _invalid("unsupported or missing datasource")
    service = DatasourceConnectionService(project_root, include_semantic_layers=True)
    with ExitStack() as stack:
        try:
            backend = stack.enter_context(service.use_backend(datasource.name, read_only=True))
        except Exception as exc:
            raise DatasetConstructionError(
                expected="a reachable configured DuckDB datasource",
                received=f"unavailable datasource {datasource_id}",
                repair="Restore the configured datasource and execute the logical source branch again.",
                location="analysis.source",
            ) from exc
        if not isinstance(backend, J1IbisBackend) or backend.name != "duckdb":
            raise _invalid("backend does not implement the admitted Ibis route")
        tables: dict[str, ir.Table] = {}
        for entity in entities:
            if not isinstance(entity.source, TableSourceIR):
                raise _invalid("non-table Entity source")
            tables[entity.ref.path] = table_source_expression(backend, entity.source)
        yield backend, tables
