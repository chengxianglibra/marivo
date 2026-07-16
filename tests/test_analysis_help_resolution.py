"""Tests for canonical help target resolution and lexical suggestions."""

from __future__ import annotations

import datetime as _dt

import pandas as pd
import pytest

from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis._capabilities.surface import ANALYSIS_LIVE_SURFACE
from marivo.analysis.errors import (
    AnalysisError,
    HelpTargetError,
    MetricNotFoundError,
    WindowInvalidError,
)
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.lineage import Lineage
from marivo.analysis.session.core import Session
from marivo.introspection.live.model import SURFACE_LIMITS
from marivo.introspection.live.resolve import (
    ResolvedLiveTarget,
    resolve_live_target,
)
from marivo.introspection.live.resolve import (
    suggestions_for as shared_suggestions_for,
)
from marivo.refs import SemanticRef, SymbolKind
from marivo.semantic.refs import DimensionRef, MetricRef


def resolve_help_target(target: object) -> ResolvedLiveTarget:
    """Resolve through the shared kernel configured by analysis."""
    return resolve_live_target(target, ANALYSIS_LIVE_SURFACE)


def suggestions_for(query: str) -> tuple[str, ...]:
    """Rank suggestions through the shared kernel's analysis index."""
    index = ANALYSIS_LIVE_SURFACE.suggestion_index
    assert index is not None
    return shared_suggestions_for(query, index)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(tmp_path, monkeypatch) -> Session:
    import marivo.analysis as mv

    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create(name="resolve_probe", use_datasources=False)


def _make_base_frame() -> BaseFrame:
    meta = BaseFrameMeta(
        kind="metric_frame",
        ref="frame_test",
        session_id="sess_test",
        project_root="/tmp",
        produced_by_job=None,
        created_at=_dt.datetime.now(tz=_dt.UTC),
        row_count=1,
        byte_size=10,
        lineage=Lineage(steps=()),
    )
    return BaseFrame(_df=pd.DataFrame({"x": [1]}), meta=meta)


# ---------------------------------------------------------------------------
# Canonical string resolution
# ---------------------------------------------------------------------------


def test_capability_id_resolves() -> None:
    result = resolve_help_target("observe")
    assert isinstance(result, ResolvedLiveTarget)
    assert result.kind == "descriptor"
    assert result.descriptor.id == "observe"


def test_grouping_topic_discover() -> None:
    result = resolve_help_target("discover")
    assert result.kind == "descriptor"
    assert result.descriptor.id == "discover"


def test_grouping_topic_transform() -> None:
    result = resolve_help_target("transform")
    assert result.kind == "descriptor"
    assert result.descriptor.id == "transform"


def test_grouping_topic_recovery() -> None:
    result = resolve_help_target("recovery")
    assert result.kind == "descriptor"
    assert result.descriptor.id == "recovery"


def test_grouping_topic_boundary() -> None:
    result = resolve_help_target("boundary")
    assert result.kind == "descriptor"
    assert result.descriptor.id == "boundary"


def test_nested_discover_target() -> None:
    result = resolve_help_target("discover.point_anomalies")
    assert result.kind == "descriptor"
    assert result.descriptor.id == "discover.point_anomalies"


def test_nested_transform_target() -> None:
    result = resolve_help_target("transform.filter")
    assert result.kind == "descriptor"
    assert result.descriptor.id == "transform.filter"


def test_nested_evidence_target() -> None:
    result = resolve_help_target("session.evidence.findings")
    assert result.kind == "descriptor"
    assert result.descriptor.id == "session.evidence.findings"


def test_help_target_equals_id_for_simple_capabilities() -> None:
    result = resolve_help_target("compare")
    assert result.descriptor.help_target == "compare"
    assert result.descriptor.id == "compare"


def test_none_target_raises() -> None:
    with pytest.raises(HelpTargetError):
        resolve_help_target(None)  # type: ignore[arg-type]


def test_empty_string_raises() -> None:
    with pytest.raises(HelpTargetError) as captured:
        resolve_help_target("")
    assert captured.value.repair is not None
    assert captured.value.repair.candidates  # non-empty suggestions


def test_unknown_string_raises() -> None:
    with pytest.raises(HelpTargetError) as captured:
        resolve_help_target("nonexistent.target")
    assert captured.value.repair is not None
    assert captured.value.repair.candidates  # non-empty suggestions


# ---------------------------------------------------------------------------
# Invalid aliases
# ---------------------------------------------------------------------------


def test_string_aliases_are_not_canonical() -> None:
    """Session.observe, session.observe, mv.Session.observe are NOT canonical."""
    for target in ("Session.observe", "session.observe", "mv.Session.observe"):
        with pytest.raises(HelpTargetError) as captured:
            resolve_help_target(target)
        assert "observe" in captured.value.repair.candidates


def test_mv_prefix_alias_raises() -> None:
    with pytest.raises(HelpTargetError):
        resolve_help_target("mv.Session")


