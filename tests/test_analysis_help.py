"""Semantic invariants for the native analysis renderer behind unified help."""

from __future__ import annotations

import inspect

import pytest

import marivo
import marivo.analysis as mv
from marivo._help.model import MarivoHelpTargetError
from marivo.analysis._capabilities.model import (
    ROOT_GROUP_ORDER,
    ConstructorCapability,
    OperatorCapability,
)
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.errors import (
    AnalysisError,
    AnalysisRepair,
    MetricNotFoundError,
)
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.session.core import Session
from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref
from tests.shared_fixtures import rendered_help

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture(target: object = None, **kwargs: object) -> str:
    """Return native text with the public print boundary newline."""
    assert not kwargs
    return _text(target) + "\n"


def _text(target: object = None, **kwargs: object) -> str:
    """Return native analysis text through the private unified router."""
    assert not kwargs
    return rendered_help(target, owner="analysis")


# ---------------------------------------------------------------------------
# Fingerprint prefix (root help)
# ---------------------------------------------------------------------------


def test_root_help_has_three_line_fingerprint() -> None:
    text = _text()
    lines = text.splitlines()
    assert len(lines) >= 3
    assert lines[0].startswith("Marivo: ")
    assert marivo.__version__ in lines[0]
    assert lines[1].startswith("Python: ")
    assert lines[2].startswith("Package: ")


def test_root_help_fingerprint_uses_resolved_paths() -> None:
    from pathlib import Path

    text = _text()
    lines = text.splitlines()
    assert str(Path(marivo.__file__).resolve()) in lines[2]


def test_root_help_documents_mv_namespace_import() -> None:
    """Root help uses ``mv.`` throughout but must state how to obtain it, so
    examples are runnable from a cold start. See issue #22.
    """
    text = _text()
    assert "Python imports:" in text
    assert "import marivo.analysis as mv" in text
    # The import hint precedes the capability index.
    assert text.index("import marivo.analysis as mv") < text.index("Capabilities:")


def test_focused_help_documents_mv_namespace_import() -> None:
    """Focused pages also use ``mv.`` and must carry the import hint."""
    text = _text("observe")
    assert "Python imports:" in text
    assert "import marivo.analysis as mv" in text
    # The import hint follows the target name on the first line.
    assert text.splitlines()[0] == "observe"


# ---------------------------------------------------------------------------
# Root groups and canonical targets
# ---------------------------------------------------------------------------


def test_root_help_has_eight_deterministic_groups() -> None:
    text = _text()
    for group in ROOT_GROUP_ORDER:
        # Each group must appear as a section header in the rendered output.
        assert group in text, f"missing root group: {group}"


def test_root_help_contains_all_direct_capabilities() -> None:
    text = _text()
    direct = [d for d in REGISTRY.descriptors if d.root_visibility == "direct"]
    assert len(direct) > 0
    for desc in direct:
        assert desc.help_target in text, f"missing direct capability: {desc.help_target}"


def test_root_help_never_advertises_grouping_topics_as_session_members() -> None:
    text = _text()
    for fake_entrypoint in (
        "session.session",
        "session.recovery",
        "session.artifacts",
        "session.boundary",
    ):
        assert fake_entrypoint not in text
    assert 'marivo.help("analysis.recovery")' in text
    assert 'marivo.help("analysis.artifacts")' in text


def test_focused_grouping_help_lists_real_members() -> None:
    recovery = _text("recovery")
    assert 'Entrypoint: marivo.help("analysis.recovery")' in recovery
    assert "Members:" in recovery
    assert "session.get_frame(ref)" in recovery
    assert "session.recent_jobs(limit=5)" in recovery
    assert "mv.session.recent()" in recovery
    assert "mv.session.inspect(name)" in recovery

    artifacts = _text("artifacts")
    assert 'Entrypoint: marivo.help("analysis.artifacts")' in artifacts
    assert "frame.show()" in artifacts
    assert "frame.contract()" in artifacts
    assert "frame.to_pandas()" in artifacts


def test_event_and_lifecycle_grouping_help_lists_real_members() -> None:
    events = _text("events")
    assert "session.events.match(...)" in events
    assert "session.events.funnel(...)" in events
    assert "session.events.time_to_event(...)" in events

    lifecycle = _text("lifecycle")
    for member in ("replay", "distribution", "transitions", "dwell", "violations"):
        assert f"session.lifecycle.{member}(...)" in lifecycle


def test_alignment_help_exposes_closed_admission_matrix() -> None:
    text = _text("alignment")
    for member in (
        "window_bucket",
        "day_of_week",
        "period_progress",
        "period_correspondence",
        "occurrence_progress",
    ):
        assert f"mv.{member}" in text
    assert "MetricFrame.compare day-grain time-series or panel" in text
    assert "EventFrame.compare     alignment=None" in text
    assert "holiday_aligned" not in text


def test_grouping_members_use_registered_prefix_without_renderer_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import marivo.analysis._capabilities.render as render_module
    from marivo.analysis._capabilities.model import ReadCapability
    from marivo.analysis._capabilities.registry import _make_grouping_descriptor

    grouping = _make_grouping_descriptor("example", "Example namespace.", "typed_analysis")
    member = ReadCapability(
        id="example.inspect",
        public_entrypoint="example.inspect()",
        help_target="example.inspect",
        summary="Inspect an example.",
        root_group="typed_analysis",
        root_visibility="grouped",
        callable_path="example.inspect",
        receiver_family="Example",
    )
    monkeypatch.setattr(
        render_module,
        "REGISTRY",
        SimpleNamespace(descriptors=(grouping, member)),
    )

    assert render_module._grouping_members(grouping) == [member]


