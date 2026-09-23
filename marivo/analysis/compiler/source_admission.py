"""Pure selected-backend source qualification for Dataset contract disclosure."""

from __future__ import annotations

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.normalize import artifact_inputs
from marivo.analysis.compiler.placement import source_binding
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.operators.registry import (
    _source_admissions,
    implementation,
    source_unsupported_reason,
)

# Source construction and contract inspection can run under a strict no-I/O
# boundary. Load the pure backend admission owners with this module, before
# that boundary, so the check itself never imports code from disk.
_source_admissions()


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
        return (
            "source_admission",
            f"static_pass backend={binding.adapter}: final placement remains execution-time",
        )
    reason = source_unsupported_reason(dataset, binding.adapter)
    detail = reason or f"no registered source implementation for {registration.operator_id}"
    return (
        "source_admission",
        f"rejected backend={binding.adapter}: {detail}",
    )