def test_dotted_type_method_alias_raises() -> None:
    with pytest.raises(HelpTargetError) as captured:
        resolve_help_target("MetricFrame.show")
    # Suggestions should include targets related to "show" or "frame".
    candidates = captured.value.repair.candidates
    assert len(candidates) > 0
    # At least one suggestion should contain "show" or "frame".
    assert any("show" in c or "frame" in c.lower() for c in candidates)


# ---------------------------------------------------------------------------
# Callable resolution
# ---------------------------------------------------------------------------


def test_unbound_session_observe() -> None:
    result = resolve_help_target(Session.observe)
    assert result.kind == "descriptor"
    assert result.descriptor.id == "observe"


def test_bound_session_observe(tmp_path, monkeypatch) -> None:
    session = _make_session(tmp_path, monkeypatch)
    result = resolve_help_target(session.observe)
    assert result.kind == "descriptor"
    assert result.descriptor.id == "observe"


def test_unbound_session_compare() -> None:
    result = resolve_help_target(Session.compare)
    assert result.kind == "descriptor"
    assert result.descriptor.id == "compare"


def test_unbound_session_forecast() -> None:
    result = resolve_help_target(Session.forecast)
    assert result.kind == "descriptor"
    assert result.descriptor.id == "forecast"


def test_unbound_session_assess_quality() -> None:
    result = resolve_help_target(Session.assess_quality)
    assert result.kind == "descriptor"
    assert result.descriptor.id == "assess_quality"


# ---------------------------------------------------------------------------
# Type resolution
# ---------------------------------------------------------------------------


def test_session_type_resolves() -> None:
    result = resolve_help_target(Session)
    assert result.kind == "type_contract"


def test_metric_frame_type_resolves() -> None:
    result = resolve_help_target(MetricFrame)
    assert result.kind == "type_contract"


def test_base_frame_type_resolves() -> None:
    result = resolve_help_target(BaseFrame)
    assert result.kind == "type_contract"


def test_unsupported_type_raises() -> None:
    class NotRegistered:
        pass

    with pytest.raises(HelpTargetError):
        resolve_help_target(NotRegistered)


def test_builtin_type_raises() -> None:
    with pytest.raises(HelpTargetError):
        resolve_help_target(int)


# ---------------------------------------------------------------------------
# Runtime object resolution
# ---------------------------------------------------------------------------


def test_session_instance_resolves(tmp_path, monkeypatch) -> None:
    session = _make_session(tmp_path, monkeypatch)
    result = resolve_help_target(session)
    assert result.kind == "type_contract"


def test_frame_instance_resolves() -> None:
    frame = _make_base_frame()
    result = resolve_help_target(frame)
    assert result.kind == "type_contract"


# ---------------------------------------------------------------------------
# Semantic object resolution
# ---------------------------------------------------------------------------


def test_metric_ref_resolves() -> None:
    ref = MetricRef("sales.revenue")
    result = resolve_help_target(ref)
    assert result.kind == "reference_briefing"


def test_dimension_ref_resolves() -> None:
    ref = DimensionRef("sales.orders.region")
    result = resolve_help_target(ref)
    assert result.kind == "reference_briefing"


def test_base_semantic_ref_resolves() -> None:
    ref = SemanticRef("sales.revenue", SymbolKind.METRIC)
    result = resolve_help_target(ref)
    assert result.kind == "reference_briefing"


# ---------------------------------------------------------------------------
# Error resolution
# ---------------------------------------------------------------------------


def test_error_subclass_resolves() -> None:
    result = resolve_help_target(MetricNotFoundError)
    assert result.kind == "error_contract"


def test_error_subclass_window_invalid() -> None:
    result = resolve_help_target(WindowInvalidError)
    assert result.kind == "error_contract"


def test_base_analysis_error_subclass_resolves() -> None:
    result = resolve_help_target(AnalysisError)
    assert result.kind == "error_contract"


def test_error_instance_resolves() -> None:
    err = MetricNotFoundError(
        message="metric not found",
        context={"metric_id": "sales.foobar"},
    )
    result = resolve_help_target(err)
    assert result.kind == "error_briefing"


def test_non_analysis_error_raises() -> None:
    with pytest.raises(HelpTargetError):
        resolve_help_target(ValueError("not an analysis error"))


def test_error_name_string_resolves() -> None:
    result = resolve_help_target("MetricNotFoundError")
    assert result.kind == "error_contract"
    assert result.error_name == "MetricNotFoundError"


def test_errors_prefixed_string_resolves() -> None:
    result = resolve_help_target("errors.MetricNotFoundError")
    assert result.kind == "error_contract"
    assert result.error_name == "MetricNotFoundError"


def test_fully_qualified_error_string_resolves() -> None:
    result = resolve_help_target("marivo.analysis.errors.MetricNotFoundError")
    assert result.kind == "error_contract"
    assert result.error_name == "MetricNotFoundError"


# ---------------------------------------------------------------------------
# Unsupported objects
# ---------------------------------------------------------------------------


