"""Resolve retained Finding authority from the remaining owning result families."""

from __future__ import annotations

from marivo.analysis.evidence._dataset_reads import FindingRegistration
from marivo.analysis.materialization.contracts import ArtifactDescriptor


def finding_registration(descriptor: ArtifactDescriptor) -> FindingRegistration | None:
    if descriptor.row_contract.family_semantics.kind in (
        "delta/funnel@v1",
        "attribution/funnel-loss-rate@v1",
    ):
        from marivo.analysis.materialization.event_comparison_publication import (
            finding_registration,
        )

        return finding_registration(descriptor)
    if descriptor.row_contract.shape_id.family_id == "forecast":
        from marivo.analysis.materialization.forecast_publication import finding_registration

        return finding_registration(descriptor)
    if descriptor.row_contract.shape_id.family_id == "association":
        from marivo.analysis.materialization.association_publication import finding_registration

        return finding_registration(descriptor)
    return None