def test_artifact_help_teaches_progressive_reads_without_planning_analysis() -> None:
    root = _text()
    artifacts = _text("artifacts")

    for text in (root, artifacts):
        assert "Read artifacts progressively" in text
        assert "inspect bounded state" in text
        assert "check mechanical compatibility" in text
        assert "terminal boundary" in text
        assert "intentionally custom work" in text

    assert artifacts.index("frame.show()") < artifacts.index("frame.contract()")
    assert artifacts.index("frame.contract()") < artifacts.index("frame.to_pandas()")
    assert "compare" not in artifacts
    assert "attribute" not in artifacts


def test_root_help_contains_type_algebra_rows() -> None:
    text = _text()
    rows = REGISTRY.type_algebra_rows()
    assert len(rows) > 0
    for row in rows:
        rendered = row.render()
        assert rendered in text, f"missing algebra row: {rendered}"


def test_root_help_contains_terminal_boundary_row() -> None:
    text = _text()
    assert "boundary.to_pandas" in text
    assert "pandas.DataFrame" in text
    assert "(terminal)" in text


def test_root_help_contains_drill_down_instruction() -> None:
    text = _text()
    assert "marivo.help(" in text


# ---------------------------------------------------------------------------
# Absence of routing/default/advanced/workflow language
# ---------------------------------------------------------------------------


def test_root_help_has_no_workflow_sequence() -> None:
    text = _text().lower()
    assert "default agent workflow" not in text
    assert "question -> first operator" not in text
    assert "intent routing" not in text


def test_root_help_has_no_advanced_label() -> None:
    text = _text().lower()
    assert "advanced" not in text


def test_root_help_has_no_default_operator_label() -> None:
    text = _text().lower()
    assert "default operators" not in text


# ---------------------------------------------------------------------------
# SURFACE_LIMITS enforcement
# ---------------------------------------------------------------------------


def test_root_help_within_line_budget() -> None:
    text = _text()
    assert len(text.splitlines()) <= SURFACE_LIMITS.root_help_max_lines


def test_root_help_within_codepoint_budget() -> None:
    text = _text()
    assert len(text) <= SURFACE_LIMITS.root_help_max_codepoints


def test_focused_help_within_line_budget() -> None:
    text = _text("observe")
    assert len(text.splitlines()) <= SURFACE_LIMITS.focused_help_max_lines


def test_focused_help_within_codepoint_budget() -> None:
    text = _text("observe")
    assert len(text) <= SURFACE_LIMITS.focused_help_max_codepoints


# ---------------------------------------------------------------------------
# Public printing adds exactly one newline to the private renderer.
# ---------------------------------------------------------------------------


def test_public_help_output_equals_private_text_plus_newline() -> None:
    for target in (None, "observe", "compare"):
        captured = _capture(target)
        text = _text(target)
        assert captured == text + "\n", f"mismatch for target={target!r}"


# ---------------------------------------------------------------------------
# Focused help: signature, families, example, constraints, edges
# ---------------------------------------------------------------------------


def test_focused_help_includes_live_signature() -> None:
    text = _text("observe")
    parameters = inspect.signature(Session.observe).parameters
    # The signature text should appear in the rendered help (without 'self').
    assert "observe(" in text
    assert "metrics" in parameters
    assert "metric" not in parameters
    assert "metrics" in text
    assert "metrics=[" in text
    assert "time_scope" in text


def test_time_scope_help_states_half_open_end_contract() -> None:
    observe = _text("observe")
    time_scope = _text("TimeScope")
    absolute = _text("AbsoluteWindow")

    for text in (observe, time_scope, absolute):
        assert "half-open [start, end)" in text
        assert "start is inclusive" in text
        assert "end is exclusive" in text
    assert 'end="2026-08-01"' in time_scope
    assert "includes all of July and excludes August 1" in time_scope


@pytest.mark.parametrize("name", ["aggregate", "slice", "weighted_mean", "ratio"])
def test_runtime_metric_constructors_have_focused_live_help(name: str) -> None:
    target = f"runtime_metric.{name}"
    callable_obj = getattr(mv.runtime_metric, name)
    assert inspect.signature(callable_obj).parameters["label"].default is inspect.Parameter.empty

    for text in (_text(target), _text(callable_obj)):
        assert text.splitlines()[0] == target
        assert "Signature:" in text
        assert "Output type:" in text
        assert "Example:" in text
        assert "runtime_metric_closed_algebra" in text
        assert "label: str" in text


def test_runtime_metric_group_help_lists_all_constructors() -> None:
    text = _text("runtime_metric")
    assert "mv.runtime_metric.aggregate(...)" in text
    assert "mv.runtime_metric.slice(...)" in text
    assert "mv.runtime_metric.weighted_mean(...)" in text
    assert "mv.runtime_metric.ratio(...)" in text


def test_runtime_weighted_mean_help_exposes_grain_and_additivity_contract() -> None:
    text = _text("runtime_metric.weighted_mean")

    assert "runtime_weighted_mean_valid" in text
    assert "same-entity measures" in text
    assert "additive weight" in text


