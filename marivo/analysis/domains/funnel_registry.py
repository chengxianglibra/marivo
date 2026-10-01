"""Private R7-only funnel result registrations; no Metric comparison variants."""

from __future__ import annotations

from marivo.analysis.datasets.base import _dataset_repr
from marivo.analysis.datasets.descriptors import _make_shape_id, _StableIdRegistry
from marivo.analysis.datasets.registry import (
    ConsumerRegistration,
    DatasetFamilyRegistration,
    DatasetFamilyRegistry,
)
from marivo.analysis.datasets.state import MaterializedDatasetState, _validate_materialized_state
from marivo.analysis.observation.contracts import RetainedRowsPayload


def register_funnel_delta(registry: DatasetFamilyRegistry, ids: _StableIdRegistry) -> None:
    from marivo.analysis.domains.event_attribution import admits_attribute
    from marivo.analysis.domains.event_comparison import FunnelComparePayload, validate_delta
    from marivo.analysis.domains.funnel_delta import (
        LogicalFunnelDeltaDataset,
        MaterializedFunnelDeltaDataset,
    )

    shapes = (_make_shape_id("delta", "funnel", 1, ids=ids),)

    def decode(state: MaterializedDatasetState) -> MaterializedDatasetState:
        _validate_materialized_state(state, ids=ids)
        return state

    registry.register(
        DatasetFamilyRegistration(
            family_id="delta",
            logical_type=LogicalFunnelDeltaDataset,
            materialized_type=MaterializedFunnelDeltaDataset,
            shape_ids=shapes,
            owner_id="domains.event_comparison",
            ids=ids,
            row_validator=lambda row, rows: validate_delta(row, rows, ids),
            consumers=(
                ConsumerRegistration(
                    "funnel_delta.attribute",
                    ("input",),
                    "attribution",
                    shapes,
                    ("event.exact_journey@v1",),
                ),
                ConsumerRegistration(
                    "delta.funnel_attribute",
                    ("input", "current", "baseline"),
                    "attribution",
                    shapes,
                    ("event.exact_journey@v1",),
                    discoverable=False,
                    operand_shape_ids=(
                        shapes,
                        (_make_shape_id("event", "funnel", 1, ids=ids),),
                        (_make_shape_id("event", "funnel", 1, ids=ids),),
                    ),
                ),
                ConsumerRegistration(
                    "delta.where",
                    ("input",),
                    "delta",
                    shapes,
                    ("event.exact_journey@v1",),
                ),
            ),
            repr_renderer=_dataset_repr,
            materialized_state_decoder=decode,
            node_payload_types=(FunnelComparePayload, RetainedRowsPayload),
            consumer_admission=lambda dataset, method: (
                admits_attribute(dataset)
                if method in ("funnel_delta.attribute", "delta.funnel_attribute")
                else True
            ),
        )
    )


def register_funnel_attribution(registry: DatasetFamilyRegistry, ids: _StableIdRegistry) -> None:
    from marivo.analysis.domains.event_attribution import (
        FunnelAttributePayload,
        validate_attribution,
    )
    from marivo.analysis.domains.funnel_attribution import (
        LogicalFunnelAttributionDataset,
        MaterializedFunnelAttributionDataset,
    )

    def decode(state: MaterializedDatasetState) -> MaterializedDatasetState:
        _validate_materialized_state(state, ids=ids)
        return state

    registry.register(
        DatasetFamilyRegistration(
            family_id="attribution",
            logical_type=LogicalFunnelAttributionDataset,
            materialized_type=MaterializedFunnelAttributionDataset,
            shape_ids=(_make_shape_id("attribution", "funnel-loss-rate", 1, ids=ids),),
            owner_id="domains.event_attribution",
            ids=ids,
            row_validator=lambda row, rows: validate_attribution(row, rows, ids),
            consumers=(),
            repr_renderer=_dataset_repr,
            materialized_state_decoder=decode,
            node_payload_types=(FunnelAttributePayload, RetainedRowsPayload),
        )
    )
