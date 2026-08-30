"""Event Journey quality, capability, and discovery contracts."""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import pytest
from pydantic import ValidationError

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._compat import UTC
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis._capabilities.validation import (
    classify_input_family,
    validate_capability_inputs,
)
from marivo.analysis.errors import (
    AnalysisError,
    InvalidCompletenessDeclarationError,
    InvalidEventMatchingPolicyError,
    InvalidEventPatternError,
    InvalidSubjectAxisError,
    PatternStepMismatchError,
    SubjectSetMismatchError,
)
from marivo.analysis.event import _event_repair, first_per_subject, sequence, step
from marivo.analysis.frames._quality import evaluate_frame_quality
from marivo.analysis.frames._quality_checks import run_event_journey_checks
from marivo.analysis.frames.event import EventFrame, EventFrameMeta, EventInputCoverage
from marivo.analysis.lineage import Lineage
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import RefPayloadV1
from tests.shared_fixtures import rendered_help


def _event_frame(session: mv.Session) -> EventFrame:
    cart = ms.ref.event("commerce.cart_created")
    payment = ms.ref.event("commerce.payment_succeeded")
    user = ms.ref.entity("commerce.users")
    cart_step = step(
        participant=ms.participant_role(event=cart, name="user"),
        key="cart",
    )
    payment_step = step(
        participant=ms.participant_role(event=payment, name="buyer"),
        key="payment",
    )
    pattern = sequence(cart_step, payment_step)
    rows = pd.DataFrame(
        [
            {
                "journey_id": "journey_1",
                "completion_status": "coverage_censored",
                "subject_identity": ("user_1",),
                "step_key": "cart",
                "event_identity": ("cart_1",),
                "occurred_at": pd.Timestamp("2026-07-01T10:00:00Z"),
                "elapsed_from_start": pd.Timedelta(0),
                "elapsed_from_previous": pd.Timedelta(0),
            },
            {
                "journey_id": "journey_1",
                "completion_status": "coverage_censored",
                "subject_identity": ("user_1",),
                "step_key": "payment",
                "event_identity": None,
                "occurred_at": pd.NaT,
                "elapsed_from_start": pd.NaT,
                "elapsed_from_previous": pd.NaT,
            },
        ]
    )
    meta = EventFrameMeta(
        ref="frame_event_quality",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_event_match",
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        row_count=len(rows),
        byte_size=0,
        lineage=Lineage(),
        catalog_definition_fingerprint="sha256:catalog",
        subject_entity_ref=RefPayloadV1.from_ref(user),
        subject_identity=("commerce.users.user_id",),
        pattern=pattern,
        matching=first_per_subject(),
        cohort_window=mv.time_scope(
            start="2026-07-01T00:00:00Z",
            end="2026-07-02T00:00:00Z",
        ),
        completion_through="2026-07-03T00:00:00Z",
        input_coverage=(
            EventInputCoverage(
                event_ref=RefPayloadV1.from_ref(cart),
                basis="unknown",
            ),
            EventInputCoverage(
                event_ref=RefPayloadV1.from_ref(payment),
                basis="unknown",
            ),
        ),
        coverage_basis="unknown",
        event_fingerprints={
            cart.path: "sha256:cart",
            payment.path: "sha256:payment",
        },
        event_identity_components={
            cart.path: (RefPayloadV1.from_ref(ms.ref.dimension("commerce.events.event_id")),),
            payment.path: (RefPayloadV1.from_ref(ms.ref.dimension("commerce.events.event_id")),),
        },
        role_endpoints={
            "cart": RefPayloadV1.from_ref(user),
            "payment": RefPayloadV1.from_ref(user),
        },
        unused_event_count=1,
        unused_event_counts_by_step={"cart": 0, "payment": 1},
    )
    return EventFrame(_df=rows, meta=meta)