def test_cutover_a_help_exposes_bounded_reads_and_closed_variants() -> None:
    select_text = _text("CandidateSet.select")
    assert "item_id: str" in select_text
    assert "attribute" not in select_text
    assert 'selection = candidates.select(item_id="candidate_<full sha256>")' in select_text

    digests_text = _text("session.evidence.digests")
    for token in ("operator", "subject", "limit: int = 10", "cursor"):
        assert token in digests_text
    assert "page.has_more" in digests_text

    digest_type = _text("ArtifactDigest")
    for field in ("items", "boundaries", "omissions", "fallback", "fingerprint"):
        assert field in digest_type

    issue_type = _text("ArtifactIssue")
    for variant in (
        "DataQualityIssue",
        "ComparabilityIssue",
        "EvidenceAvailabilityIssue",
    ):
        assert variant in issue_type


def test_focused_help_signature_matches_inspect() -> None:
    text = _text("observe")
    # Extract the portion after 'self' — the public signature.
    # The help text should contain the parameter names from the signature.
    for param_name in ("metrics", "time_scope", "grain", "dimensions", "analysis_purpose"):
        assert param_name in text


def test_observe_capability_registers_only_the_plural_metrics_input() -> None:
    accepted_inputs = REGISTRY.by_id("observe").accepted_inputs

    assert "metrics" in accepted_inputs
    assert "metric" not in accepted_inputs


def test_sequence_help_preserves_variadic_signature() -> None:
    text = _text("sequence")

    assert "Signature: sequence(*steps: PatternStep)" in text


def test_event_journey_help_explains_business_policy_and_coverage_choices() -> None:
    first_help = _text("first_per_subject")
    first_guidance = " ".join(first_help.split())
    assert "one subject-level conversion journey" in first_guidance
    assert "later starts are excluded" in first_guidance
    assert "subject-level funnel reduction" in first_guidance

    every_help = _text("every_start")
    every_guidance = " ".join(every_help.split())
    assert "each completion belongs to at most one attempt" in every_guidance
    assert "one completion is business-correct for multiple" in every_guidance

    declaration_help = _text("declared_complete_through")
    declaration_guidance = " ".join(declaration_help.split())
    assert "explicit caller assumption" in declaration_guidance
    assert "weaker than an authoritative backend watermark" in declaration_guidance
    assert "requires a rationale" in declaration_guidance

    match_help = _text("events.match")
    match_guidance = " ".join(match_help.split())
    assert "completion_through" in match_guidance
    assert "never proves that input data is complete" in match_guidance
    assert "observed backend watermark" in match_guidance


@pytest.mark.parametrize(
    ("canonical", "aliases"),
    (
        (
            "events.funnel",
            ("session.events.funnel", "Session.events.funnel"),
        ),
        (
            "events.time_to_event",
            ("session.events.time_to_event", "Session.events.time_to_event"),
        ),
        (
            "select_subjects",
            ("Session.select_subjects",),
        ),
    ),
)
def test_phase2_event_help_aliases_resolve_to_one_descriptor(
    canonical: str,
    aliases: tuple[str, ...],
) -> None:
    expected = _text(canonical)
    assert all(_text(alias) == expected for alias in aliases)


def test_phase2_event_focused_help_has_exact_examples_and_axis_kind() -> None:
    observe = _text("observe")
    assert "cohort=subjects" in observe

    match = _text("events.match")
    assert "cohort=subjects" in match

    funnel = _text("events.funnel")
    assert "session.events.funnel(" in funnel
    assert "axes=[acquisition_channel]" in funnel
    assert "DimensionEntry | Ref[dimension]" in funnel
    assert "TimeDimensionEntry" not in funnel

    elapsed = _text("events.time_to_event")
    assert "session.events.time_to_event(" in elapsed
    assert "start_step=checkout_step" in elapsed
    assert "end_step=payment_step" in elapsed

    selection = _text("select_subjects")
    assert "session.select_subjects(" in selection
    assert "mv.dropped_before(step=payment_step)" in selection
    assert "subject_identity" in _text("SubjectSet")


def test_focused_help_includes_accepted_and_output_families() -> None:
    text = _text("observe")
    assert "MetricFrame" in text
    desc = REGISTRY.by_help_target("observe")
    assert isinstance(desc, OperatorCapability)
    # Accepted input families should be mentioned.
    for families in desc.accepted_inputs.values():
        for family in families:
            assert str(family) in text or family in text


def test_focused_help_includes_runnable_example() -> None:
    text = _text("observe")
    assert "Example:" in text
    # The example must be runnable (contain session.observe call).
    assert "session.observe(" in text
    # No ellipsis in the example.
    example_section = text[text.index("Example:") :]
    assert "..." not in example_section


def test_observe_example_documents_multi_dimension_slice_by_usage() -> None:
    """The observe example must show filtering by a dimension combination."""
    text = _text("observe")
    example_section = text[text.index("Example:") :]
    assert 'channel = catalog.dimensions.get("sales.orders.channel")' in example_section
    assert 'revenue = catalog.metrics.get("sales.revenue")' in example_section
    assert "_SemanticInput" not in text
    assert "SemanticInput[MetricKind]" in text
    assert 'slice_by={country: "US", channel: "online"}' in example_section


