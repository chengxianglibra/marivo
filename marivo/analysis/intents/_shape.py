"""Pure output-shape predictors for analysis intents.

These compute a frame's semantic/attribution shape from its inputs with no
backend execution, so agents can predict and assert shape before submitting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from marivo.analysis.errors import ComponentDecompositionError

if TYPE_CHECKING:
    from marivo.analysis.frames.delta import DeltaFrameMeta
    from marivo.analysis.frames.metric import MetricFrameMeta

SemanticShape = Literal["scalar", "time_series", "segmented", "panel"]
AttributionShape = Literal["sum", "ratio_mix", "weighted_mix"]


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
    ("ratio" -> "ratio_mix", "weighted_average" -> "weighted_mix") decides.
    ComponentFrameMeta.composition_kind is the authoritative source; the delta's
    composition["kind"] mirrors it for a cheap read.
    """
    if delta_meta.component_ref is None:
        return "sum"
    kind = (delta_meta.composition or {}).get("kind")
    if kind == "ratio":
        return "ratio_mix"
    if kind == "weighted_average":
        return "weighted_mix"
    if kind == "linear":
        return "sum"
    raise ComponentDecompositionError(
        message="cannot predict attribution shape: unknown component composition kind",
        details={
            "component_ref": delta_meta.component_ref,
            "composition_kind": kind,
        },
    )
