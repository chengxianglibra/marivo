"""Public ``session.lifecycle.replay`` integration over a real DuckDB project."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.errors import (
    AnalysisError,
    InsufficientStateHistoryError,
    InvalidCompletenessDeclarationError,
    InvalidLifecycleSeedError,
    SemanticKindMismatchError,
    SubjectSetMismatchError,
    WindowInvalidError,
)
from marivo.analysis.frames.lifecycle import (
    LIFECYCLE_HISTORY_COLUMNS,
    LIFECYCLE_VIOLATIONS_COLUMNS,
    LifecycleFrame,
)
from marivo.analysis.windows.spec import TimeScope
from tests.shared_fixtures import (
    LIFECYCLE_BASE_EVENTS,
    LIFECYCLE_BASE_ORDERS,
    LIFECYCLE_MODEL_REF,
    lifecycle_project_files,
    rendered_help,
    seed_lifecycle_backend,
)

_MODEL_REF = LIFECYCLE_MODEL_REF
_WINDOW = TimeScope(start="2026-07-01T00:00:00Z", end="2026-08-01T00:00:00Z")
_BASE_EVENTS = LIFECYCLE_BASE_EVENTS
_BASE_ORDERS = LIFECYCLE_BASE_ORDERS
_LIFECYCLE_CAPABILITY_IDS = {
    "lifecycle.replay",
    "lifecycle.distribution",
    "lifecycle.transitions",
    "lifecycle.dwell",
    "lifecycle.violations",
}


def _replay_session(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    events: tuple[tuple[str, ...], ...] = _BASE_EVENTS,
    orders: tuple[tuple[str, ...], ...] = _BASE_ORDERS,
    watermark_events: frozenset[str] = frozenset(),
) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    semantic_project_factory(lifecycle_project_files())
    backend = seed_lifecycle_backend(
        events=events,
        orders=orders,
        watermark_events=watermark_events,
    )
    return session_attach.get_or_create(
        name=name,
        report_timezone="UTC",
        backends={"warehouse": lambda: backend},
    )


def _declaration(*paths: str) -> mv.CompletenessDeclaration:
    return mv.declared_complete_through(
        inputs=tuple(ms.ref.event(path) for path in paths),
        through="2026-08-01T00:00:00Z",
        rationale="The replay fixture is reconciled through the window end.",
    )


def _query_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every datasource execution performed by occurrence materialization."""
    occurrences = importlib.import_module("marivo.analysis.intents._event_occurrences")
    original = occurrences.execute
    calls: list[str] = []

    def spy(expression: Any, **kwargs: Any) -> Any:
        calls.append(str(kwargs.get("datasource_name")))
        return original(expression, **kwargs)

    monkeypatch.setattr(occurrences, "execute", spy)
    return calls