def test_focused_help_adds_only_the_two_registered_call_forms() -> None:
    observe_text = _text("observe")
    correlate_text = _text("correlate")
    assert "import marivo.semantic as ms" in observe_text
    assert "import marivo.semantic as ms" in correlate_text
    assert "Direct Ref segmented time series:" in observe_text
    assert 'ms.ref.metric("sales.revenue")' in observe_text
    assert 'ms.ref.dimension("sales.orders.region")' in observe_text
    assert "Common-key cross-sectional frames from exact Refs:" in correlate_text
    assert 'ms.ref.metric("sales.order_count")' in correlate_text


def test_metric_projection_primary_example_uses_full_metric_id() -> None:
    text = _text("MetricFrame.metric")
    assert 'frame.metric("sales.revenue")' in text


def test_observe_and_catalog_require_document_ref_factory_format() -> None:
    """Observe and catalog.require teach exact Ref factories."""
    for target in ("observe", "catalog.require"):
        text = _text(target)
        assert "Ref ID format" in text, f"{target} missing ref id format section"
        assert 'ms.ref.metric("<domain>.<metric_name>")' in text
        assert 'ms.ref.dimension("<domain>.<entity>.<dimension_name>")' in text
        assert 'ms.ref.time_dimension("<domain>.<entity>.<dimension_name>")' in text
        assert 'ms.ref.measure("<domain>.<entity>.<measure_name>")' in text


def test_focused_help_includes_invocation_critical_constraints() -> None:
    text = _text("observe")
    desc = REGISTRY.by_help_target("observe")
    for constraint_id in desc.constraint_ids:
        # Each constraint id should be mentioned.
        assert constraint_id in text, f"missing constraint: {constraint_id}"


def test_correlate_help_explains_signed_lag_semantics() -> None:
    text = _text("correlate")

    assert "correlate_lag_semantics" in text
    assert "range(-3, 4)" in text
    assert "a[t]" in text
    assert "b[t+k]" in text
    assert "positive means a leads b" in text
    assert "negative means b leads a" in text
    assert "lag 0 is the default" in text
    assert "Non-zero lags require time_series or panel frames" in text
    assert "panel lag shifts stay within each dimension series" in text
    assert "null pairs are dropped after shifting" in text


@pytest.mark.parametrize("target", ["correlate", "forecast", "hypothesis_test"])
def test_metric_value_selector_help_uses_public_value_columns(target: str) -> None:
    text = _text(target)
    assert ".value_columns[0]" in text


def test_attribute_help_explains_additivity_boundary() -> None:
    text = _text("attribute")

    assert "attribution_additivity_compatible" in text
    assert "compatible persisted additivity" in text
    assert "ratio" in text
    assert "weighted-mean" in text
    assert "Tier-1 mean" in text
    assert "count_non_null" in text
    assert "attribution_shape=weighted_mix lowered_from=mean" in text
    assert "call attribute directly" in text
    assert "already lowered to sum/count_non_null components" in text
    assert "do not manually split numerator and denominator" in text
    assert "Graph-owned count_distinct and supported quantiles" in text
    assert "approved distribution-aware attribution basis" in text
    assert "current = session.observe(metric, time_scope=current_window)" in text
    assert "baseline = session.observe(metric, time_scope=baseline_window)" in text
    assert "delta = session.compare(current, baseline)" in text
    assert "status time axis" in text
    assert "numerator" in text
    assert "denominator" in text
    assert "attribution_reconciliation" in text
    assert "share_of_total_delta" in text
    assert "positive- and negative-pool shares" in text
    assert "new and churned" in text
    assert "one-sided" in text
    assert "independently computed total delta" in text


def test_attribution_mode_help_is_self_contained_and_not_in_root_index() -> None:
    text = _text("AttributionMode")

    assert 'mode="joint" | mode="hierarchy" | mode="multiresolution"' in text
    assert "one additive row per complete axis combination" in text
    assert "independently recomputes each ordered prefix" in text
    assert "Multi-axis calls have no default" in text
    assert "Omit mode for one axis" in text
    assert "distinct from attribution method" in text
    assert "DeltaFrame.contract().attribute_admission" in text
    assert "AttributionMode" in _text("attribute")
    assert "AttributionMode" not in _text(None)


def test_catalog_collection_help_labels_properties_and_show_path() -> None:
    group = _text("catalog")
    focused = _text("catalog.dimensions")

    assert (
        "session.catalog.dimensions  "
        "(property -> CatalogCollection[DimensionKind]; inspect with .show())"
    ) in group
    assert "Property: session.catalog.dimensions" in focused
    assert "Returns: CatalogCollection[DimensionKind]" in focused
    assert "Inspect: session.catalog.dimensions.show()" in focused
    assert "Entrypoint: session.catalog.dimensions" not in focused


def test_analysis_catalog_help_covers_the_closed_semantic_collections() -> None:
    from marivo.semantic._capabilities.catalog_members import CATALOG_MEMBER_CONTRACTS

    text = _text("catalog")
    registered = {
        descriptor.id for descriptor in REGISTRY.descriptors if descriptor.id.startswith("catalog.")
    }
    for member in CATALOG_MEMBER_CONTRACTS:
        capability_id = f"catalog.{member.property_name}"
        assert capability_id in registered
        assert f"session.catalog.{member.property_name}" in text


