"""Pure selected-backend source qualification for Dataset contract disclosure."""

from __future__ import annotations

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.normalize import artifact_inputs, required_source_dependencies
from marivo.analysis.compiler.placement import source_binding
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.contracts import MetricPayload, PopulationPayload
from marivo.analysis.operators.registry import (
    _source_admissions,
    implementation,
    legacy_source_migration_stage,
    source_unsupported_reason,
)
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.metric_graph import AggregateNodeV1

# Source construction and contract inspection can run under a strict no-I/O
# boundary. Load the pure backend admission owners with this module, before
# that boundary, so the check itself never imports code from disk.
_source_admissions()


def basic_population_candidate(dataset: LogicalDataset) -> bool:
    """Identify the closed R1.1 Population source route without source I/O."""
    root = dataset._root
    return (
        isinstance(root, LogicalRootHandle)
        and root.operator_id in {"session.population", "population.where"}
        and isinstance(root.payload, PopulationPayload)
        and root.payload.time_scope is None
        and root.payload.version_selection is None
        and root.payload.reference_axis is None
    )


def basic_metric_candidate(dataset: LogicalDataset) -> bool:
    """Identify existing sum/count lowering admitted for the R1.1 transport."""
    root = dataset._root
    if (
        not isinstance(root, LogicalRootHandle)
        or root.operator_id != "metric.aggregate"
        or not isinstance(root.payload, MetricPayload)
    ):
        return False
    definition = root.payload.definition
    return (
        definition.time_axis is None
        and definition.time_scope is None
        and definition.reference_axis is None
        and definition.temporal_snapshot is None
        and not definition.distinct_memberships
        and not definition.distributions
        and bool(definition.metrics)
        and all(
            len(metric.graph.nodes) == 1
            and isinstance(metric.graph.nodes[0].node, AggregateNodeV1)
            and metric.graph.nodes[0].node.agg in ("sum", "count")
            and metric.graph.nodes[0].node.fold is None
            and not metric.graph.nodes[0].node.filter
            for metric in definition.metrics
        )
    )


def source_admission_fact(dataset: LogicalDataset) -> tuple[str, str]:
    """Describe static source admission without opening a datasource or Run."""
    if artifact_inputs(dataset):
        return (
            "source_admission",
            "not_checked: retained Artifact inputs require execution-time placement",
        )
    try:
        binding = source_binding(dataset)
    except DatasetCompilationError as error:
        return ("source_admission", f"rejected: {error.received}")
    registration = implementation(dataset)
    selected = registration.for_backend(binding.adapter)
    if selected is not None and selected.source:
        dependencies = (
            required_source_dependencies(dataset, registry=binding.owner.semantic_registry)
            if (basic_population_candidate(dataset) or basic_metric_candidate(dataset))
            and binding.adapter in {"duckdb", "sqlite"}
            else None
        )
        if (
            dependencies is not None
            and len(dependencies.entries) == 1
            and isinstance(dependencies.entries[0].entity.source, TableSourceIR)
        ):
            return (
                "source_admission",
                f"qualified_basic_r1.1 backend={binding.adapter}: final placement remains execution-time",
            )
        return (
            "source_admission",
            f"blocked_r1.1 backend={binding.adapter}: legacy Dataset source route awaits "
            f"R{legacy_source_migration_stage(registration.operator_id)} migration",
        )
    reason = source_unsupported_reason(dataset, binding.adapter)
    detail = reason or f"no registered source implementation for {registration.operator_id}"
    return (
        "source_admission",
        f"rejected backend={binding.adapter}: {detail}",
    )
