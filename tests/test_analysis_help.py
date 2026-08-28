"""Semantic invariants for the native analysis renderer behind unified help."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re

import pytest

import marivo
import marivo.analysis as mv
from marivo._help.model import MarivoHelpTargetError
from marivo.analysis._capabilities.model import (
    AnalysisArtifactFamilyContract,
    AnalysisMethodFamily,
    AnalysisNavigationTopic,
    ConstructorCapability,
    HelpExample,
    OperatorCapability,
)
from marivo.analysis._capabilities.registry import REGISTRY, _validate_additional_examples
from marivo.analysis._capabilities.render import (
    _descriptor_render_budget,
    _extract_example,
    _rendered_help_targets,
    _resolve_callable,
)
from marivo.analysis._capabilities.surface import ERROR_TYPES, TYPE_REGISTRY
from marivo.analysis.constraints import CONSTRAINTS, ConstraintId
from marivo.analysis.errors import (
    AnalysisError,
    AnalysisRepair,
    EvidenceIntegrityError,
    EvidenceSelectionError,
    MetricNotFoundError,
)
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.session.core import Session
from marivo.introspection.live.model import LiveHelpTarget
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
    assert lines[0] == "marivo.analysis"
    assert lines[1].startswith("Marivo: ")
    assert marivo.__version__ in lines[1]
    assert lines[2].startswith("Python: ")
    assert lines[3].startswith("Package: ")


def test_root_help_fingerprint_uses_resolved_paths() -> None:
    from pathlib import Path

    text = _text()
    lines = text.splitlines()
    assert str(Path(marivo.__file__).resolve()) in lines[3]


def test_root_help_contains_no_runnable_workflow_or_inventory() -> None:
    text = _text()
    for forbidden in (
        "Python imports:",
        "First observation:",
        "Signature:",
        "Example:",
        "session.observe",
        "mv.grain",
        "MetricFrame",
        "session.get_or_create",
    ):
        assert forbidden not in text


def test_focused_help_documents_mv_namespace_import() -> None:
    """Focused pages also use ``mv.`` and must carry the import hint."""
    text = _text("observe")
    assert "Python imports:" in text
    assert "import marivo.analysis as mv" in text
    assert f"Marivo: {marivo.__version__}" in text
    # The import hint follows the target name on the first line.
    assert text.splitlines()[0] == "observe"


def test_source_bindings_has_focused_help_and_session_type_member() -> None:
    text = _text("Session.source_bindings")
    session_type = _text("Session")

    assert "session.source_bindings({...})" in text
    assert "Mapping[Ref[EntityKind]" in text
    assert "non-secret" in text
    assert "one Session runtime" in text
    assert "Sequence[str | int | float | bool]" in text
    assert "flat non-empty scalar-list" in text
    assert "source_bindings_exact" in text
    assert ".source_bindings()" in session_type
    assert REGISTRY.by_callable(Session.source_bindings).id == "Session.source_bindings"


# ---------------------------------------------------------------------------
# Progressive root and canonical targets
# ---------------------------------------------------------------------------


def test_root_help_renders_only_the_registry_owned_routes() -> None:
    text = _text()
    rendered_targets = tuple(re.findall(r'marivo\.help\("analysis\.([^"\n]+)"\)', text))
    expected = tuple(
        target.canonical_id for target in REGISTRY.root_members if target.canonical_id is not None
    )
    assert rendered_targets == expected == REGISTRY.discovery_ids()


def test_root_help_routes_to_six_hubs_and_exact_boundary() -> None:
    text = _text()
    for label in (
        "Governed entry",
        "Installed computation families",
        "Construct inputs and policies",
        "Understand Artifact families and reads",
        "Read and validate Evidence",
        "Resume persisted work",
        "Inspect typed-flow exits",
    ):
        assert label in text
    assert 'marivo.help("analysis.boundary.to_pandas")' in text


def test_root_help_never_advertises_grouping_topics_as_session_members() -> None:
    text = _text()
    for fake_entrypoint in (
        "session.session",
        "session.recovery",
        "session.artifacts",
        "session.boundary",
    ):
        assert fake_entrypoint not in text
    assert 'marivo.help("analysis.recovery")' not in text
    assert 'marivo.help("analysis.runtime")' in text
    assert 'marivo.help("analysis.artifacts")' in text


def test_root_recovery_routes_only_to_runtime_hub() -> None:
    root = _text()
    runtime_sessions = _text("runtime.sessions")

    assert 'marivo.help("analysis.runtime")' in root
    for entrypoint in (
        "mv.session.get_or_create(...)",
        "mv.session.current()",
        "mv.session.resume(session_id)",
        "mv.session.recent()",
        "mv.session.inspect(name)",
        "mv.session.delete(name)",
    ):
        assert entrypoint not in root
        assert entrypoint in runtime_sessions


def test_session_resume_focused_help_uses_exact_id_contract() -> None:
    text = _text("session.resume")

    assert "Entrypoint: mv.session.resume(session_id)" in text
    assert "Identity input: session_id" in text
    assert "mv.session.resume(page.items[0].id)" in text
    signature_line = next(line for line in text.splitlines() if "Signature:" in line)
    assert "question" not in signature_line
    assert "report_timezone" not in signature_line


def test_focused_grouping_help_lists_real_members() -> None:
    runtime = _text("runtime")
    assert 'Entrypoint: marivo.help("analysis.runtime")' in runtime
    for target in ("runtime.sessions", "runtime.artifacts", "runtime.jobs"):
        assert f'analysis.{target}")' in runtime
    assert "Session" in runtime
    assert "evidence" in runtime

    artifacts = _text("artifacts")
    assert 'Entrypoint: marivo.help("analysis.artifacts")' in artifacts
    for target in (
        "artifacts.metric_change",
        "artifacts.event_lifecycle",
        "artifacts.discovery_inference",
        "artifacts.quality_projection",
        "artifacts.reading",
    ):
        assert f'analysis.{target}")' in artifacts


def test_entry_help_routes_governed_inputs_and_states_execution_boundaries() -> None:
    text = _text("entry")
    expected_cross_links = (
        LiveHelpTarget(surface="analysis", canonical_id="observe"),
        LiveHelpTarget(surface="analysis", canonical_id="events.match"),
        LiveHelpTarget(surface="analysis", canonical_id="lifecycle.replay"),
        LiveHelpTarget(surface="analysis", canonical_id="catalog"),
        LiveHelpTarget(surface="analysis", canonical_id="catalog.readiness"),
        LiveHelpTarget(surface="semantic", canonical_id="authoring"),
    )

    assert REGISTRY.cross_links("entry") == expected_cross_links
    advertised_members = tuple(
        target
        for target in _rendered_help_targets(text)
        if target != LiveHelpTarget(surface="analysis", canonical_id="entry")
    )
    assert advertised_members == REGISTRY.navigation_topic("entry").members
    for target in expected_cross_links:
        assert target.display in text
        rendered_help(target.canonical_id, owner=target.surface)
    assert 'marivo.help("analysis.entry.event_observations")' in text
    for boundary in (
        "catalog selection != readiness",
        "readiness != current source health",
        "entry execution != analytical conclusion",
    ):
        assert boundary in text


def test_slice3_evidence_help_preserves_proof_boundaries() -> None:
    evidence = _text("evidence")
    browse = _text("evidence.browse")
    exact = _text("evidence.exact")

    for boundary in ("quality", "compatibility", "revalidation", "source freshness"):
        assert boundary in evidence
    for target in (
        "evidence.browse",
        "evidence.exact",
        "session.evidence.compatibility",
        "session.revalidate",
        "BaseFrame.show",
        "BaseFrame.quality_report",
    ):
        assert target in evidence
    assert "healthy empty page" in browse
    assert "unavailable Evidence store" in browse
    assert "session.evidence.digests" in browse
    assert "session.evidence.findings" in browse
    for target in (
        "session.evidence.digest",
        "session.evidence.finding",
        "session.evidence.trace",
    ):
        assert target in exact


def test_slice3_runtime_help_routes_exact_persisted_identities() -> None:
    runtime = _text("runtime")
    sessions = _text("runtime.sessions")
    artifacts = _text("runtime.artifacts")
    jobs = _text("runtime.jobs")

    for identity in ("Session name or id", "Artifact ref", "job id", "Evidence id"):
        assert identity in runtime
    for entrypoint in (
        "mv.session.get_or_create(...)",
        "mv.session.current()",
        "mv.session.recent()",
        "mv.session.inspect(name)",
        "mv.session.resume(session_id)",
        "mv.session.delete(name)",
    ):
        assert entrypoint in sessions
    assert "session.frame_summaries()" in artifacts
    assert "session.get_frame(ref)" in artifacts
    assert "session.revalidate" in artifacts
    for entrypoint in (
        "session.jobs()",
        "session.recent_jobs(limit=5)",
        "session.job(job_id)",
    ):
        assert entrypoint in jobs


def test_event_and_lifecycle_grouping_help_lists_real_members() -> None:
    events = _text("events")
    assert 'marivo.help("analysis.events.match")' in events
    assert 'marivo.help("analysis.events.funnel")' in events
    assert 'marivo.help("analysis.events.time_to_event")' in events
    assert "events.watermark" not in events
    assert "events.occurrence_bounds" not in events

    lifecycle = _text("lifecycle")
    for member in ("replay", "distribution", "transitions", "dwell", "violations"):
        assert f'marivo.help("analysis.lifecycle.{member}")' in lifecycle


def test_alignment_help_routes_only_to_exact_policy_contracts() -> None:
    text = _text("alignment")
    for member in (
        "window_bucket",
        "day_of_week",
        "period_progress",
        "period_correspondence",
        "occurrence_progress",
        "working_day_progress",
    ):
        assert f"mv.{member}" in text
    assert "Signature:" not in text
    assert "Example:" not in text
    assert "holiday_aligned" not in text


def test_sampling_policy_is_routed_directly_without_a_singleton_topic() -> None:
    with pytest.raises(MarivoHelpTargetError) as exc_info:
        _text("sampling")
    assert "analysis.SamplingPolicy" in str(exc_info.value)
    assert "SamplingPolicy" in _text("SamplingPolicy")


def test_working_day_progress_help_exposes_schedule_example_and_constraints() -> None:
    text = _text("working_day_progress")
    assert "schedule: Ref[WorkScheduleKind] | WorkScheduleEntry" in text
    assert "alignment = mv.working_day_progress(schedule=schedule)" in text
    assert "Constraints:" in text
    assert "alignment_policy_shape" in text


def test_grouping_members_use_only_explicit_registry_membership() -> None:
    import marivo.analysis._capabilities.render as render_module

    grouping = REGISTRY.by_help_target("discover")
    members = render_module._grouping_members(grouping)
    assert tuple(member.help_target for member in members) == tuple(
        target.canonical_id for target in REGISTRY.discovery_members("discover")
    )


def test_artifact_help_teaches_progressive_reads_without_planning_analysis() -> None:
    artifacts = _text("artifacts")
    reading = _text("artifacts.reading")

    assert "static Artifact family contracts" in artifacts
    assert "repr -> show/render -> contract -> exact Evidence or rows -> terminal exit" in reading
    assert reading.index("BaseFrame.show") < reading.index("BaseFrame.contract")
    assert "boundary.to_pandas" not in tuple(
        target.canonical_id for target in REGISTRY.discovery_members("artifacts.reading")
    )
    for target in REGISTRY.cross_links("artifacts.reading"):
        assert target.canonical_id in reading
    assert "compare" not in artifacts
    assert "attribute" not in artifacts


def test_slice2_navigation_pages_are_bounded_and_do_not_expand_leaf_contracts() -> None:
    targets = (
        "methods",
        "inputs",
        "artifacts",
        "entry.event_observations",
        "catalog",
        "inputs.scope",
        "alignment",
        "runtime_metric",
        "inputs.events",
        "inputs.subject_selection",
        "inputs.operator_options",
        "inputs.transform_options",
        "methods.change",
        "discover",
        "methods.relationship_testing",
        "events",
        "lifecycle",
        "transform",
        "artifacts.metric_change",
        "artifacts.event_lifecycle",
        "artifacts.discovery_inference",
        "artifacts.quality_projection",
        "artifacts.reading",
    )
    for target in targets:
        descriptor = REGISTRY.by_help_target(target)
        assert isinstance(descriptor, (AnalysisNavigationTopic, AnalysisMethodFamily))
        text = _text(target)
        budget_class = (
            descriptor.render_class
            if isinstance(descriptor, AnalysisNavigationTopic)
            else "navigation"
        )
        budget = REGISTRY.render_budget(budget_class)
        assert len(text.splitlines()) <= budget.max_lines
        assert len(text) <= budget.max_codepoints
        assert "Signature:" not in text
        assert "Example:" not in text


def test_every_artifact_type_page_renders_complete_derived_algebra() -> None:
    for contract in REGISTRY.artifact_contracts:
        assert isinstance(contract, AnalysisArtifactFamilyContract)
        text = _text(contract.canonical_id)
        for target in REGISTRY.artifact_producers(contract.artifact_family):
            assert target.canonical_id in text
        for target in REGISTRY.artifact_consumers(contract.artifact_family):
            assert target.canonical_id in text
        for required in (
            "BaseFrame.show",
            "BaseFrame.contract",
            "boundary.to_pandas",
            "Static consumers are possibilities, not current admission",
        ):
            assert required in text
        if contract.artifact_family == "QualityReport":
            assert "session.evidence.digest" not in text
            assert "session.get_frame" not in text
        else:
            assert "session.evidence.digest" in text
            assert "session.get_frame" in text
        budget = REGISTRY.render_budget("public_type")
        assert len(text.splitlines()) <= budget.max_lines
        assert len(text) <= budget.max_codepoints
        rendered_routes = tuple(re.findall(r'marivo\.help\("analysis\.([^"\n]+)"\)', text))
        assert rendered_routes == tuple(
            target.canonical_id for target in REGISTRY.cross_links(contract.canonical_id)
        )
        assert len(rendered_routes) <= budget.max_outgoing_routes
        assert "Example:" not in text


def test_artifact_type_help_renders_admission_and_nullable_edge_facts() -> None:
    event = _text("EventFrame")
    assert "compare [parameter=current, shapes=funnel, matching=first_per_subject]" in event
    assert (
        "select_subjects [parameter=artifact, shapes=journey, matching=first_per_subject]" in event
    )
    coverage = _text("CoverageFrame")
    assert "MetricFrame.coverage [nullable]" in coverage


def test_slice2_final_render_budget_rejects_import_and_route_overflow() -> None:
    from marivo.analysis._capabilities.render import (
        _enforce_analysis_render_budget,
        _with_python_imports,
    )

    with pytest.raises(RuntimeError, match="surface budget"):
        _with_python_imports(
            "\n".join(f"line {index}" for index in range(69)),
            render_class="public_type",
        )
    too_many_routes = tuple(
        LiveHelpTarget(surface="analysis", canonical_id=f"route.{index}") for index in range(11)
    )
    with pytest.raises(RuntimeError, match="outgoing-route budget"):
        _enforce_analysis_render_budget(
            "bounded",
            render_class="public_type",
            outgoing_routes=too_many_routes,
            examples_or_snippets=0,
        )
    with pytest.raises(RuntimeError, match="example/snippet budget"):
        _enforce_analysis_render_budget(
            "bounded",
            render_class="public_type",
            outgoing_routes=(),
            examples_or_snippets=1,
        )


def test_singleton_method_and_input_contracts_have_no_family_aliases() -> None:
    for removed_topic, direct_target in (
        ("methods.forecast", "forecast"),
        ("methods.quality", "BaseFrame.quality_report"),
        ("inputs.sampling", "SamplingPolicy"),
    ):
        with pytest.raises(MarivoHelpTargetError):
            _text(removed_topic)
        assert _text(direct_target)


def test_slice3_removed_navigation_topics_have_no_alias_fallback() -> None:
    for removed_topic in ("recovery", "session", "boundary", "sampling"):
        with pytest.raises(MarivoHelpTargetError):
            _text(removed_topic)

    boundary = _text("boundary.to_pandas")
    assert "frame.to_pandas()" in boundary
    assert "defensive pandas DataFrame copy" in boundary
    assert "lineage, meta, session_ownership, evidence" in boundary


def test_type_algebra_remains_registered_but_is_not_rendered_in_root_help() -> None:
    root = _text()
    rows = REGISTRY.type_algebra_rows()
    assert len(rows) > 0
    assert "Type algebra:" not in root
    assert rows[-1].render() not in root


def test_root_help_contains_terminal_boundary_row() -> None:
    text = _text()
    assert 'marivo.help("analysis.boundary.to_pandas")' in text
    assert "frame.to_pandas()" not in text
    assert "pandas DataFrame" not in text


def test_root_does_not_repeat_focused_summaries() -> None:
    root = _text()
    observe = _text("observe")
    descriptor = REGISTRY.by_id("observe")

    assert descriptor.summary in observe
    assert descriptor.summary not in root


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
# Analysis-owned render-budget enforcement
# ---------------------------------------------------------------------------


def test_root_help_within_line_budget() -> None:
    text = _text()
    assert len(text.splitlines()) <= REGISTRY.render_budget("root").max_lines


def test_root_help_within_codepoint_budget() -> None:
    text = _text()
    assert len(text) <= REGISTRY.render_budget("root").max_codepoints


def test_focused_help_within_line_budget() -> None:
    text = _text("observe")
    assert len(text.splitlines()) <= REGISTRY.render_budget("exact_callable").max_lines


def test_focused_help_within_codepoint_budget() -> None:
    text = _text("observe")
    assert len(text) <= REGISTRY.render_budget("exact_callable").max_codepoints


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
    time_scope = _text("time_scope")
    absolute = _text("AbsoluteWindow")

    for text in (observe, time_scope, absolute):
        assert "half-open [start, end)" in text
        assert "start is inclusive" in text
        assert "end is exclusive" in text
    assert 'end="2026-08-01"' in time_scope
    assert "includes all of July and excludes August 1" in time_scope


@pytest.mark.parametrize("name", ["aggregate", "slice", "weighted_mean", "ratio", "linear"])
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


def test_runtime_aggregate_help_exposes_temporal_fold_contract() -> None:
    text = _text("runtime_metric.aggregate")

    assert "runtime_metric_fold_requires_semi_additive" in text
    assert "semi-additive measure" in text
    assert "default fold" in text
    assert "explicit fold" in text


def test_runtime_metric_group_help_lists_all_constructors() -> None:
    text = _text("runtime_metric")
    assert "mv.runtime_metric.aggregate(...)" in text
    assert "mv.runtime_metric.slice(...)" in text
    assert "mv.runtime_metric.weighted_mean(...)" in text
    assert "mv.runtime_metric.ratio(...)" in text
    assert "mv.runtime_metric.linear(...)" in text


def test_runtime_weighted_mean_help_exposes_grain_and_additivity_contract() -> None:
    text = _text("runtime_metric.weighted_mean")

    assert "runtime_weighted_mean_valid" in text
    assert "same-entity measures" in text
    assert "additive weight" in text


def test_runtime_linear_help_exposes_commensurable_unit_contract() -> None:
    text = _text("runtime_metric.linear")

    assert "runtime_linear_units_commensurable" in text
    assert "commensurable units" in text


def test_runtime_linear_is_owned_by_closed_algebra_constraint() -> None:
    constraint = CONSTRAINTS[ConstraintId.RUNTIME_METRIC_CLOSED_ALGEBRA]

    assert "runtime_metric.linear" in constraint.applies_to


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

    compatibility_text = _text("session.evidence.compatibility")
    assert "finding_ids: Sequence[str]" in compatibility_text
    assert "Output type: EvidenceCompatibility" in compatibility_text
    assert "immutable_metadata" in compatibility_text
    assert "Read bound: bounded" in compatibility_text

    compatibility_type = _text("EvidenceCompatibility")
    for field in (
        "status",
        "finding_ids",
        "subject_status",
        "scope_status",
        "semantic_status",
        "issues",
        "boundaries",
        "fingerprint",
    ):
        assert field in compatibility_type

    revalidation_text = _text("session.revalidate")
    assert "frame: BaseFrame" in revalidation_text
    assert "Output type: ArtifactRevalidation" in revalidation_text
    assert "immutable_metadata" in revalidation_text
    assert "Read bound: bounded" in revalidation_text

    revalidation_type = _text("ArtifactRevalidation")
    for field in (
        "artifact_ref",
        "content_hash",
        "semantic_status",
        "evidence_status",
        "status",
        "issues",
        "checked_at",
        "authority_fingerprint",
        "fingerprint",
    ):
        assert field in revalidation_type


def test_focused_help_signature_matches_inspect() -> None:
    text = _text("observe")
    # Extract the portion after 'self' — the public signature.
    # The help text should contain the parameter names from the signature.
    for param_name in ("metrics", "time_scope", "grain", "dimensions", "analysis_purpose"):
        assert param_name in text


@pytest.mark.parametrize(
    ("target", "default", "alternatives"),
    [
        ("discover.point_anomalies", "zscore", "seasonal_robust_zscore"),
        ("discover.period_shifts", "delta_window_zscore", None),
        ("discover.driver_axes", "concentration", None),
        ("discover.interesting_slices", "slice_zscore", None),
        ("discover.interesting_windows", "global_zscore_runs", None),
        ("discover.cross_sectional_outliers", "mad", None),
    ],
)
def test_discover_focused_help_discloses_strategy_contract(
    target: str, default: str, alternatives: str | None
) -> None:
    text = _text(target)
    assert "Strategy:" in text
    assert f"default: {default}" in text
    if alternatives is not None:
        assert f"alternatives: {alternatives}" in text
    else:
        assert "alternatives:" not in text
    assert "applies to semantic kinds:" in text
    assert "when to use:" in text


def test_discover_help_distinguishes_period_shift_from_whole_window_compare() -> None:
    # Local regime change is discover; a sustained whole-window shift is compare.
    assert "belongs to compare" in _text("discover.period_shifts")


def test_findings_focused_help_discloses_pagination_contract() -> None:
    text = _text("session.evidence.findings")
    assert "limit: int = 50" in text
    assert "[1, 100]" in text
    assert "has_more" in text
    assert "next_cursor" in text
    assert "cursor=page.next_cursor" in text
    assert "EvidenceLimitError" in text


def test_digests_focused_help_discloses_pagination_bound() -> None:
    text = _text("session.evidence.digests")
    assert "[1, 100]" in text
    assert "has_more" in text
    assert "next_cursor" in text
    assert "EvidenceLimitError" in text


def test_focused_operator_help_discloses_registered_authority_policy() -> None:
    assert "authority: semantic_current" in _text("events.match")
    assert "authority: semantic_current" in _text("attribute")
    assert "authority: materialized_only" in _text("compare")
    assert "authority: materialized_only" in _text("transform.topk")


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
    assert "cohort: SubjectSet" in observe

    match = _text("events.match")
    assert "cohort: SubjectSet" in match

    funnel = _text("events.funnel")
    assert "session.events.funnel(" in funnel
    assert "axes=[acquisition_channel]" in funnel
    assert "DimensionEntry | Ref[dimension]" in funnel
    assert "TimeDimensionEntry" not in funnel

    elapsed = _text("events.time_to_event")
    assert "session.events.time_to_event(" in elapsed
    assert "start_step, end_step = journeys.meta.pattern.steps[:2]" in elapsed
    assert "start_step=start_step" in elapsed
    assert "end_step=end_step" in elapsed

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


def test_metric_frame_coverage_help_declares_nullable_output_and_guard() -> None:
    text = _text("MetricFrame.coverage")

    assert "Output family: CoverageFrame | None" in text
    assert "coverage = frame.coverage()" in text
    assert "if coverage is not None:" in text
    assert "        coverage.show()" in text
    assert "        coverage.contract().show()" in text
    assert "\n    coverage.show()" not in text
    assert "\n    coverage.contract().show()" not in text


def test_observe_example_documents_multi_dimension_slice_by_usage() -> None:
    """The observe example must show filtering by a dimension combination."""
    text = _text("observe")
    example_section = text[text.index("Example:") :]
    assert 'channel = catalog.dimensions.get("sales.orders.channel")' in example_section
    assert 'revenue = catalog.metrics.get("sales.revenue")' in example_section
    assert "_SemanticInput" not in text
    assert "SemanticInput[MetricKind]" in text
    assert 'slice_by={country: "US", channel: "online"}' in example_section


def test_focused_help_routes_supported_semantic_input_forms_without_extra_examples() -> None:
    observe_text = _text("observe")
    correlate_text = _text("correlate")
    assert "import marivo.semantic as ms" in observe_text
    assert "MetricEntry | Ref[metric]" in observe_text
    assert 'marivo.help("semantic.CatalogEntry")' in observe_text
    assert 'marivo.help("semantic.Ref")' in observe_text
    assert "Direct Ref segmented time series:" not in observe_text
    assert "Common-key cross-sectional frames from exact Refs:" not in correlate_text


def test_observe_and_quality_inputs_route_through_family_owners() -> None:
    observe = _text("observe")
    quality = _text("BaseFrame.quality_report")

    assert 'cohort: acquire via marivo.help("analysis.artifacts.event_lifecycle")' in observe
    assert 'marivo.help("analysis.select_subjects")' not in observe
    assert 'marivo.help("analysis.artifacts.metric_change")' in quality
    assert 'marivo.help("analysis.artifacts.event_lifecycle")' in quality
    for producer in ("observe", "compare", "attribute", "events", "lifecycle", "transform"):
        assert f'marivo.help("analysis.{producer}")' not in quality


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


def test_observe_help_documents_ref_readiness_gate() -> None:
    """Observe must surface the ref-level readiness entry point so analysts
    following the help chain can verify refs before materializing a frame.
    """
    text = _text("observe")
    assert "session.catalog.readiness(refs=[metric])" in text
    assert "analysis APIs do not invoke readiness automatically" in text


def test_observe_help_documents_time_dimension_grain_compatibility() -> None:
    text = _text("observe")

    assert "observe_time_grain_compatible" in text
    assert "no finer than the selected time dimension's declared granularity" in text
    assert 'grain=mv.grain("hour")' in text
    assert "time_dimension=hourly_time_dimension" in text


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
    assert "status time axis" in text
    assert "independently computed total delta" in text


def test_attribution_mode_help_is_self_contained_and_not_in_root_index() -> None:
    text = _text("AttributionMode")

    assert 'mode="joint" | mode="hierarchy"' in text
    assert "multiresolution" not in text
    assert "top_k" in _text("attribute")
    assert "one additive row per complete axis combination" in text
    assert "independently reconciled at every prefix" in text
    assert "Metric session.attribute calls default to joint for multiple axes" in text
    assert "Funnel attribution and decompose still require an explicit multi-axis mode" in text
    assert "Omit mode for one axis" in text
    assert "distinct from attribution method" in text
    assert "DeltaFrame.contract().attribute_admission" in text
    assert "AttributionMode" in _text("attribute")
    assert "AttributionMode" not in _text(None)


def test_catalog_collection_help_labels_properties_and_show_path() -> None:
    group = _text("catalog")
    focused = _text("catalog.dimensions")

    assert (
        "catalog.dimensions  (property -> CatalogCollection[DimensionKind]; inspect with .show())"
    ) in group
    assert "Signature:" not in group
    assert "Example:" not in group
    assert "Property: catalog.dimensions" in focused
    assert "Returns: CatalogCollection[DimensionKind]" in focused
    assert "Inspect: catalog.dimensions.show()" in focused
    assert "Entrypoint: catalog.dimensions" not in focused


def test_analysis_catalog_help_covers_the_closed_semantic_collections() -> None:
    from marivo.semantic._capabilities.catalog_members import CATALOG_MEMBER_CONTRACTS

    text = _text("catalog")
    registered = {
        descriptor.id for descriptor in REGISTRY.descriptors if descriptor.id.startswith("catalog.")
    }
    for member in CATALOG_MEMBER_CONTRACTS:
        capability_id = f"catalog.{member.property_name}"
        assert capability_id in registered
        assert f"catalog.{member.property_name}" in text


def test_analysis_catalog_collection_help_teaches_the_full_object_handoff() -> None:
    from marivo.semantic._capabilities.catalog_members import CATALOG_MEMBER_CONTRACTS

    group = _text("catalog")
    assert "Members:" in group
    assert "marivo.help(entry)" not in group

    for member in CATALOG_MEMBER_CONTRACTS:
        text = _text(f"catalog.{member.property_name}")
        collection = f"catalog.{member.property_name}"

        assert f"{collection}.show()" in text
        assert f'{collection}.get("<full semantic path or typed key>")' in text
        assert f"{collection}.get(ref)" in text
        assert "entry.show(); entry.details().show(); marivo.help(entry)" in text
        assert "entry.ref" in text
        assert 'marivo.help("semantic.CatalogEntry")' in text

        show_index = text.index(f"{collection}.show()")
        get_index = text.index(f'{collection}.get("<full semantic path or typed key>")')
        details_index = text.index("entry.details().show()")
        assert show_index < get_index < details_index


def test_analysis_consumers_advertise_catalog_entry_and_ref_handoff() -> None:
    text = _text("observe")

    assert "Semantic object handoff:" in text
    assert "A current CatalogEntry or exact Ref can satisfy the semantic input." in text
    assert "marivo.help(entry)" in text
    assert 'marivo.help("semantic.CatalogEntry")' in text
    assert 'marivo.help("semantic.Ref")' in text


def test_period_calendar_period_help_teaches_exact_scope_navigation() -> None:
    text = _text("calendar.period")

    assert "Entrypoint: calendar.period(level, key)" in text
    assert "Signature: period(level: str, key: str | int | float | bool)" in text
    assert 'calendar = session.catalog.period_calendars.get("sales.fiscal")' in text
    assert 'scope = calendar.period("fiscal_week", "FY2026-W01")' in text
    assert "The calendar must have a current certified snapshot" in text
    assert "Constraints" not in text
    assert "Result kind: immutable_metadata" in text
    assert "Read bound: bounded" in text
    assert "calendar.period(level, key)" in _text("inputs.scope")
    assert "calendar.period(level, key)" not in _text("catalog")


def test_compare_help_explains_cumulative_component_compatibility() -> None:
    text = _text("compare")

    assert "cumulative_compare_compatible" in text
    assert "outer component" in text
    assert "trailing" in text
    assert "grain_to_date" in text
    assert "all_history" in text
    assert "exact evaluation_end coordinates" in text
    assert "delta.show()" in text
    assert "delta.contract().show()" in text
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


@pytest.mark.parametrize(
    "target",
    [
        "correlate",
        "forecast",
        "hypothesis_test",
        "discover.point_anomalies",
        "discover.interesting_slices",
        "discover.interesting_windows",
        "discover.cross_sectional_outliers",
        "transform.filter",
        "transform.normalize",
    ],
)
def test_single_metric_gated_intent_help_declares_arity_precondition(target: str) -> None:
    """Issue #74: every single-metric gated intent must declare the arity=1
    precondition in help, so an agent learns it before tripping MetricArityError."""
    text = _text(target)

    # The single-metric constraint is registered on the capability.
    assert "single_metric_input" in text
    # The constraint title states the arity=1 precondition.
    assert "arity=1" in text


@pytest.mark.parametrize(
    "target",
    ["transform.filter", "transform.topk", "transform.bottomk", "transform.rank"],
)
def test_transform_help_uses_public_value_columns(target: str) -> None:
    text = _text(target)

    assert 'frame.value_columns[0] if isinstance(frame, mv.MetricFrame) else "delta"' in text
    assert 'by="value"' not in text
    assert 'data["value"]' not in text
    assert "public frame column" in text or "public columns" in text

    if target == "transform.rank":
        assert "value is reserved" in text
        assert "canonical MetricFrame storage" in text


def test_quality_report_help_declares_read_only_sidecar_contract() -> None:
    text = _text("BaseFrame.quality_report")

    assert "frame.quality_report()" in text
    assert "Output family: QualityReport | None" in text
    assert "session.assess_quality" not in text


@pytest.mark.parametrize("target", ["discover.period_shifts", "discover.driver_axes"])
def test_delta_frame_only_discover_objectives_do_not_declare_single_metric(target: str) -> None:
    """Issue #74: DeltaFrame-only discover objectives are rejected by family
    before any arity gate, so their help must NOT advertise single_metric_input
    (declaring it would mislead an agent about a precondition that never fires)."""
    assert "single_metric_input" not in _text(target)


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
    assert 'session = mv.session.get_or_create("<stable-session-name>"' in text
    assert 'journeys: acquire via marivo.help("analysis.events.match")' in text
    assert "After success:" in text
    assert "funnel.show()" in text
    assert "funnel.contract().show()" in text


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


def test_focused_help_routes_non_family_parameter_construction() -> None:
    expected = {
        "observe": (
            'grain: build a builtin or certified semantic Grain; help: marivo.help("analysis.grain"), marivo.help("semantic.calendar_grain")',
            'expect_shape: choose one closed expected MetricFrame shape; help: marivo.help("analysis.SemanticShape")',
        ),
        "events.time_to_event": (
            "start_step: select one exact earlier step from journeys.meta.pattern.steps",
            "end_step: select one exact later step from journeys.meta.pattern.steps",
            'marivo.help("analysis.PatternStep")',
            'marivo.help("analysis.step")',
        ),
        "attribute": ("mode: choose the closed multi-axis row layout",),
        "discover.point_anomalies": ('marivo.help("analysis.PointAnomalyStrategy")',),
        "transform.rollup": (
            'marivo.help("analysis.grain")',
            'marivo.help("semantic.calendar_grain")',
        ),
        "transform.rank": ('marivo.help("analysis.RankMethod")',),
        "transform.normalize": (
            'marivo.help("analysis.NormalizeKind")',
            'marivo.help("analysis.NormalizeBaseline")',
        ),
    }
    for target, fragments in expected.items():
        text = _text(target)
        assert "Parameter construction:" in text
        for fragment in fragments:
            assert fragment in text

    distribution = _text("lifecycle.distribution")
    assert "axes: DimensionSemantic (DimensionEntry | Ref[dimension])" in distribution
    assert 'axes: select via session.catalog.dimensions.get("<full semantic path>")' in distribution


@pytest.mark.parametrize(
    ("target", "values"),
    (
        ("SemanticShape", ("scalar", "time_series", "segmented", "panel")),
        ("PointAnomalyStrategy", ("zscore", "seasonal_robust_zscore")),
        ("RankMethod", ("ordinal", "dense", "min", "max")),
        ("NormalizeKind", ("index", "share", "pct_change", "per_unit", "z_score")),
        ("NormalizeBaseline", ('{"value": <number>}', "axis_column")),
    ),
)
def test_parameter_value_contracts_are_focused_only(
    target: str,
    values: tuple[str, ...],
) -> None:
    text = _text(target)
    assert text.startswith(target)
    for value in values:
        assert value in text
    assert target not in _text()


def test_normalize_value_contracts_expose_exact_omission_and_mode_rules() -> None:
    kind = _text("NormalizeKind")
    assert "Required metric normalization mode; there is no default." in kind
    assert "Only index and per_unit accept baseline; per_unit requires it." in kind

    baseline = _text("NormalizeBaseline")
    assert "Omit it for index to use the first series-ordered row overall" in baseline
    assert "the first row per dimension group after time ordering" in baseline
    assert "per_unit requires it" in baseline
    assert "Share, pct_change, and z_score reject it" in baseline


def test_every_parameter_help_target_resolves_on_its_owning_surface() -> None:
    for descriptor in REGISTRY.descriptors:
        if not isinstance(descriptor, OperatorCapability):
            continue
        for contract in descriptor.parameter_help.values():
            for target in contract.help_targets:
                assert target.canonical_id is not None
                assert rendered_help(target.canonical_id, owner=target.surface)


def test_every_operator_input_family_has_a_registered_acquisition_path() -> None:
    for descriptor in REGISTRY.descriptors:
        if not isinstance(descriptor, OperatorCapability):
            continue
        for families in descriptor.accepted_inputs.values():
            for family in families:
                assert REGISTRY.producer_targets(family), (
                    f"{descriptor.id} has no producer for {family}"
                )


def test_semantic_handoffs_choose_one_progressive_entry_path_per_kind() -> None:
    expected = {
        SemanticKind.METRIC: ("observe",),
        SemanticKind.DIMENSION: ("observe",),
        SemanticKind.TIME_DIMENSION: ("observe",),
        SemanticKind.EVENT: ("events.match",),
        SemanticKind.STATE_MODEL: ("lifecycle.replay",),
        SemanticKind.PERIOD_CALENDAR: ("period_progress", "period_correspondence"),
        SemanticKind.TEMPORAL_SET: ("occurrence_progress",),
        SemanticKind.WORK_SCHEDULE: ("working_day_progress",),
    }

    actual: dict[SemanticKind, tuple[str | None, ...]] = {}
    for kind in expected:
        handoff = REGISTRY.semantic_handoff(kind.value)
        assert handoff is not None
        assert all(target.surface == "analysis" for target in handoff.handoff_targets)
        for target in (*handoff.handoff_targets, *handoff.preparation_targets):
            assert target.canonical_id is not None
            assert rendered_help(target.canonical_id, owner=target.surface)
        actual[kind] = tuple(target.canonical_id for target in handoff.handoff_targets)
    assert actual == expected
    event_handoff = REGISTRY.semantic_handoff(SemanticKind.EVENT.value)
    assert event_handoff is not None
    assert tuple(target.canonical_id for target in event_handoff.preparation_targets) == (
        "participant_role",
        "step",
        "sequence",
    )


def test_evidence_and_recovery_links_resolve_independently() -> None:
    owners = (
        "evidence",
        "evidence.browse",
        "evidence.exact",
        "runtime",
        "runtime.sessions",
        "runtime.artifacts",
        "runtime.jobs",
    )
    for owner in owners:
        targets = (*REGISTRY.discovery_members(owner), *REGISTRY.cross_links(owner))
        assert targets
        for target in targets:
            assert target.canonical_id is not None
            assert rendered_help(target.canonical_id, owner=target.surface)


def test_constructor_descriptors_declare_direct_input_families() -> None:
    expected = {
        "sequence": "EventPattern",
        "first_per_subject": "EventMatchingPolicy",
        "from_inception": "LifecycleSeed",
        "time_scope": "TimeScopeInput",
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
        ("transform.topk", "biggest", "frame"),
        ("transform.bottomk", "smallest", "frame"),
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
    assert f"{result_name}.contract().show()" in postconditions
    assert f"{preparation_name}.show()" not in postconditions
    assert f"{preparation_name}.contract().show()" not in postconditions


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
    assert "Typed Evidence reads:" not in text
    assert "Recovery:" not in text
    assert "session.get_frame" not in text
    assert "session.evidence." not in text
    assert 'marivo.help("analysis.BaseFrame.quality_report")' in text
    assert 'marivo.help("analysis.boundary.to_pandas")' in text


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


@pytest.mark.parametrize("error_type", (EvidenceSelectionError, EvidenceIntegrityError))
def test_compatibility_error_help_is_structured(error_type: type[AnalysisError]) -> None:
    text = _text(error_type)
    assert error_type.__name__ in text
    assert "base: AnalysisError" in text


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
    assert "Analysis handoff (kind-level" in event_text
    assert "session.events.match(...) -> EventFrame" in event_text
    assert 'marivo.help("analysis.events.match")' in event_text
    assert "result.contract().show()" in event_text
    assert "participant_role" not in event_text

    model_text = _text(catalog.state_models.get("commerce.order_lifecycle"))
    assert "session.lifecycle.replay(...) -> LifecycleFrame" in model_text
    assert 'marivo.help("analysis.lifecycle.replay")' in model_text
    assert "lifecycle.distribution" not in model_text


def test_temporal_briefings_route_to_their_own_alignment_handoffs(
    semantic_project_factory,
) -> None:
    from marivo.semantic.catalog import SemanticCatalog

    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Data', default=True)\n"
            ),
            "sales/calendar.py": """