def test_replay_materializes_exact_clipped_history_and_fixed_trace(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-replay-rows",
    )
    try:
        history = session.lifecycle.replay(
            ms.ref.state_model(_MODEL_REF),
            window=_WINDOW,
            seed=mv.from_inception(),
        )

        assert isinstance(history, LifecycleFrame)
        assert history.semantic_shape == "history"
        rows = history.to_pandas()
        assert tuple(rows.columns) == LIFECYCLE_HISTORY_COLUMNS
        assert [
            (
                item.subject_identity,
                item.model_state,
                item.valid_from.isoformat(),
                item.valid_to.isoformat(),
                item.interval_status,
            )
            for item in rows.itertuples(index=False)
        ] == [
            (
                ("o1",),
                "created",
                "2026-07-01T00:00:00+00:00",
                "2026-07-05T00:00:00+00:00",
                "completed",
            ),
            (
                ("o1",),
                "paid",
                "2026-07-05T00:00:00+00:00",
                "2026-07-20T00:00:00+00:00",
                "completed",
            ),
            (
                ("o1",),
                "closed",
                "2026-07-20T00:00:00+00:00",
                "2026-08-01T00:00:00+00:00",
                "coverage_censored",
            ),
            (
                ("o2",),
                "created",
                "2026-07-10T00:00:00+00:00",
                "2026-07-25T00:00:00+00:00",
                "completed",
            ),
            (
                ("o2",),
                "closed",
                "2026-07-25T00:00:00+00:00",
                "2026-08-01T00:00:00+00:00",
                "coverage_censored",
            ),
        ]
        # The pre-window inception is reconstructed, not treated as a seed gap.
        assert rows.iloc[0]["entered_by_event_identity"] == ("e1",)
        assert rows.iloc[0]["exited_by_event_identity"] == ("e2",)

        meta = history.meta
        assert meta.coverage_basis == "unknown"
        assert meta.population_count == 2
        assert meta.seeded_subject_count == 2
        assert meta.coverage_censored_subject_count == 0
        assert meta.interval_count == 5
        assert meta.violation_count == 2
        assert set(meta.pre_inception_ignored_counts.values()) == {0}

        trace = history._auxiliary_frames["violations.parquet"]
        assert tuple(trace.columns) == LIFECYCLE_VIOLATIONS_COLUMNS
        assert [
            (
                item.subject_identity,
                item.trigger_event_identity,
                item.model_state_at_event,
                item.violation_kind,
            )
            for item in trace.itertuples(index=False)
        ] == [
            (("o1",), ("e3",), "paid", "illegal_transition"),
            (("o1",), ("e5",), "closed", "transition_from_terminal"),
        ]
        # The private trace never surfaces through the public history rows.
        assert "violation_kind" not in rows.columns
    finally:
        session.close()
        session_attach._reset_process_state()


def test_replay_queries_each_distinct_event_exactly_once(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-replay-queries",
    )
    try:
        calls = _query_spy(monkeypatch)
        history = session.lifecycle.replay(
            ms.ref.state_model(_MODEL_REF),
            window=_WINDOW,
            seed=mv.from_inception(),
        )

        # order_closed drives two modeled transitions but is queried once.
        assert len(calls) == 3
        assert set(history.meta.event_fingerprints) == {
            "commerce.order_created",
            "commerce.payment_captured",
            "commerce.order_closed",
        }
        assert len(history.meta.triggers) == 4
    finally:
        session.close()
        session_attach._reset_process_state()


def test_replay_accepts_a_current_catalog_entry_and_an_exact_ref(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-replay-parity",
    )
    try:
        from_ref = session.lifecycle.replay(
            ms.ref.state_model(_MODEL_REF),
            window=_WINDOW,
            seed=mv.from_inception(),
        )
        from_entry = session.lifecycle.replay(
            session.catalog.state_models.get(_MODEL_REF),
            window=_WINDOW,
            seed=mv.from_inception(),
        )

        assert from_entry.meta.artifact_id == from_ref.meta.artifact_id
        assert from_entry.to_pandas().equals(from_ref.to_pandas())
    finally:
        session.close()
        session_attach._reset_process_state()