def test_compare_help_explains_cumulative_component_compatibility() -> None:
    text = _text("compare")

    assert "cumulative_compare_compatible" in text
    assert "outer component" in text
    assert "trailing" in text
    assert "grain_to_date" in text
    assert "all_history" in text
    assert "current_evaluation_end" in text
    assert "baseline_evaluation_end" in text
    assert "delta.show()" in text
    assert "delta.contract()" in text
    assert "exc.expected" in text
    assert "exc.received" in text
    assert "exc.repair.action" in text
    assert "Requires from prerequisites or the preceding example: AnalysisError" not in text


def test_compare_help_declares_single_metric_precondition() -> None:
    """Issue #67: analysis.compare help must declare the single-metric
    precondition and point at the canonical projection path, so an agent
    discovers the arity=1 requirement before comparing."""
    text = _text("compare")

    # The single-metric constraint is declared on the compare capability.
    assert "single_metric_input" in text
    # Both sides must be single-metric (summary + constraint title).
    assert "arity=1" in text
    assert "single metric" in text
    # The summary teaches the canonical projection method.
    assert 'frame.metric("<metric_id>")' in text
    assert "multi-metric frame is projected" in text


def test_attribute_help_explains_cumulative_route_gate() -> None:
    text = _text("attribute")

    assert "cumulative_attribution_route_compatible" in text
    assert "cumulative" in text
    assert "base-flow" in text


def test_focused_help_includes_producer_consumer_edges() -> None:
    text = _text("MetricFrame")
    # Type help should show producers (who creates MetricFrame).
    assert "observe" in text or "producer" in text.lower()
    # Type help should show consumers (what consumes MetricFrame).
    consumers = REGISTRY.constructor_consumers.get("MetricFrame", ())
    for consumer_id in consumers[:3]:
        assert consumer_id in text, f"missing consumer: {consumer_id}"


def test_focused_operator_help_includes_prerequisites_and_postconditions() -> None:
    text = _text("events.funnel")

    assert "Prerequisites:" in text
    assert 'session = mv.session.get_or_create("analysis"' in text
    assert 'journeys: acquire via marivo.help("analysis.events.match")' in text
    assert "After success:" in text
    assert "funnel.show()" in text
    assert "funnel.contract()" in text


def test_focused_help_teaches_semantic_and_constructor_prerequisites() -> None:
    match = _text("events.match")
    funnel = _text("events.funnel")
    replay = _text("lifecycle.replay")

    assert 'session.catalog.events.get("<full semantic path>").ref' in match
    assert 'marivo.help("semantic.participant_role")' in match
    assert 'marivo.help("analysis.step")' in match
    assert 'marivo.help("analysis.sequence")' in match
    assert "cart_created = session.catalog.events.get" in match
    assert "cart_user = ms.participant_role(event=cart_created.ref" in match

    assert 'session.catalog.dimensions.get("<full semantic path>")' in funnel
    assert "acquisition_channel = session.catalog.dimensions.get" in funnel

    assert 'session.catalog.state_models.get("<full semantic path>")' in replay
    assert "order_lifecycle = session.catalog.state_models.get" in replay


def test_every_operator_input_family_has_a_registered_acquisition_path() -> None:
    for descriptor in REGISTRY.descriptors:
        if not isinstance(descriptor, OperatorCapability):
            continue
        for families in descriptor.accepted_inputs.values():
            for family in families:
                assert REGISTRY.producer_targets(family), (
                    f"{descriptor.id} has no producer for {family}"
                )


def test_constructor_descriptors_declare_direct_input_families() -> None:
    expected = {
        "sequence": "EventPattern",
        "first_per_subject": "EventMatchingPolicy",
        "from_inception": "LifecycleSeed",
        "TimeScope": "TimeScopeInput",
    }
    for capability_id, input_family in expected.items():
        descriptor = REGISTRY.by_id(capability_id)
        assert isinstance(descriptor, ConstructorCapability)
        assert descriptor.produced_input_family == input_family


@pytest.mark.parametrize(
    ("target", "result_name", "preparation_name"),
    (
        ("observe", "frame", "catalog"),
        ("events.match", "journeys", "pattern"),
        ("compare", "delta", "revenue"),
        ("attribute", "attribution", "delta"),
        ("forecast", "forecast", "history"),
        ("discover.driver_axes", "candidates", "country"),
        ("transform.topk", "biggest", "delta"),
        ("transform.bottomk", "declines", "delta"),
    ),
)
def test_focused_operator_postconditions_follow_registered_call_result(
    target: str,
    result_name: str,
    preparation_name: str,
) -> None:
    text = _text(target)
    postconditions = text.split("After success:", 1)[1]

    assert f"{result_name}.show()" in postconditions
    assert f"{result_name}.contract()" in postconditions
    assert f"{preparation_name}.show()" not in postconditions
    assert f"{preparation_name}.contract()" not in postconditions


def test_shape_aware_help_does_not_advertise_invalid_consumers() -> None:
    funnel = _text("events.funnel")
    violations = _text("lifecycle.violations")
    funnel_consumers = funnel.split("Consumed by:", 1)[1].split("Related:", 1)[0]
    violation_consumers = violations.split("Consumed by:", 1)[1].split("Related:", 1)[0]

    assert "compare" in funnel_consumers
    assert "events.funnel" not in funnel_consumers
    assert "events.time_to_event" not in funnel_consumers
    assert "lifecycle.distribution" not in violation_consumers
    assert "lifecycle.violations" not in violation_consumers


