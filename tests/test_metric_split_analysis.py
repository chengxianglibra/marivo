"""Tests for metric-split Plan 5: analysis layer migration."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from marivo.analysis.intents.observe_planner import _planned_metric
from marivo.semantic.catalog import (
    AiContextView,
    MetricDetails,
    SemanticKind,
    SemanticRef,
)
from marivo.semantic.ir import ParityStatus, SourceLocation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ref(ref: str, kind: SemanticKind = SemanticKind.METRIC) -> SemanticRef:
    return SemanticRef(ref=ref, kind=kind)


def _make_ctx() -> AiContextView:
    from marivo.datasource.ir import AiContextIR

    return AiContextIR(
        business_definition=None,
        guardrails=None,
        synonyms=(),
        examples=(),
        instructions=None,
        owner_notes=None,
    )


def _make_loc() -> SourceLocation:
    return SourceLocation(file="test.py", line=1)


def metric_details_factory(
    *,
    metric_type: str = "simple",
    composition: str | None = None,
    components: tuple[tuple[str, str], ...] = (),
    linear_terms: tuple[tuple[str, str], ...] = (),
    **overrides,
) -> MetricDetails:
    """Build a MetricDetails with sensible defaults for analysis-layer tests."""
    ref = overrides.pop("ref", None) or _make_ref("test.metric")
    comp_refs = tuple(
        (role, SemanticRef(ref=comp_id, kind=SemanticKind.METRIC)) for role, comp_id in components
    )
    kwargs = {
        "ref": ref,
        "kind": SemanticKind.METRIC,
        "name": overrides.pop("name", ref.ref.rsplit(".", 1)[-1]),
        "domain": overrides.pop("domain", "test"),
        "description": overrides.pop("description", None),
        "context": overrides.pop("context", _make_ctx()),
        "source_location": overrides.pop("source_location", _make_loc()),
        "parents": overrides.pop("parents", ()),
        "children": overrides.pop("children", ()),
        "dependents": overrides.pop("dependents", ()),
        "entities": overrides.pop("entities", (_make_ref("test.entity", SemanticKind.ENTITY),)),
        "root_entity": overrides.pop("root_entity", _make_ref("test.entity", SemanticKind.ENTITY)),
        "metric_type": metric_type,
        "aggregation": overrides.pop("aggregation", "sum" if metric_type == "simple" else None),
        "measure": overrides.pop("measure", None),
        "composition": composition,
        "components": comp_refs,
        "linear_terms": linear_terms,
        "required_relationships": overrides.pop("required_relationships", ()),
        "additivity": overrides.pop("additivity", "additive"),
        "fold": overrides.pop("fold", None),
        "status_time_dimension": overrides.pop("status_time_dimension", None),
        "fanout_policy": overrides.pop("fanout_policy", "block"),
        "unit": overrides.pop("unit", None),
        "provenance": overrides.pop("provenance", None),
        "parity_status": overrides.pop("parity_status", ParityStatus.UNVERIFIED),
        "python_symbol": overrides.pop("python_symbol", ref.ref.rsplit(".", 1)[-1]),
    }
    kwargs.update(overrides)
    return MetricDetails(**kwargs)


# ---------------------------------------------------------------------------
# Task 1: Planner adapter exposes metric_type / composition
# ---------------------------------------------------------------------------


def test_planned_metric_exposes_split_attrs():
    d = metric_details_factory(
        metric_type="derived",
        composition="ratio",
        components=[("numerator", "s.a"), ("denominator", "s.b")],
    )
    planned = _planned_metric(d)
    assert planned.metric_type == "derived"
    assert planned.composition.kind == "ratio"
    assert planned.composition.components == {"numerator": "s.a", "denominator": "s.b"}


# ---------------------------------------------------------------------------
# Task 2: Frame metas use composition / composition_kind
# ---------------------------------------------------------------------------


def test_frame_metas_use_composition():
    from marivo.analysis.frames.component import ComponentFrameMeta
    from marivo.analysis.frames.delta import DeltaFrameMeta
    from marivo.analysis.frames.metric import MetricFrameMeta

    metric_fields = set(MetricFrameMeta.model_fields)
    delta_fields = set(DeltaFrameMeta.model_fields)
    component_fields = set(ComponentFrameMeta.model_fields)

    assert "composition" in metric_fields
    assert "decomposition" not in metric_fields
    assert "composition" in delta_fields
    assert "decomposition" not in delta_fields
    assert "composition_kind" in component_fields
    assert "decomposition_kind" not in component_fields


# ---------------------------------------------------------------------------
# Task 3: _evaluate_composition_on_frame handles ratio, weighted, linear
# ---------------------------------------------------------------------------


def test_evaluate_composition_ratio():
    import ibis
    import pandas as pd

    from marivo.analysis.intents.observe import _evaluate_composition_on_frame

    d = metric_details_factory(
        metric_type="derived",
        composition="ratio",
        components=[("numerator", "s.revenue"), ("denominator", "s.orders")],
    )
    metric_ir = _planned_metric(d)
    df = pd.DataFrame({"revenue": [100.0, 200.0], "orders": [10.0, 20.0]})
    table = ibis.memtable(df)
    result = _evaluate_composition_on_frame(metric_ir, table)
    result_df = result.to_pandas()
    assert float(result_df.iloc[0]) == pytest.approx(10.0)
    assert float(result_df.iloc[1]) == pytest.approx(10.0)


def test_evaluate_composition_weighted_average_uses_value_role():
    import ibis
    import pandas as pd

    from marivo.analysis.intents.observe import _evaluate_composition_on_frame

    d = metric_details_factory(
        metric_type="derived",
        composition="weighted_average",
        components=[("value", "s.rate"), ("weight", "s.sessions")],
    )
    metric_ir = _planned_metric(d)
    df = pd.DataFrame({"rate": [4.0, 6.0], "sessions": [10.0, 20.0]})
    table = ibis.memtable(df)
    result = _evaluate_composition_on_frame(metric_ir, table)
    result_df = result.to_pandas()
    # value/weight = rate/sessions
    assert float(result_df.iloc[0]) == pytest.approx(0.4)
    assert float(result_df.iloc[1]) == pytest.approx(0.3)


def test_evaluate_composition_linear_adds_terms():
    import ibis
    import pandas as pd

    from marivo.analysis.intents.observe import _evaluate_composition_on_frame

    d = metric_details_factory(
        metric_type="derived",
        composition="linear",
        components=[("term0", "s.gross"), ("term1", "s.refunds")],
        linear_terms=[("+", "s.gross"), ("-", "s.refunds")],
    )
    metric_ir = _planned_metric(d)
    df = pd.DataFrame({"gross": [100.0, 200.0], "refunds": [20.0, 30.0]})
    table = ibis.memtable(df)
    result = _evaluate_composition_on_frame(metric_ir, table)
    result_df = result.to_pandas()
    # linear: +gross - refunds = 100-20=80, 200-30=170
    assert float(result_df.iloc[0]) == pytest.approx(80.0)
    assert float(result_df.iloc[1]) == pytest.approx(170.0)


# ---------------------------------------------------------------------------
# Task 4: attribution_output_shape uses composition; linear -> sum
# ---------------------------------------------------------------------------


def test_shape_linear_is_sum():
    from marivo.analysis.intents._shape import attribution_output_shape

    delta = SimpleNamespace(component_ref="x", composition={"kind": "linear"})
    assert attribution_output_shape(delta) == "sum"


def test_shape_ratio_and_weighted():
    from marivo.analysis.intents._shape import attribution_output_shape

    assert (
        attribution_output_shape(SimpleNamespace(component_ref="x", composition={"kind": "ratio"}))
        == "ratio_mix"
    )
    assert (
        attribution_output_shape(
            SimpleNamespace(component_ref="x", composition={"kind": "weighted_average"})
        )
        == "weighted_mix"
    )


# ---------------------------------------------------------------------------
# Task 5: Decompose value-role per kind + linear additive attribution
# ---------------------------------------------------------------------------


def test_component_value_role_ratio_is_numerator():
    from marivo.analysis.intents.decompose import _component_value_role

    component = SimpleNamespace(
        meta=SimpleNamespace(
            composition_kind="ratio", components={"numerator": "s.a", "denominator": "s.b"}
        )
    )
    assert _component_value_role(component) == "numerator"


def test_component_value_role_weighted_is_value():
    from marivo.analysis.intents.decompose import _component_value_role

    component = SimpleNamespace(
        meta=SimpleNamespace(
            composition_kind="weighted_average",
            components={"value": "s.rate", "weight": "s.sessions"},
        )
    )
    assert _component_value_role(component) == "value"


def test_component_linear_output_additive():
    import pandas as pd

    from marivo.analysis.intents.decompose import _component_linear_output_for_df

    component = SimpleNamespace(
        meta=SimpleNamespace(
            composition_kind="linear",
            components={"term0": "s.gross", "term1": "s.refunds"},
            linear_terms=(("+", "s.gross"), ("-", "s.refunds")),
        )
    )
    df = pd.DataFrame(
        {
            "axis": ["a", "b"],
            "current_gross": [110.0, 220.0],
            "baseline_gross": [100.0, 200.0],
            "current_refunds": [25.0, 35.0],
            "baseline_refunds": [20.0, 30.0],
        }
    )
    result = _component_linear_output_for_df(df=df, component=component, axis_column="axis")
    # contribution = (+1)*(110-100) + (-1)*(25-20) = 10 - 5 = 5 for row a
    # contribution = (+1)*(220-200) + (-1)*(35-30) = 20 - 5 = 15 for row b
    # Output is sorted by contribution descending, so row b comes first
    assert float(result[result["axis"] == "a"]["contribution"].iloc[0]) == pytest.approx(5.0)
    assert float(result[result["axis"] == "b"]["contribution"].iloc[0]) == pytest.approx(15.0)
    # additive: all contribution is value-effect
    assert float(result[result["axis"] == "a"]["value_effect"].iloc[0]) == pytest.approx(5.0)
    assert float(result[result["axis"] == "a"]["mix_effect"].iloc[0]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Task 6: Evidence extraction module renamed to composition
# ---------------------------------------------------------------------------


def test_evidence_composition_module_imports():
    from marivo.analysis.evidence.extraction import composition

    assert hasattr(composition, "__name__")