@pytest.mark.parametrize(
    ("case", "expected_error", "location"),
    [
        ("model", SemanticKindMismatchError, "session.lifecycle.replay.model"),
        ("window_type", WindowInvalidError, "session.lifecycle.replay.window"),
        ("window_naive", WindowInvalidError, "session.lifecycle.replay.window.start"),
        ("window_empty", WindowInvalidError, "session.lifecycle.replay.window"),
        ("seed", InvalidLifecycleSeedError, "session.lifecycle.replay.seed"),
        (
            "completeness_type",
            InvalidCompletenessDeclarationError,
            "session.lifecycle.replay.completeness",
        ),
        (
            "completeness_short",
            InvalidCompletenessDeclarationError,
            "session.lifecycle.replay.completeness.through",
        ),
        (
            "completeness_foreign",
            InvalidCompletenessDeclarationError,
            "session.lifecycle.replay.completeness.inputs",
        ),
        (
            "completeness_overlap",
            InvalidCompletenessDeclarationError,
            "session.lifecycle.replay.completeness.inputs",
        ),
        ("cohort", SubjectSetMismatchError, "session.lifecycle.replay.cohort"),
    ],
)
def test_replay_rejects_invalid_inputs_before_any_datasource_query(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_error: type[AnalysisError],
    location: str,
) -> None:
    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name=f"lifecycle-replay-static-{case}",
    )
    try:
        kwargs: dict[str, Any] = {
            "window": _WINDOW,
            "seed": mv.from_inception(),
        }
        model: Any = ms.ref.state_model(_MODEL_REF)
        if case == "model":
            model = ms.ref.entity("commerce.orders")
        elif case == "window_type":
            kwargs["window"] = {"start": "2026-07-01T00:00:00Z", "end": "2026-08-01T00:00:00Z"}
        elif case == "window_naive":
            kwargs["window"] = TimeScope(start="2026-07-01T00:00:00", end="2026-08-01T00:00:00Z")
        elif case == "window_empty":
            kwargs["window"] = TimeScope(start="2026-08-01T00:00:00Z", end="2026-08-01T00:00:00Z")
        elif case == "seed":
            kwargs["seed"] = "from_inception"
        elif case == "completeness_type":
            kwargs["completeness"] = [_declaration("commerce.order_created")]
        elif case == "completeness_short":
            kwargs["completeness"] = (
                mv.declared_complete_through(
                    inputs=(ms.ref.event("commerce.order_created"),),
                    through="2026-07-15T00:00:00Z",
                    rationale="This declaration stops before the replay window end.",
                ),
            )
        elif case == "completeness_foreign":
            kwargs["completeness"] = (_declaration("commerce.order_refunded"),)
        elif case == "completeness_overlap":
            kwargs["completeness"] = (
                _declaration("commerce.order_created"),
                _declaration("commerce.order_created"),
            )
        elif case == "cohort":
            kwargs["cohort"] = "everyone"

        calls = _query_spy(monkeypatch)
        with pytest.raises(expected_error) as exc_info:
            session.lifecycle.replay(model, **kwargs)

        assert calls == []
        error = exc_info.value
        assert error.location == location
        assert error.expected
        assert error.received
        assert error.repair is not None
        assert error.repair.action
        assert error.repair.help_target.surface == "analysis"
        assert error.repair.help_target.canonical_id == "lifecycle.replay"
        assert session._store.list_artifacts(session.id) == []
        assert session._store.list_jobs(session.id) == []
    finally:
        session.close()
        session_attach._reset_process_state()


def test_replay_rejects_an_unknown_state_model_with_live_candidates(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-replay-candidates",
    )
    try:
        with pytest.raises(SemanticKindMismatchError) as exc_info:
            session.lifecycle.replay(
                ms.ref.state_model("commerce.missing_lifecycle"),
                window=_WINDOW,
                seed=mv.from_inception(),
            )

        repair = exc_info.value.repair
        assert repair is not None
        assert repair.candidates == (f"state_model:{_MODEL_REF}",)
    finally:
        session.close()
        session_attach._reset_process_state()


@pytest.mark.parametrize(
    ("watermarks", "declared", "expected_basis", "expected_status"),
    [
        (frozenset(), (), "unknown", "coverage_censored"),
        (
            frozenset(
                {
                    "commerce.order_created",
                    "commerce.payment_captured",
                    "commerce.order_closed",
                }
            ),
            (),
            "observed_watermark",
            "right_censored",
        ),
        (
            frozenset(),
            (
                "commerce.order_created",
                "commerce.payment_captured",
                "commerce.order_closed",
            ),
            "declared_complete",
            "right_censored",
        ),
        (
            frozenset({"commerce.order_created"}),
            ("commerce.payment_captured", "commerce.order_closed"),
            "mixed",
            "right_censored",
        ),
    ],
)
def test_replay_resolves_authoritative_coverage_and_final_interval_status(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    watermarks: frozenset[str],
    declared: tuple[str, ...],
    expected_basis: str,
    expected_status: str,
) -> None:
    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name=f"lifecycle-replay-coverage-{expected_basis}",
        watermark_events=watermarks,
    )
    try:
        history = session.lifecycle.replay(
            ms.ref.state_model(_MODEL_REF),
            window=_WINDOW,
            seed=mv.from_inception(),
            completeness=(_declaration(*declared),) if declared else (),
        )

        assert history.meta.coverage_basis == expected_basis
        rows = history.to_pandas()
        final = rows.groupby("subject_identity", sort=False)["interval_status"].last()
        assert set(final) == {expected_status}
        assert {item.basis for item in history.meta.input_coverage} == (
            {"observed_watermark", "declared_complete"}
            if expected_basis == "mixed"
            else {expected_basis}
        )
    finally:
        session.close()
        session_attach._reset_process_state()