def _declared_event_frame(session: mv.Session) -> EventFrame:
    """Build the journey frame with fully disclosed declared completeness."""
    frame = _event_frame(session)
    cart, payment = (step.event for step in frame.meta.pattern.steps)
    declaration = mv.declared_complete_through(
        inputs=(cart, payment),
        through=frame.meta.completion_through,
        rationale="Fixture events are reconciled through the completion bound.",
    )
    declared_meta = frame.meta.model_copy(
        update={
            "completeness": (declaration,),
            "coverage_basis": "declared_complete",
            "input_coverage": tuple(
                EventInputCoverage(
                    event_ref=RefPayloadV1.from_ref(event_ref),
                    basis="declared_complete",
                    declaration_fingerprint=declaration.fingerprint,
                    declaration_rationale=declaration.rationale,
                )
                for event_ref in (cart, payment)
            ),
        }
    )
    return EventFrame(_df=frame._dataframe_copy(), meta=declared_meta)


def test_event_value_constructor_repairs_require_truthful_user_choice() -> None:
    with pytest.raises(InvalidEventPatternError) as pattern_error:
        mv.sequence()
    with pytest.raises(InvalidEventMatchingPolicyError) as matching_error:
        mv.every_start(completion_assignment="invalid")  # type: ignore[arg-type]
    with pytest.raises(InvalidCompletenessDeclarationError) as completeness_error:
        mv.declared_complete_through(inputs=(), through="", rationale="")

    for error in (
        pattern_error.value,
        matching_error.value,
        completeness_error.value,
    ):
        assert error.expected
        assert error.received
        assert error.location
        assert error.repair is not None
        assert error.repair.kind == "user_choice"
        assert error.repair.help_target.canonical_id == "events.match"
        assert error.repair.snippet is None
    assert matching_error.value.repair is not None
    assert matching_error.value.repair.candidates == (
        'completion_assignment="exclusive"',
        'completion_assignment="shared"',
    )


def test_event_repair_factory_enforces_retry_snippet_invariant() -> None:
    with pytest.raises(ValueError, match="requires a runnable snippet"):
        _event_repair(kind="retry", action="Retry the call.")
    with pytest.raises(ValueError, match="only Event retry repairs"):
        _event_repair(
            kind="inspect",
            action="Inspect the current Event.",
            snippet="session.events.match(...)",
        )