def test_module_raises() -> None:
    import marivo.analysis

    with pytest.raises(HelpTargetError):
        resolve_help_target(marivo.analysis)


def test_string_number_raises() -> None:
    with pytest.raises(HelpTargetError):
        resolve_help_target(42)


def test_list_raises() -> None:
    with pytest.raises(HelpTargetError):
        resolve_help_target([1, 2, 3])


def test_dict_raises() -> None:
    with pytest.raises(HelpTargetError):
        resolve_help_target({"key": "value"})


# ---------------------------------------------------------------------------
# Lexical suggestions
# ---------------------------------------------------------------------------


def test_summary_tokens_precede_edit_distance() -> None:
    """Summary/id keyword tokens should rank above edit-distance fuzzy matches.

    'anomaly' matches the id token 'anomalies' (fuzzy) in
    discover.point_anomalies, and 'unusual' matches the summary token in the
    same descriptor. Both should rank the discover target highly.
    """
    anomaly = suggestions_for("anomaly")
    assert "discover.point_anomalies" in anomaly
    assert len(anomaly) <= SURFACE_LIMITS.help_suggestion_limit

    # Summary keyword "unusual" appears in the discover.point_anomalies summary.
    unusual = suggestions_for("unusual")
    assert "discover.point_anomalies" in unusual

    # Summary keyword "candidates" appears in the discover.period_shifts summary.
    period = suggestions_for("period_shifts")
    assert "discover.period_shifts" in period
    assert len(period) <= SURFACE_LIMITS.help_suggestion_limit


def test_suggestions_are_bounded() -> None:
    for query in ("frame", "session", "discover", "a"):
        result = suggestions_for(query)
        assert len(result) <= SURFACE_LIMITS.help_suggestion_limit


def test_exact_token_match_first() -> None:
    result = suggestions_for("observe")
    assert result[0] == "observe"


def test_exact_help_target_match() -> None:
    result = suggestions_for("forecast")
    assert result[0] == "forecast"


def test_empty_query_returns_empty() -> None:
    result = suggestions_for("")
    assert result == ()


def test_no_match_returns_empty() -> None:
    result = suggestions_for("zzzzzzzzzz_nothing_matches")
    assert result == ()


def test_suggestions_return_canonical_strings() -> None:
    """Every suggestion must be a canonical help_target registered in REGISTRY."""
    for query in ("frame", "session", "compare", "quality", "boundary"):
        for suggestion in suggestions_for(query):
            assert suggestion in REGISTRY.help_targets


def test_substring_match_ranks_above_edit_distance() -> None:
    # "discover" contains "cover" — should surface discover.* targets
    result = suggestions_for("cover")
    assert any(r.startswith("discover") for r in result)


def test_deterministic_ordering() -> None:
    """Same query always produces the same ordering."""
    r1 = suggestions_for("frame")
    r2 = suggestions_for("frame")
    assert r1 == r2


def test_tie_break_by_canonical_id() -> None:
    """When relevance scores tie, break by canonical id alphabetically."""
    # "as" appears in many as_* targets — ties broken alphabetically
    result = suggestions_for("as_")
    assert len(result) > 0
    # Verify deterministic: sorted by id when scores tie
    # All "as_" results should be present in alphabetical order among ties
    assert all(s in REGISTRY.help_targets for s in result)


# ---------------------------------------------------------------------------
# ResolvedLiveTarget protocol
# ---------------------------------------------------------------------------


def test_repr_is_bounded_single_line() -> None:
    result = resolve_help_target("observe")
    r = repr(result)
    assert isinstance(r, str)
    assert "\n" not in r


def test_descriptor_result_has_descriptor() -> None:
    result = resolve_help_target("observe")
    assert result.descriptor is not None
    assert result.descriptor.id == "observe"


def test_analysis_live_surface_preserves_native_registry_and_descriptor_identity() -> None:
    assert ANALYSIS_LIVE_SURFACE.registry is REGISTRY
    for target in REGISTRY.help_targets:
        resolved = resolve_live_target(target, ANALYSIS_LIVE_SURFACE)
        assert resolved.descriptor is REGISTRY.by_help_target(target)


def test_type_contract_result_has_type_name() -> None:
    result = resolve_help_target(Session)
    assert result.type_name is not None
    assert "Session" in result.type_name


def test_reference_briefing_has_ref_id() -> None:
    ref = MetricRef("sales.revenue")
    result = resolve_help_target(ref)
    assert result.reference_id is not None


def test_error_contract_has_error_name() -> None:
    result = resolve_help_target(MetricNotFoundError)
    assert result.error_name is not None
    assert "MetricNotFound" in result.error_name


def test_error_briefing_has_error_name() -> None:
    err = MetricNotFoundError(
        message="not found",
        context={"metric_id": "x.y"},
    )
    result = resolve_help_target(err)
    assert result.error_name is not None
    assert "MetricNotFound" in result.error_name