def test_replay_fails_when_complete_history_proves_a_missing_inception(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-replay-seed-gap",
        events=(*_BASE_EVENTS, ("e8", "o3", "paid", "2026-07-02 00:00:00")),
        orders=(*_BASE_ORDERS, ("o3", "north", "2026-07-02")),
        watermark_events=frozenset(
            {
                "commerce.order_created",
                "commerce.payment_captured",
                "commerce.order_closed",
            }
        ),
    )
    try:
        with pytest.raises(InsufficientStateHistoryError) as exc_info:
            session.lifecycle.replay(
                ms.ref.state_model(_MODEL_REF),
                window=_WINDOW,
                seed=mv.from_inception(),
            )

        assert exc_info.value.location == "session.lifecycle.replay.seed"
        assert session._store.list_artifacts(session.id) == []
    finally:
        session.close()
        session_attach._reset_process_state()


def test_replay_censors_unseeded_subjects_when_coverage_is_unknown(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-replay-censored",
        events=(*_BASE_EVENTS, ("e8", "o3", "paid", "2026-07-02 00:00:00")),
        orders=(*_BASE_ORDERS, ("o3", "north", "2026-07-02")),
    )
    try:
        history = session.lifecycle.replay(
            ms.ref.state_model(_MODEL_REF),
            window=_WINDOW,
            seed=mv.from_inception(),
        )

        assert history.meta.population_count == 3
        assert history.meta.seeded_subject_count == 2
        assert history.meta.coverage_censored_subject_count == 1
        assert history.meta.pre_inception_ignored_counts["commerce.payment_captured#order"] == 1
        assert ("o3",) not in set(history.to_pandas()["subject_identity"])
    finally:
        session.close()
        session_attach._reset_process_state()


def test_replay_history_cold_recovers_with_identical_rows_and_trace(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-replay-cold",
    )
    try:
        warm = session.lifecycle.replay(
            ms.ref.state_model(_MODEL_REF),
            window=_WINDOW,
            seed=mv.from_inception(),
        )
        artifact_id = warm.meta.artifact_id or warm.meta.ref
        warm_rows = warm.to_pandas()
        warm_trace = warm._auxiliary_frames["violations.parquet"].copy()
    finally:
        session.close()
        session_attach._reset_process_state()

    monkeypatch.chdir(tmp_path)
    backend = seed_lifecycle_backend()
    reopened = session_attach.get_or_create(
        name="lifecycle-replay-cold",
        report_timezone="UTC",
        backends={"warehouse": lambda: backend},
    )
    try:
        cold = reopened.get_frame(artifact_id)

        assert isinstance(cold, LifecycleFrame)
        assert cold.meta.semantic_kind == "history"
        assert cold.to_pandas().equals(warm_rows)
        assert cold._auxiliary_frames["violations.parquet"].equals(warm_trace)
        assert cold.meta.violation_trace.content_hash is not None
        assert cold.meta.violation_trace.content_hash.startswith("sha256:")
        assert cold.to_pandas()["subject_identity"].map(type).eq(tuple).all()
    finally:
        reopened.close()
        session_attach._reset_process_state()