def test_event_journey_quality_evaluation_is_typed_and_discloses_coverage(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(
        name="event_quality",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )
    frame = _event_frame(session)

    evaluation = evaluate_frame_quality(frame, artifact_id="prospective")
    assert evaluation is not None

    assert evaluation.overall_status == "warning"
    assert evaluation.dataframe["metric_id"].isna().all()
    assert set(evaluation.dataframe["check_kind"]) == {
        "row_count",
        "event_row_contract",
        "event_identity",
        "event_participant",
        "event_ordering",
        "event_coverage",
        "declared_completeness_used",
        "event_censoring",
    }
    assert {issue.kind for issue in evaluation.issues} == {
        "event_coverage_unknown",
        "event_censoring_present",
    }
    persisted = json.dumps(frame.meta.model_dump(mode="json"), sort_keys=True)
    assert "user_1" not in persisted
    assert "cart_1" not in persisted


def test_empty_event_result_is_warning_when_contract_remains_valid(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(
        name="empty_event_quality",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )
    source = _declared_event_frame(session)
    empty = EventFrame(
        _df=source.to_pandas().iloc[0:0],
        meta=source.meta.model_copy(update={"row_count": 0}),
    )

    evaluation = evaluate_frame_quality(empty, artifact_id="prospective")
    assert evaluation is not None
    row_count = evaluation.dataframe.set_index("check_kind").loc["row_count"]

    assert row_count["severity"] == "warning"
    assert evaluation.overall_status == "warning"
    assert evaluation.blocking_issue_count == 0
    assert {issue.kind for issue in evaluation.issues} == {"sample_size_low"}


def test_declared_completeness_is_not_a_quality_warning(tmp_path, monkeypatch) -> None:
    """A disclosed declaration is a governed path, not a defect.

    Pins issue #83: ``declared_completeness_used`` used to warn whenever any
    input relied on ``mv.declared_complete_through(...)``, so a disclosed,
    rationale-bearing declaration could never reach ``ok``. The frame meta
    already guarantees any retained declaration covers the completion bound
    and carries a matching rationale, so the quality check must be neutral.
    """
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(
        name="event_quality_declared",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )
    frame = _declared_event_frame(session)

    rows = run_event_journey_checks(frame)
    declared = next(row for row in rows if row["check_kind"] == "declared_completeness_used")
    assert declared["severity"] == "ok"
    assert declared["status"] == "ok"
    details = json.loads(declared["details_json"])
    assert details["declared_input_count"] == 2

    report = evaluate_frame_quality(frame, artifact_id="prospective")
    assert report is not None
    assert "declared_completeness_used" not in {issue.kind for issue in report.issues}


def test_event_capability_family_gate_and_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="event_capability", use_datasources=False)
    frame = _event_frame(session)

    assert classify_input_family(frame) == "EventFrame"
    assert frame.row_count == frame.shape[0]
    assert classify_input_family(frame.meta.pattern) == "EventPattern"
    assert classify_input_family(frame.meta.matching) == "EventMatchingPolicy"
    validate_capability_inputs(
        "events.match",
        pattern=frame.meta.pattern,
        cohort_window=frame.meta.cohort_window,
        matching=frame.meta.matching,
        completeness=frame.meta.completeness,
    )

    with pytest.raises(AnalysisError) as captured:
        validate_capability_inputs(
            "events.match",
            pattern=frame.meta.pattern.steps[0].event,
        )
    assert captured.value.location == "events.match.pattern"

    affordances = {item.capability_id for item in frame.contract().affordances}
    assert affordances == {
        "events.funnel",
        "events.time_to_event",
        "select_subjects",
    }


@pytest.mark.parametrize(
    ("capability_id", "parameter", "value", "error_type", "location", "repair_kind"),
    [
        (
            "events.funnel",
            "axes",
            ["channel"],
            InvalidSubjectAxisError,
            "session.events.funnel.axes",
            "user_choice",
        ),
        (
            "select_subjects",
            "selection",
            "payment",
            PatternStepMismatchError,
            "session.select_subjects.selection",
            "user_choice",
        ),
        (
            "events.match",
            "cohort",
            "dropouts",
            SubjectSetMismatchError,
            "session.events.match.cohort",
            "inspect",
        ),
        (
            "observe",
            "cohort",
            "dropouts",
            SubjectSetMismatchError,
            "session.observe.cohort",
            "inspect",
        ),
        (
            "events.funnel",
            "journeys",
            "journeys",
            SubjectSetMismatchError,
            "session.events.funnel.journeys",
            "inspect",
        ),
        (
            "events.time_to_event",
            "journeys",
            "journeys",
            SubjectSetMismatchError,
            "session.events.time_to_event.journeys",
            "inspect",
        ),
        (
            "select_subjects",
            "artifact",
            "journeys",
            SubjectSetMismatchError,
            "session.select_subjects.artifact",
            "inspect",
        ),
    ],
)
def test_phase2_family_gate_preserves_structured_event_repairs(
    capability_id: str,
    parameter: str,
    value: object,
    error_type: type[AnalysisError],
    location: str,
    repair_kind: str,
) -> None:
    with pytest.raises(error_type) as captured:
        validate_capability_inputs(capability_id, **{parameter: value})

    error = captured.value
    assert error.location == location
    assert error.repair is not None
    assert error.repair.kind == repair_kind
    assert error.repair.help_target.canonical_id == capability_id
    assert not (error.repair.kind == "retry" and error.repair.snippet is None)


def test_event_quality_blocks_dense_row_and_coverage_contract_violations(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="event_quality_invalid", use_datasources=False)
    frame = _event_frame(session)
    broken_rows = frame._dataframe_copy()
    broken_rows.loc[1, "occurred_at"] = pd.Timestamp("2026-07-01T11:00:00Z")
    broken_rows.loc[1, "event_identity"] = ("payment_1",)
    broken_rows.loc[1, "elapsed_from_start"] = pd.Timedelta(minutes=5)
    broken_rows.loc[1, "elapsed_from_previous"] = pd.Timedelta(hours=1)
    broken = EventFrame(
        _df=broken_rows,
        meta=frame.meta.model_copy(update={"input_coverage": frame.meta.input_coverage[:1]}),
    )

    checks = {row["check_id"]: row for row in run_event_journey_checks(broken)}

    assert checks["event_row_contract"]["severity"] == "blocking"
    assert checks["event_coverage:commerce.payment_succeeded"]["severity"] == "blocking"


def test_event_coverage_rejects_unsupported_authority_and_aggregate_claims(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="event_coverage_invariants", use_datasources=False)
    frame = _event_frame(session)
    cart = ms.ref.event("commerce.cart_created")

    with pytest.raises(ValidationError, match="requires a receipt"):
        EventInputCoverage(
            event_ref=RefPayloadV1.from_ref(cart),
            basis="observed_watermark",
        )

    receipt = mv.EventWatermarkReceipt(
        complete_through=frame.meta.completion_through,
        authority="warehouse_reconciliation",
        observed_at="2026-07-03T01:00:00Z",
    )
    observed = tuple(
        EventInputCoverage(
            event_ref=item.event_ref,
            basis="observed_watermark",
            receipt=receipt,
            observed_complete_through=receipt.complete_through,
        )
        for item in frame.meta.input_coverage
    )
    with pytest.raises(ValidationError, match="coverage_basis must be 'observed_watermark'"):
        EventFrameMeta.model_validate(
            {
                **frame.meta.model_dump(mode="python"),
                "input_coverage": observed,
                "coverage_basis": "unknown",
            }
        )
    stale_receipt = receipt.model_copy(update={"complete_through": "2026-07-02T23:59:59Z"})
    stale_observed = tuple(
        EventInputCoverage(
            event_ref=item.event_ref,
            basis="observed_watermark",
            receipt=stale_receipt,
            observed_complete_through=stale_receipt.complete_through,
        )
        for item in frame.meta.input_coverage
    )
    with pytest.raises(ValidationError, match="must cover completion_through"):
        EventFrameMeta.model_validate(
            {
                **frame.meta.model_dump(mode="python"),
                "input_coverage": stale_observed,
                "coverage_basis": "observed_watermark",
            }
        )


def test_event_quality_blocks_forged_observed_coverage_without_receipts(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="event_coverage_quality", use_datasources=False)
    frame = _event_frame(session)
    forged = tuple(
        EventInputCoverage.model_construct(
            event_ref=item.event_ref,
            basis="observed_watermark",
            receipt=None,
            declaration_fingerprint=None,
            declaration_rationale=None,
            observed_complete_through=None,
        )
        for item in frame.meta.input_coverage
    )
    frame.meta = frame.meta.model_copy(
        update={
            "input_coverage": forged,
            "coverage_basis": "observed_watermark",
        }
    )

    checks = {row["check_id"]: row for row in run_event_journey_checks(frame)}

    assert checks["event_coverage:commerce.cart_created"]["severity"] == "blocking"
    assert checks["event_coverage:commerce.payment_succeeded"]["severity"] == "blocking"
    assert (
        "observed_watermark_receipt_missing"
        in checks["event_coverage:commerce.cart_created"]["details_json"]
    )


def test_event_watermark_types_have_registered_public_help() -> None:
    request_help = rendered_help(mv.EventWatermarkRequest, owner="analysis")
    receipt_help = rendered_help("EventWatermarkReceipt", owner="analysis")

    assert request_help.startswith("EventWatermarkRequest")
    assert "event_fingerprint" in request_help
    assert "required_through" in request_help
    assert receipt_help.startswith("EventWatermarkReceipt")
    assert "complete_through" in receipt_help
    assert "authority" in receipt_help


def test_event_contract_filters_first_per_subject_only_continuations(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="event_contract_matrix", use_datasources=False)
    first = _event_frame(session)
    repeated = EventFrame(
        _df=first.to_pandas(),
        meta=first.meta.model_copy(
            update={
                "matching": mv.every_start(completion_assignment="exclusive"),
            }
        ),
    )

    first_ids = {item.capability_id for item in first.contract().affordances}
    repeated_ids = {item.capability_id for item in repeated.contract().affordances}
    assert {
        "events.funnel",
        "events.time_to_event",
        "select_subjects",
    }.issubset(first_ids)
    assert "events.funnel" not in repeated_ids
    assert "select_subjects" not in repeated_ids
    assert {"events.time_to_event"}.issubset(repeated_ids)

    time_to_event = next(
        item
        for item in first.contract().affordances
        if item.capability_id == "events.time_to_event"
    )
    requirements = {item.parameter: item for item in time_to_event.input_requirements}
    assert {"axes", "journeys", "start_step", "end_step"} <= set(requirements)
    for parameter in ("start_step", "end_step"):
        requirement = requirements[parameter]
        assert requirement.bindable_from_current_artifact is False
        assert requirement.derivable_from_current_artifact is True
        assert "journeys.meta.pattern.steps" in (requirement.acquisition or "")
        assert requirement.help_targets == (
            LiveHelpTarget(surface="analysis", canonical_id="PatternStep"),
            LiveHelpTarget(surface="analysis", canonical_id="step"),
        )
    assert requirements["journeys"].bindable_from_current_artifact is True
    assert requirements["journeys"].derivable_from_current_artifact is False

    rendered_contract = first.contract().render()
    assert "start_step=constructed (current_artifact=false" in rendered_contract
    assert "end_step=constructed (current_artifact=false" in rendered_contract
    assert "derived_from_current_artifact=true" in rendered_contract

    session._connection_runtime.begin_query_capture()
    with pytest.raises(InvalidEventMatchingPolicyError) as captured:
        session.events.funnel(repeated)
    assert captured.value.location == "session.events.funnel.journeys.matching"
    assert captured.value.repair is not None
    assert captured.value.repair.kind == "user_choice"
    assert captured.value.repair.candidates == ("first_per_subject",)
    assert session._connection_runtime.take_captured_queries() == []


def test_phase_two_event_capabilities_are_discoverable(tmp_path, monkeypatch) -> None:
    expected = {
        "events.match",
        "events.funnel",
        "events.time_to_event",
        "select_subjects",
        "step",
        "sequence",
        "first_per_subject",
        "every_start",
        "declared_complete_through",
        "dropped_before",
    }
    assert expected.issubset(REGISTRY.capability_ids)
    assert {
        "lifecycle.match",
        "lifecycle.transition",
    }.isdisjoint(REGISTRY.capability_ids)

    rendered = rendered_help("events.match", owner="analysis")
    assert "session.events.match" in rendered
    assert "EventFrame" in rendered
    assert 'marivo.help("analysis.every_start")' in rendered
    matching_help = rendered_help("every_start", owner="analysis")
    assert "completion_assignment: Literal['exclusive', 'shared']" in matching_help
    assert 'mv.every_start(completion_assignment="exclusive")' in matching_help

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="event_discovery", use_datasources=False)
    assert ".events" in session.render()
    assert ".select_subjects(...)" in session.render()
    assert callable(session.events.match)
    for canonical, bound in (
        ("events.match", session.events.match),
        ("events.funnel", session.events.funnel),
        ("events.time_to_event", session.events.time_to_event),
    ):
        canonical_help = rendered_help(canonical, owner="analysis")
        assert rendered_help(f"session.{canonical}", owner="analysis") == canonical_help
        assert rendered_help(f"Session.{canonical}", owner="analysis") == canonical_help
        assert rendered_help(bound, owner="analysis") == canonical_help
    select_help = rendered_help("select_subjects", owner="analysis")
    assert rendered_help("Session.select_subjects", owner="analysis") == select_help
    assert rendered_help(session.select_subjects, owner="analysis") == select_help
    assert "subject_identity" in rendered_help("SubjectSet", owner="analysis")


@pytest.mark.parametrize("assignment", ["exclusive", "shared"])
def test_event_frame_render_discloses_complete_matching_policy(
    tmp_path,
    monkeypatch,
    assignment: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(
        name=f"event_render_{assignment}",
        use_datasources=False,
    )
    frame = _event_frame(session)
    frame.meta = frame.meta.model_copy(
        update={
            "matching": mv.every_start(
                completion_assignment=assignment,  # type: ignore[arg-type]
            )
        }
    )

    rendered = frame.render()

    assert "matching=every_start" in rendered
    assert f"completion_assignment={assignment}" in rendered
    assert "output_columns:" not in rendered
    contract_rendered = frame.contract().render(max_output_bytes=None)
    assert "output_columns:" in contract_rendered
    assert 'session.catalog.events.get("commerce.cart_created")' in contract_rendered
    assert 'session.catalog.events.get("commerce.payment_succeeded")' in contract_rendered
    contract = frame.contract()
    assert contract.output_columns == tuple(frame.columns)
    assert {item.semantic_path for item in contract.semantic_inputs} == {
        "commerce.cart_created",
        "commerce.payment_succeeded",
    }