import marivo.datasource as md
import marivo.semantic as ms

calendar = ms.entity(
    name="calendar",
    datasource=ms.ref.datasource("warehouse"),
    source=md.table("calendar"),
)
calendar_date = ms.time_dimension_column(
    name="calendar_date", entity=calendar, column="calendar_date", granularity="day"
)
week = ms.dimension_column(name="week", entity=calendar, column="week")
campaign_id = ms.dimension_column(
    name="campaign_id", entity=calendar, column="campaign_id"
)
campaign_start = ms.time_dimension_column(
    name="campaign_start", entity=calendar, column="campaign_start", granularity="day"
)
campaign_end = ms.time_dimension_column(
    name="campaign_end", entity=calendar, column="campaign_end", granularity="day"
)
is_working = ms.dimension_column(
    name="is_working", entity=calendar, column="is_working"
)
ms.period_calendar(
    name="fiscal",
    date=calendar_date,
    boundary_timezone="UTC",
    coverage=(
        __import__("datetime").date(2026, 1, 1),
        __import__("datetime").date(2027, 1, 1),
    ),
    levels={"week": week},
)
ms.temporal_set(
    name="campaigns",
    occurrence_id=campaign_id,
    start=campaign_start,
    end=campaign_end,
    boundary_timezone="UTC",
    coverage=(
        __import__("datetime").date(2026, 1, 1),
        __import__("datetime").date(2027, 1, 1),
    ),
)
ms.work_schedule(
    name="schedule",
    date=calendar_date,
    is_working=is_working,
    boundary_timezone="UTC",
    coverage=(
        __import__("datetime").date(2026, 1, 1),
        __import__("datetime").date(2027, 1, 1),
    ),
)
""",
        }
    )
    catalog = SemanticCatalog(project)

    calendar_text = _text(catalog.period_calendars.get("sales.fiscal"))
    assert "mv.period_progress(...)" in calendar_text
    assert "mv.period_correspondence(...)" in calendar_text

    temporal_set_text = _text(catalog.temporal_sets.get("sales.campaigns"))
    assert "mv.occurrence_progress(...)" in temporal_set_text

    schedule_text = _text(catalog.work_schedules.get("sales.schedule"))
    assert "mv.working_day_progress(...)" in schedule_text

    for text in (calendar_text, temporal_set_text, schedule_text):
        assert "participant_role" not in text
        assert "session.events.match" not in text


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
        "AbsoluteWindow",
        "SamplingPolicy",
        "AlignmentPolicy",
        "ArtifactRef",
        "FunnelLossRate",
    ),
)
def test_type_resolves_same_as_string(type_name: str) -> None:
    type_obj = getattr(mv, type_name)
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
        "ArtifactRevalidation",
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
        "EvidenceCompatibility",
        "EvidenceCompatibilityIssue",
        "EvidenceDerivationTrace",
        "EvidenceIntegrityError",
        "EvidenceRuleIssue",
        "EvidenceSelectionError",
        "EventOccurrenceBounds",
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
        "working_day_progress",
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
    """Root help must stay within its analysis-owned budget."""
    text = _text()
    lines = text.replace("\r\n", "\n").splitlines()
    budget = REGISTRY.render_budget("root")
    assert len(lines) <= budget.max_lines
    assert len(text) <= budget.max_codepoints


def test_every_registered_descriptor_obeys_its_four_dimensional_budget() -> None:
    for descriptor in REGISTRY.descriptors:
        text = _text(descriptor.help_target)
        render_class, example_count = _descriptor_render_budget(descriptor)
        lines = text.replace("\r\n", "\n").splitlines()
        budget = REGISTRY.render_budget(render_class)
        assert len(lines) <= budget.max_lines
        assert len(text) <= budget.max_codepoints
        advertised = _rendered_help_targets(text)
        assert len(advertised) <= budget.max_outgoing_routes
        for target in advertised:
            assert target.canonical_id is not None
            rendered_help(target.canonical_id, owner=target.surface)
        assert example_count <= budget.max_examples_or_snippets


def test_every_public_type_and_error_contract_obeys_its_budget() -> None:
    budget = REGISTRY.render_budget("public_type")
    targets = tuple(dict.fromkeys((*TYPE_REGISTRY.values(), *ERROR_TYPES)))

    for target in targets:
        text = _text(target)
        assert len(text.splitlines()) <= budget.max_lines
        assert len(text) <= budget.max_codepoints
        assert len(_rendered_help_targets(text)) <= budget.max_outgoing_routes
        assert "  Example:" not in text


def test_default_error_instances_obey_current_briefing_budget() -> None:
    budget = REGISTRY.render_budget("current_briefing")

    for error_type in dict.fromkeys(ERROR_TYPES.values()):
        if "message" not in inspect.signature(error_type).parameters:
            continue
        text = _text(error_type(message="budget audit"))
        assert len(text.splitlines()) <= budget.max_lines
        assert len(text) <= budget.max_codepoints
        assert len(_rendered_help_targets(text)) <= budget.max_outgoing_routes
        assert text.count("    snippet:") <= budget.max_examples_or_snippets


def test_every_exact_callable_docstring_example_parses_and_binds_live_signature() -> None:
    def clean(example: str) -> str:
        lines: list[str] = []
        for line in example.splitlines():
            stripped = line.lstrip()
            if stripped.startswith(">>> "):
                lines.append(stripped[4:])
            elif stripped.startswith(">>>"):
                lines.append(stripped[3:].lstrip())
            elif stripped.startswith("... "):
                lines.append(stripped[4:])
            elif stripped.startswith("..."):
                lines.append(stripped[3:].lstrip())
            else:
                lines.append(line)
        return "\n".join(lines)

    def call_name(node: ast.expr) -> str | None:
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        parts.append(node.id)
        return ".".join(reversed(parts))

    for descriptor in REGISTRY.descriptors:
        callable_obj = _resolve_callable(descriptor)
        if callable_obj is None:
            continue
        example = _extract_example(inspect.getdoc(callable_obj) or "")
        if example is None:
            continue
        tree = ast.parse(clean(example))
        callable_name = getattr(callable_obj, "__name__", None)
        owned_calls = tuple(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (name := call_name(node.func)) is not None
            and name.rsplit(".", 1)[-1] == callable_name
        )
        assert owned_calls, descriptor.help_target
        signature = inspect.signature(callable_obj)
        receiver_bound = next(iter(signature.parameters), None) in {"self", "cls"}
        for call in owned_calls:
            assert not any(isinstance(argument, ast.Starred) for argument in call.args)
            assert all(keyword.arg is not None for keyword in call.keywords)
            positional = [object() for _ in call.args]
            if receiver_bound:
                positional.insert(0, object())
            keywords = {keyword.arg: object() for keyword in call.keywords if keyword.arg}
            signature.bind(*positional, **keywords)


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


def test_registered_examples_never_discard_contract_output() -> None:
    discarded = re.compile(r"\s*[A-Za-z_][\w.]*\.contract\(\)\s*$")
    for descriptor in REGISTRY.descriptors:
        for example in descriptor.additional_examples:
            for line in example.code.splitlines():
                assert not discarded.fullmatch(line), (
                    f"{descriptor.id}/{example.label}: discarded contract line {line!r}; "
                    "use .contract().show() or assign the result"
                )


def test_registry_rejects_examples_that_discard_contract_output() -> None:
    descriptor = REGISTRY.by_id("compare")
    bad = HelpExample(
        label="Discard the contract",
        code="delta = session.compare(current, baseline)\ndelta.contract()",
        requires=("current", "baseline"),
    )
    with pytest.raises(ValueError, match=r"discards \.contract\(\)"):
        _validate_additional_examples(dataclasses.replace(descriptor, additional_examples=(bad,)))