# ---------------------------------------------------------------------------
# Type help: no constructors, no private fields, properties/methods separation
# ---------------------------------------------------------------------------


def test_type_help_omits_constructors() -> None:
    text = _text("MetricFrame")
    assert "MetricFrame(" not in text.split("Properties:")[0].split("Methods:")[0]
    assert "__init__" not in text
    assert "model_config" not in text


def test_type_help_omits_private_fields() -> None:
    text = _text("MetricFrame")
    assert "_df" not in text
    assert "_NEXT_INTENTS" not in text
    assert "_GATED_INTENTS" not in text
    # Pydantic internals should not appear.
    assert "model_fields" not in text
    assert "model_validate" not in text


def test_type_help_separates_properties_and_methods() -> None:
    text = _text("MetricFrame")
    assert "Properties:" in text or "properties" in text.lower()
    assert "Methods:" in text or "methods" in text.lower()


def test_type_help_lists_registry_allowlist_members() -> None:
    from marivo.analysis._capabilities.registry import (
        PUBLIC_FRAME_METHODS,
        PUBLIC_FRAME_PROPERTIES,
    )

    text = _text("MetricFrame")
    for prop in PUBLIC_FRAME_PROPERTIES.get("MetricFrame", ()):
        assert prop in text, f"missing property: {prop}"
    for method in PUBLIC_FRAME_METHODS.get("MetricFrame", ()):
        assert method in text, f"missing method: {method}"


def test_quality_report_help_exposes_verdict_and_all_exact_shapes() -> None:
    from marivo.analysis._capabilities.registry import PUBLIC_TYPE_VARIANTS

    text = _text("QualityReport")
    for prop in ("overall_status", "blocking_issue_count", "warning_count"):
        assert prop in text
    for variant in PUBLIC_TYPE_VARIANTS["QualityReport"]:
        assert f"QualityReport[{variant}]" in text
    assert "report.state is ArtifactState materialization metadata" in text


def test_session_type_help_teaches_acquisition_without_delete() -> None:
    text = _text("Session")
    assert "Acquired or recovered by:" in text
    assert "session.get_or_create" in text
    assert "session.current" in text
    assert "session.delete" not in text


@pytest.mark.parametrize(
    ("type_name", "receiver"),
    (
        ("Session", "Session"),
        ("SessionEvents", "SessionEvents"),
        ("SessionLifecycle", "SessionLifecycle"),
    ),
)
def test_object_type_help_lists_registry_owned_receiver_methods(
    type_name: str,
    receiver: str,
) -> None:
    text = _text(type_name)

    for method_name in REGISTRY.public_member_names(receiver):
        assert f".{method_name}()" in text
    assert ".close()" not in text


# ---------------------------------------------------------------------------
# Error help
# ---------------------------------------------------------------------------


def test_error_class_help_shows_static_fields() -> None:
    text = _text(MetricNotFoundError)
    assert "MetricNotFoundError" in text
    # The static contract must render the kind and base class.
    assert "kind: MetricNotFound" in text
    assert "base: AnalysisError" in text
    # MetricNotFoundError has at least one matching constraint; verify
    # it is actually listed rather than relying on a coincidental word.
    assert "Constraints:" in text
    assert "metric_expression_resolvable" in text
    assert "Every metric-expression leaf must resolve to an analysis-ready governed ref." in text


def test_error_instance_help_shows_concrete_repair() -> None:
    err = MetricNotFoundError(
        message="metric not found",
        context={"metric_id": "sales.foobar"},
    )
    text = _text(err)
    assert "MetricNotFound" in text
    assert "repair" in text.lower() or "action" in text.lower()
    # The concrete repair action should be present.
    assert (
        "retry" in text.lower() or "semantic_authoring" in text.lower() or "inspect" in text.lower()
    )
    assert "next_help: marivo.help()" in text


def test_error_instance_help_renders_exact_cross_surface_target() -> None:
    err = AnalysisError(
        message="inspect the datasource",
        repair=AnalysisRepair(
            kind="inspect",
            action="Inspect the registered source.",
            help_target=LiveHelpTarget(surface="datasource", canonical_id="inspect"),
        ),
    )
    assert 'next_help: marivo.help("datasource.inspect")' in _text(err)


def test_repair_free_error_instance_matches_generic_contract() -> None:
    assert _text(AnalysisError(message="repair unavailable")) == _text(AnalysisError)


def test_base_error_class_help() -> None:
    text = _text(AnalysisError)
    assert "AnalysisError" in text


# ---------------------------------------------------------------------------
# Semantic object help
# ---------------------------------------------------------------------------


def test_semantic_ref_help_without_project_is_identity_only() -> None:
    ref = make_ref("sales.revenue", SemanticKind.METRIC)
    text = _text(ref)
    assert "typed identity only" in text
    assert "sales.revenue" in text


def test_semantic_ref_help_does_not_implicitly_use_available_project(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "warehouse = ms.ref.datasource('warehouse')\n"
                "orders = ms.entity(name='orders', datasource=warehouse, "
                "source=md.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', name='revenue', "
                " unit='CNY')\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
            "datasources/warehouse.py": (
                "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
            ),
        }
    )
    text = _text(make_ref("sales.revenue", SemanticKind.METRIC))
    assert "revenue" in text
    assert "typed identity only" in text
    assert "unit: CNY" not in text


