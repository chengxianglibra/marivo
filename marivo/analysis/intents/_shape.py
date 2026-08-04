"""Pure output-shape predictors for analysis intents.

These compute a frame's semantic/attribution shape from its inputs with no
backend execution, so agents can predict and assert shape before submitting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from marivo.analysis.attribution_contract import AttributionShape
from marivo.analysis.errors import (
    AttributionShapeUnavailableError,
    ComponentDecompositionError,
)

__all__ = [
    "AttributionShape",
    "SemanticShape",
    "attribution_output_shape",
    "compare_output_shape",
    "observe_output_shape",
]

if TYPE_CHECKING:
    from marivo.analysis.frames.delta import DeltaFrameMeta
    from marivo.analysis.frames.metric import MetricFrameMeta

SemanticShape = Literal["scalar", "time_series", "segmented", "panel"]


def observe_output_shape(*, has_grain: bool, has_dimensions: bool) -> SemanticShape:
    """Predict the MetricFrame shape observe will produce.

    Mirrors the (grain, dimensions) matrix in observe for both derived and
    non-derived metrics; derived-ness changes the execution path, not the shape.
    """
    if has_grain and has_dimensions:
        return "panel"
    if has_grain:
        return "time_series"
    if has_dimensions:
        return "segmented"
    return "scalar"


def compare_output_shape(current_meta: MetricFrameMeta) -> SemanticShape:
    """Predict the DeltaFrame shape compare will produce.

    compare requires both inputs to share semantic_kind and passes the current
    frame's semantic_kind through to the delta.
    """
    return current_meta.semantic_kind


def attribution_output_shape(delta_meta: DeltaFrameMeta) -> AttributionShape:
    """Predict the AttributionFrame shape decompose will produce for a delta.

    Reads the delta's own metadata only (no component-frame load): a delta with
    no component_ref decomposes to "sum"; otherwise the linked composition kind
    ("ratio" -> "ratio_mix", "weighted_mean" -> "weighted_mix") decides.
    ComponentFrameMeta.composition_kind is the authoritative source; the delta's
    composition["kind"] mirrors it for a cheap read.
    """
    basis = getattr(delta_meta, "attribution_basis", None)
    if basis is not None:
        return "distinct_membership" if basis.kind == "count_distinct" else "quantile_replacement"
    if delta_meta.component_ref is None:
        if delta_meta.additivity is None:
            return "sum"
        if delta_meta.additivity == "additive" or (
            delta_meta.additivity == "semi_additive"
            and delta_meta.status_time_dimension is not None
        ):
            return "sum"
        raise AttributionShapeUnavailableError(
            message="no closed mathematical attribution shape is available for this delta",
            expected="a persisted attribution basis, component basis, or rollup-safe aggregate",
            received=f"aggregation={delta_meta.aggregation!r} additivity={delta_meta.additivity!r}",
            location="DeltaFrame.predicted_attribution_shape()",
            context={"next": "inspect DeltaFrame.contract() for admission and repair"},
        )
    kind = (delta_meta.composition or {}).get("kind")
    if kind == "ratio":
        return "ratio_mix"
    if kind == "weighted_mean":
        return "weighted_mix"
    if kind == "linear":
        return "sum"
    raise ComponentDecompositionError(
        message="cannot predict attribution shape: unknown component composition kind",
        context={
            "component_ref": delta_meta.component_ref,
            "composition_kind": kind,
        },
    )