@pytest.mark.parametrize(
    "failure_target",
    ["register_frame_artifact", "persist_job_record"],
)
def test_replay_rolls_back_artifact_evidence_and_job_on_late_failure(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_target: str,
) -> None:
    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name=f"lifecycle-replay-rollback-{failure_target}",
    )
    lifecycle_module = importlib.import_module("marivo.analysis.intents.lifecycle")

    def fail_persistence(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced late persistence failure")

    monkeypatch.setattr(lifecycle_module, failure_target, fail_persistence)
    try:
        with pytest.raises(RuntimeError, match="forced late persistence failure"):
            session.lifecycle.replay(
                ms.ref.state_model(_MODEL_REF),
                window=_WINDOW,
                seed=mv.from_inception(),
            )

        assert session._store.list_artifacts(session.id) == []
        assert session._store.list_jobs(session.id) == []
        assert list(session._layout.frames_dir.glob("*")) == []
        assert list(session._layout.jobs_dir.glob("*.json")) == []
        evidence_store = session._evidence_store()
        assert evidence_store is not None
        evidence_count = (
            evidence_store.read()
            .execute(
                "SELECT COUNT(*) FROM artifacts WHERE session_id = ?",
                (session.id,),
            )
            .fetchone()[0]
        )
        assert evidence_count == 0
    finally:
        session.close()
        session_attach._reset_process_state()


def test_replay_preserves_a_preexisting_artifact_when_a_later_job_write_fails(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-replay-preserve",
    )
    lifecycle_module = importlib.import_module("marivo.analysis.intents.lifecycle")
    try:
        first = session.lifecycle.replay(
            ms.ref.state_model(_MODEL_REF),
            window=_WINDOW,
            seed=mv.from_inception(),
        )
        artifact_id = first.meta.artifact_id or first.meta.ref

        def fail_persistence(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("forced late persistence failure")

        monkeypatch.setattr(lifecycle_module, "persist_job_record", fail_persistence)
        with pytest.raises(RuntimeError, match="forced late persistence failure"):
            session.lifecycle.replay(
                ms.ref.state_model(_MODEL_REF),
                window=_WINDOW,
                seed=mv.from_inception(),
            )

        assert session._store.get_artifact(session.id, artifact_id) is not None
        recovered = session.get_frame(artifact_id)
        assert isinstance(recovered, LifecycleFrame)
        assert recovered.to_pandas().equals(first.to_pandas())
    finally:
        session.close()
        session_attach._reset_process_state()


def test_replay_scopes_the_population_to_a_ready_subject_set(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-replay-cohort",
    )
    try:
        with pytest.raises(SubjectSetMismatchError) as exc_info:
            session.lifecycle.replay(
                ms.ref.state_model(_MODEL_REF),
                window=_WINDOW,
                seed=mv.from_inception(),
                cohort=pd.DataFrame({"subject_identity": [("o1",)]}),
            )

        assert exc_info.value.location == "session.lifecycle.replay.cohort"
    finally:
        session.close()
        session_attach._reset_process_state()


def test_lifecycle_capabilities_are_discoverable_with_help_parity(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _LIFECYCLE_CAPABILITY_IDS.issubset(REGISTRY.capability_ids)
    # Phase 4/5 operators must remain unavailable.
    assert {
        "lifecycle.match",
        "lifecycle.transition",
        "lifecycle.survival",
        "lifecycle.retention",
    }.isdisjoint(REGISTRY.capability_ids)
    assert not hasattr(mv, "in_state_at")
    assert not hasattr(mv, "from_state")

    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-replay-discovery",
    )
    try:
        assert ".lifecycle" in session.render()
        for canonical, bound in (
            ("lifecycle.replay", session.lifecycle.replay),
            ("lifecycle.distribution", session.lifecycle.distribution),
            ("lifecycle.transitions", session.lifecycle.transitions),
            ("lifecycle.dwell", session.lifecycle.dwell),
            ("lifecycle.violations", session.lifecycle.violations),
        ):
            canonical_help = rendered_help(canonical, owner="analysis")
            assert rendered_help(f"session.{canonical}", owner="analysis") == canonical_help
            assert rendered_help(f"Session.{canonical}", owner="analysis") == canonical_help
            assert rendered_help(bound, owner="analysis") == canonical_help
            assert "LifecycleFrame" in canonical_help

        history = session.lifecycle.replay(
            ms.ref.state_model(_MODEL_REF),
            window=_WINDOW,
            seed=mv.from_inception(),
        )
        history_contract = history.contract().render()
        assert "session.lifecycle.distribution(...)" in history_contract
        assert "session.select_subjects(...)" in history_contract
        assert "session.lifecycle.match" not in history_contract

        reduced = session.lifecycle.transitions(history)
        assert "session.assess_quality(...)" in reduced.contract().render()
    finally:
        session.close()
        session_attach._reset_process_state()


def test_lifecycle_business_choices_are_published_as_guidance_and_disclosure(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seed, violation contract, and completeness carry business guidance."""
    seed_help = rendered_help("from_inception", owner="analysis")
    seed_text = " ".join(seed_help.split())
    assert "Guidance:" in seed_help
    assert "never replaced by assuming the initial state" in seed_text

    replay_help = rendered_help("lifecycle.replay", owner="analysis")
    replay_text = " ".join(replay_help.split())
    assert "Guidance:" in replay_help
    # The fixed v1 contract is stated as a contract, not offered as a policy.
    assert "fixed v1 replay contract rather than a policy slot" in replay_text
    assert "leaves state unchanged" in replay_text
    assert "mv.declared_complete_through(...)" in replay_text
    assert "on_violation" not in replay_text

    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-replay-disclosure",
    )
    try:
        history = session.lifecycle.replay(
            ms.ref.state_model(_MODEL_REF),
            window=_WINDOW,
            seed=mv.from_inception(),
        )
        rendered = history.render()
        assert "seed: from_inception" in rendered
        assert "violation_contract: record_and_continue/v1" in rendered
        assert "coverage=unknown" in rendered
        assert "output_columns:" in rendered
        assert f'session.catalog.state_models.get("{_MODEL_REF}")' in rendered
        contract = history.contract()
        assert contract.output_columns == tuple(history.columns)
        assert contract.semantic_inputs[0].role == "state_model"
        assert contract.semantic_inputs[0].semantic_path == _MODEL_REF
    finally:
        session.close()
        session_attach._reset_process_state()


def test_reducers_and_evidence_consume_committed_history_without_event_rereads(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _replay_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-replay-reducers",
    )
    try:
        history = session.lifecycle.replay(
            ms.ref.state_model(_MODEL_REF),
            window=_WINDOW,
            seed=mv.from_inception(),
        )

        calls = _query_spy(monkeypatch)
        session._connection_runtime.take_captured_queries()
        reducers = (
            session.lifecycle.distribution(history, at=["2026-07-15T00:00:00Z"]),
            session.lifecycle.transitions(history),
            session.lifecycle.dwell(history),
            session.lifecycle.violations(history),
        )
        reports = tuple(session.assess_quality(frame) for frame in (history, *reducers))
        history_checks = {
            row.check_kind: row.status for row in reports[0].to_pandas().itertuples(index=False)
        }

        # Reducers and quality run purely from committed rows.
        assert calls == []
        assert session._connection_runtime.take_captured_queries() == []
        assert all(frame.meta.source_history_ref for frame in reducers)
        assert history_checks["lifecycle_history_state"] == "ok"
        assert history_checks["lifecycle_trace"] == "ok"

        # No raw subject or Event identity ever reaches metadata or evidence.
        raw_identities = ("o1", "o2", "e1", "e2", "e3", "e4", "e5", "e6", "e7")
        for frame in (history, *reducers):
            payload = json.dumps(frame.meta.model_dump(mode="json"), sort_keys=True)
            digest = session.evidence.digest(frame.meta.artifact_id or frame.ref)
            evidence_payload = json.dumps(digest.model_dump(mode="json"), sort_keys=True)
            for value in raw_identities:
                assert f'"{value}"' not in payload
                assert f'"{value}"' not in evidence_payload
        for report in reports:
            report_payload = json.dumps(report.meta.model_dump(mode="json"), sort_keys=True)
            for value in raw_identities:
                assert f'"{value}"' not in report_payload
    finally:
        session.close()
        session_attach._reset_process_state()