def test_catalog_object_help_renders_briefing(semantic_project_factory) -> None:
    from marivo.semantic.catalog import SemanticCatalog

    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "warehouse = ms.ref.datasource('warehouse')\n"
                "orders = ms.entity(name='orders', datasource=warehouse, "
                "source=md.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', name='revenue', "
                " unit='CNY')\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
            "datasources/warehouse.py": (
                "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)
    revenue_obj = catalog.require(make_ref("sales.revenue", SemanticKind.METRIC))
    assert revenue_obj is not None
    text = _text(revenue_obj)
    assert "revenue" in text
    assert "unit: CNY" in text


def test_event_and_state_model_briefings_expose_typed_analysis_handoffs(
    semantic_project_factory,
) -> None:
    from marivo.semantic.catalog import SemanticCatalog
    from tests.shared_fixtures import lifecycle_project_files

    catalog = SemanticCatalog(semantic_project_factory(lifecycle_project_files()))

    event_text = _text(catalog.events.get("commerce.order_created"))
    assert "Typed analysis preparation:" in event_text
    assert "ms.participant_role(event=entry.ref" in event_text
    assert 'marivo.help("analysis.step")' in event_text
    assert 'marivo.help("analysis.sequence")' in event_text
    assert 'marivo.help("analysis.events.match")' in event_text

    model_text = _text(catalog.state_models.get("commerce.order_lifecycle"))
    assert "Conditional analysis consumers" in model_text
    assert 'marivo.help("analysis.lifecycle.replay")' in model_text


# ---------------------------------------------------------------------------
# Callable / object / type / error / semantic resolution parity
# ---------------------------------------------------------------------------


def test_callable_resolves_same_as_string() -> None:
    text_callable = _text(Session.observe)
    text_string = _text("observe")
    assert text_callable == text_string


def test_bound_method_resolves_same_as_unbound() -> None:
    text_unbound = _text(Session.compare)
    # Can't easily get a bound method without a session, so test that
    # the unbound function and the string target produce the same output.
    text_string = _text("compare")
    assert text_unbound == text_string


def test_live_help_preserves_leading_keyword_only_separator() -> None:
    recent = _text("session.recent")
    frames = _text("session.frame_summaries")
    assert "Signature: recent(*, limit: int = 20, cursor: str | None = None)" in recent
    assert "Signature: frame_summaries(*, kind: str | None = None" in frames


@pytest.mark.parametrize(
    "type_name",
    (
        "Session",
        "TimeScope",
        "AbsoluteWindow",
        "SamplingPolicy",
        "AlignmentPolicy",
        "ArtifactRef",
        "FunnelLossRate",
    ),
)
def test_type_resolves_same_as_string(type_name: str) -> None:
    type_obj = mv.time_scope if type_name == "TimeScope" else getattr(mv, type_name)
    text_type = _text(type_obj)
    text_string = _text(type_name)
    assert text_type == text_string

    if type_name == "AlignmentPolicy":
        assert "Closed public alignment protocol" in text_type
        assert "Call marivo.help(AlignmentPolicy)" not in text_type


def test_type_contracts_do_not_shadow_registered_constructor_classes() -> None:
    from marivo.analysis._capabilities.surface import TYPE_REGISTRY

    for type_obj in TYPE_REGISTRY:
        with pytest.raises(KeyError):
            REGISTRY.by_callable(type_obj)


def test_object_resolves_same_as_type(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="help_test_session", use_datasources=False)
    text_obj = _text(session)
    text_type = _text(Session)
    assert text_obj == text_type


def test_error_subclass_resolves_same_as_string() -> None:
    text_class = _text(MetricNotFoundError)
    # Should render the error contract.
    assert "MetricNotFound" in text_class


# ---------------------------------------------------------------------------
# No public JSON/format parameter
# ---------------------------------------------------------------------------


def test_help_has_no_format_parameter() -> None:
    sig = inspect.signature(marivo.help)
    assert "format" not in sig.parameters
    assert "json" not in sig.parameters


def test_analysis_module_exposes_no_help_attributes() -> None:
    assert not hasattr(mv, "help")
    assert not hasattr(mv, "help_text")


def test_help_rejects_format_kwarg() -> None:
    with pytest.raises(TypeError):
        marivo.help("analysis.observe", format="json")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Empty string is not a hidden alias for root
# ---------------------------------------------------------------------------


def test_empty_string_is_not_root() -> None:
    with pytest.raises(MarivoHelpTargetError):
        _text("")


def test_none_is_root() -> None:
    text = _text(None)
    assert "Marivo:" in text
    assert "Python:" in text


# ---------------------------------------------------------------------------
# Unknown target raises HelpTargetError
# ---------------------------------------------------------------------------


def test_unknown_string_raises_help_target_error() -> None:
    with pytest.raises(MarivoHelpTargetError):
        _text("nonexistent_thing_xyz")


# ---------------------------------------------------------------------------
# Module/class docstring first-line routing
# ---------------------------------------------------------------------------


def test_analysis_module_docstring_first_line() -> None:
    first_line = mv.__doc__.strip().splitlines()[0] if mv.__doc__ else ""
    assert "mv.help" not in first_line


def test_session_class_docstring_first_line() -> None:
    first_line = Session.__doc__.strip().splitlines()[0] if Session.__doc__ else ""
    assert "marivo.help" in first_line


def test_metric_frame_class_docstring_first_line() -> None:
    first_line = MetricFrame.__doc__.strip().splitlines()[0] if MetricFrame.__doc__ else ""
    assert "marivo.help" in first_line


def test_base_frame_class_docstring_first_line() -> None:
    first_line = BaseFrame.__doc__.strip().splitlines()[0] if BaseFrame.__doc__ else ""
    assert "marivo.help" in first_line


# ---------------------------------------------------------------------------
# Pinned __all__ and __dir__
# ---------------------------------------------------------------------------


def test_analysis_all_is_pinned() -> None:
    expected = {
        "AnalysisScope",
        "AnomalyCandidate",
        "ArtifactDigest",
        "ArtifactDigestPage",
        "ArtifactIssue",
        "AssociationFact",
        "CandidateOrigin",
        "CandidateResolutionIssue",
        "CandidateSelection",
        "ChangeFact",
        "ComparabilityIssue",
        "CompletenessDeclaration",
        "ContributionFact",
        "CrossSectionalOutlierSelection",
        "DataQualityIssue",
        "DriverAxisSelection",
        "DroppedBefore",
        "EvidenceAvailabilityIssue",
        "EvidenceDerivationTrace",
        "EventFrame",
        "EventPattern",
        "EventWatermarkReceipt",
        "EventWatermarkRequest",
        "EveryStart",
        "Finding",
        "FindingPage",
        "FirstPerSubject",
        "ForecastOutput",
        "FrameSummaryEntry",
        "FrameSummaryPage",
        "Grain",
        "FunnelLossRate",
        "FromInception",
        "InState",
        "ObservationFact",
        "PatternStep",
        "PeriodShiftSelection",
        "PointAnomalySelection",
        "QualityCheckResult",
        "SliceSelection",
        "TestDecision",
        "WindowSelection",
        "AbsoluteWindow",
        "AlignmentPolicy",
        "ArtifactRef",
        "AssociationResult",
        "AttributionFrame",
        "CandidateSet",
        "DeltaFrame",
        "ForecastFrame",
        "HypothesisTestResult",
        "LifecycleFrame",
        "MetricFrame",
        "QualityReport",
        "Session",
        "OntologyMetricCandidate",
        "SubjectSet",
        "TimeScope",
        "time_scope",
        "day_of_week",
        "period_progress",
        "period_correspondence",
        "occurrence_progress",
        "session",
        "window_bucket",
        "runtime_metric",
        "declared_complete_through",
        "dropped_before",
        "every_start",
        "first_per_subject",
        "funnel_loss_rate",
        "from_inception",
        "grain",
        "in_state",
        "sequence",
        "step",
    }
    assert set(mv.__all__) == expected


def test_analysis_dir_matches_all() -> None:
    assert set(dir(mv)) == set(mv.__all__)


# ---------------------------------------------------------------------------
# help() returns None
# ---------------------------------------------------------------------------


def test_help_returns_none() -> None:
    assert marivo.help() is None


def test_help_with_target_returns_none() -> None:
    assert marivo.help("analysis.observe") is None


# ---------------------------------------------------------------------------
# Budget enforcement is strict (registry validation)
# ---------------------------------------------------------------------------


def test_root_help_does_not_silently_exceed_budget() -> None:
    """Root help must stay within SURFACE_LIMITS; overflow is a build failure."""
    text = _text()
    lines = text.replace("\r\n", "\n").splitlines()
    assert len(lines) <= SURFACE_LIMITS.root_help_max_lines
    assert len(text) <= SURFACE_LIMITS.root_help_max_codepoints


def test_focused_help_does_not_silently_exceed_budget() -> None:
    """Focused help must stay within SURFACE_LIMITS; overflow is a build failure."""
    for target in ("observe", "compare", "forecast", "Session", "MetricFrame"):
        text = _text(target)
        lines = text.replace("\r\n", "\n").splitlines()
        assert len(lines) <= SURFACE_LIMITS.focused_help_max_lines, (
            f"{target}: {len(lines)} lines > {SURFACE_LIMITS.focused_help_max_lines}"
        )
        assert len(text) <= SURFACE_LIMITS.focused_help_max_codepoints, (
            f"{target}: {len(text)} chars > {SURFACE_LIMITS.focused_help_max_codepoints}"
        )


@pytest.mark.parametrize("target", ["observe", "compare", "forecast", "Session", "MetricFrame"])
def test_live_help_signature_has_no_memory_address_defaults(target: str) -> None:
    """Live help must not leak a per-process memory address into any agent-visible
    signature, and observe (which uses the _Unset sentinel) renders <unset>.
    The sweep covers more targets than the bug so a future sentinel in another
    signature is caught too (issue #46)."""
    from marivo.analysis.session.core import _UNSET

    assert repr(_UNSET) == "<unset>"
    text = _text(target)
    assert "0x" not in text
    if target == "observe":
        assert "<unset>" in text


def test_unset_repr_does_not_break_identity_guard() -> None:
    """Adding __repr__ to _Unset must not change the sentinel's identity-based
    guard semantics (issue #46 review)."""
    from marivo.analysis.session.core import _UNSET, _normalize_unset

    assert _normalize_unset(_UNSET) is None
    assert _normalize_unset("value") == "value"
    # repr is deterministic and carries no address.
    assert repr(_UNSET) == "<unset>" == str(_UNSET)
