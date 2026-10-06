"""Native disclosure of committed graph state and bounded byte-count facts."""

from marivo.analysis._capabilities.dataset_model import (
    DisclosureProvider,
    ExportInput,
    NavigationInput,
    value_type,
    with_sealed_variants,
)
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.state import MaterializedDatasetState


def provider() -> DisclosureProvider:
    """Describe the concrete state returned by current materialized results."""
    descriptors = (
        value_type(
            "datasets.materialized_state",
            MaterializedDatasetState,
            summary="Committed Artifact state, counts and execution identity.",
            acquisition="Read result.state after execute() or exact Artifact recovery.",
            producers=("session.artifact", "actions.execute"),
            consumers=("session.artifact",),
        ),
        value_type(
            "datasets.byte_count",
            d.DatasetByteCount,
            summary="Exact retained byte count or an explicit unavailable reason.",
            acquisition="Read result.state.realized_byte_count.",
            producers=("datasets.materialized_state",),
            consumers=("actions.show",),
        ),
        NavigationInput(
            "datasets",
            "Inspect the committed state of typed graph results.",
            ("datasets.materialized_state", "datasets.byte_count"),
            discovery_group="artifacts",
        ),
        NavigationInput(
            "actions.execute",
            "Execute the current logical typed analysis graph.",
            (),
            guidance=(
                "Call logical.execute() to publish into Store 7 the paired materialized result.",
                "Use marivo.help(logical.execute) for the exact receiver contract; construction and contract() do not execute.",
            ),
            related=("session.members", "dsl.LogicalAnalysisDomain.execute"),
            discovery_group="entry",
        ),
    )
    return DisclosureProvider(
        "core",
        with_sealed_variants(
            descriptors,
            {
                "datasets.byte_count": (d._ExactByteCount, d._UnavailableByteCount),
            },
        ),
        (
            ExportInput(
                "MaterializedDatasetState", MaterializedDatasetState, "datasets.materialized_state"
            ),
            ExportInput("DatasetByteCount", d.DatasetByteCount, "datasets.byte_count"),
        ),
    )
