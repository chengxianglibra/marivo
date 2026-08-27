"""Pure persisted-history Lifecycle reducer contracts."""

from __future__ import annotations

import textwrap
from datetime import datetime

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo._compat import UTC
from marivo.analysis.errors import (
    InvalidDistributionInstantsError,
    SubjectSetMismatchError,
)
from marivo.analysis.frames.event import EventInputCoverage
from marivo.analysis.frames.lifecycle import (
    LIFECYCLE_HISTORY_COLUMNS,
    LIFECYCLE_VIOLATIONS_COLUMNS,
    LifecycleFrame,
    LifecycleHistoryFrameMeta,
    LifecycleStateBinding,
    LifecycleTraceManifest,
    LifecycleTriggerBinding,
    PersistedModelStateHandle,
)
from marivo.analysis.intents._lifecycle_distribution import (
    reduce_lifecycle_distribution,
)
from marivo.analysis.intents._lifecycle_dwell import reduce_lifecycle_dwell
from marivo.analysis.intents._lifecycle_transitions import (
    reduce_lifecycle_transitions,
)
from marivo.analysis.intents._lifecycle_violations import (
    reduce_lifecycle_violations,
)
from marivo.analysis.lifecycle import from_inception
from marivo.analysis.lineage import Lineage
from marivo.analysis.session._runtime import persist_frame
from marivo.refs import RefPayloadV1

_DOMAIN = """\
import marivo.semantic as ms
ms.domain(name="commerce", owner="Analytics", default=True)
"""

_OBJECTS = """\
import marivo.datasource as md
import marivo.semantic as ms

warehouse = ms.ref.datasource("warehouse")
orders = ms.entity(
    name="orders", datasource=warehouse, source=md.table("orders"),
    primary_key=["order_id"],
    ai_context=ms.ai_context(business_definition="One row per order."),
)
event_log = ms.entity(
    name="event_log", datasource=warehouse, source=md.table("event_log"),
    primary_key=["event_id"],
    ai_context=ms.ai_context(business_definition="One row per event."),
)
order_id = ms.dimension_column(name="order_id", entity=orders, column="order_id")
region = ms.dimension_column(name="region", entity=orders, column="region")
event_id = ms.dimension_column(name="event_id", entity=event_log, column="event_id")
event_order_id = ms.dimension_column(
    name="order_id", entity=event_log, column="order_id"
)
event_type = ms.dimension_column(name="event_type", entity=event_log, column="event_type")
event_time = ms.time_dimension_column(
    name="event_time", entity=event_log, column="event_time",
    granularity="second", parse=ms.timestamp(timezone="UTC"), is_default=True,
)
event_to_order = ms.relationship(
    name="event_to_order", from_entity=event_log, to_entity=orders,
    keys=[ms.join_on(event_order_id, order_id)],
)

@ms.event(
    name="order_created", identity=(event_id,), occurred_at=event_time,
    participants=(
        ms.participant(name="order", path=(event_to_order,), cardinality="one"),
    ),
    ai_context=ms.ai_context(business_definition="An order was created."),
)
def order_created(rows):
    return ms.bind(event_type, rows) == "created"

@ms.event(
    name="payment_captured", identity=(event_id,), occurred_at=event_time,
    participants=(
        ms.participant(name="order", path=(event_to_order,), cardinality="one"),
    ),
    ai_context=ms.ai_context(business_definition="Payment was captured."),
)
def payment_captured(rows):
    return ms.bind(event_type, rows) == "paid"

created = ms.lifecycle_state(name="created", initial=True)
paid = ms.lifecycle_state(name="paid", terminal=True)
order_lifecycle = ms.state_model(
    name="order_lifecycle",
    subject=orders,
    states=(created, paid),
    transitions=(
        ms.inception(on=order_created),
        ms.transition(from_state=created, on=payment_captured, to_state=paid),
    ),
    ai_context=ms.ai_context(business_definition="Commercial order lifecycle."),
)
"""


def _payload(value):
    return RefPayloadV1.from_ref(value)


def _session(semantic_project_factory, tmp_path, monkeypatch) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    semantic_project_factory(
        {
            "commerce/_domain.py": textwrap.dedent(_DOMAIN),
            "commerce/objects.py": textwrap.dedent(_OBJECTS),
        }
    )
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql("CREATE TABLE orders (order_id VARCHAR, region VARCHAR)")
    backend.raw_sql("INSERT INTO orders VALUES ('o1', 'east'), ('o2', NULL)")
    return session_attach.get_or_create(
        name="lifecycle-reducers",
        backends={"warehouse": lambda: backend},
    )


def _committed_history(session: mv.Session) -> LifecycleFrame:
    model_entry = session.catalog.require(ms.ref.state_model("commerce.order_lifecycle"))
    model_details = model_entry.details()
    model_ref = _payload(model_entry.ref)
    subject_ref = _payload(ms.ref.entity("commerce.orders"))
    subject_identity = ("commerce.orders.order_id",)
    created_event = session.catalog.require(ms.ref.event("commerce.order_created")).details()
    paid_event = session.catalog.require(ms.ref.event("commerce.payment_captured")).details()
    states = (
        LifecycleStateBinding(
            state=PersistedModelStateHandle(model=model_ref, name="created"),
            initial=True,
        ),
        LifecycleStateBinding(
            state=PersistedModelStateHandle(model=model_ref, name="paid"),
            terminal=True,
        ),
    )
    triggers = (
        LifecycleTriggerBinding(
            kind="inception",
            event_ref=_payload(created_event.ref),
            participant_role="order",
            to_state="created",
        ),
        LifecycleTriggerBinding(
            kind="transition",
            event_ref=_payload(paid_event.ref),
            participant_role="order",
            from_state="created",
            to_state="paid",
        ),
    )
    history = pd.DataFrame(
        [
            (
                ("o1",),
                "created",
                pd.Timestamp("2026-07-01T00:00:00Z"),
                pd.Timestamp("2026-07-05T00:00:00Z"),
                created_event.ref.path,
                ("created-1",),
                paid_event.ref.path,
                ("paid-1",),
                "completed",
            ),
            (
                ("o1",),
                "paid",
                pd.Timestamp("2026-07-05T00:00:00Z"),
                pd.Timestamp("2026-07-10T00:00:00Z"),
                paid_event.ref.path,
                ("paid-1",),
                None,
                None,
                "coverage_censored",
            ),
            (
                ("o2",),
                "created",
                pd.Timestamp("2026-07-02T00:00:00Z"),
                pd.Timestamp("2026-07-10T00:00:00Z"),
                created_event.ref.path,
                ("created-2",),
                None,
                None,
                "coverage_censored",
            ),
        ],
        columns=LIFECYCLE_HISTORY_COLUMNS,
    )
    trace = pd.DataFrame(
        [
            (
                ("o2",),
                paid_event.ref.path,
                ("paid-illegal",),
                pd.Timestamp("2026-07-04T00:00:00Z"),
                "created",
                "illegal_transition",
            )
        ],
        columns=LIFECYCLE_VIOLATIONS_COLUMNS,
    )
    frame = LifecycleFrame(
        _df=history,
        meta=LifecycleHistoryFrameMeta(
            ref="frame_lifecycle_history",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_lifecycle_replay",
            created_at=datetime(2026, 7, 25, tzinfo=UTC),
            row_count=len(history),
            byte_size=0,
            lineage=Lineage(),
            catalog_definition_fingerprint=session.catalog.definition_fingerprint,
            state_model_ref=model_ref,
            state_model_fingerprint=model_details.definition_fingerprint,
            subject_entity_ref=subject_ref,
            subject_identity=subject_identity,
            states=states,
            seed=from_inception(),
            window=mv.time_scope(
                start="2026-07-01T00:00:00Z",
                end="2026-07-10T00:00:00Z",
            ),
            triggers=triggers,
            input_coverage=(
                EventInputCoverage(
                    event_ref=_payload(created_event.ref),
                    basis="unknown",
                ),
                EventInputCoverage(
                    event_ref=_payload(paid_event.ref),
                    basis="unknown",
                ),
            ),
            coverage_basis="unknown",
            event_fingerprints={
                created_event.ref.path: created_event.definition_fingerprint,
                paid_event.ref.path: paid_event.definition_fingerprint,
            },
            event_identity_components={
                created_event.ref.path: tuple(_payload(item) for item in created_event.identity),
                paid_event.ref.path: tuple(_payload(item) for item in paid_event.identity),
            },
            population_count=3,
            seeded_subject_count=2,
            coverage_censored_subject_count=1,
            interval_count=len(history),
            violation_count=len(trace),
            pre_inception_ignored_counts={trigger.key: 0 for trigger in triggers},
            violation_trace=LifecycleTraceManifest(row_count=len(trace)),
        ),
        _auxiliary_frames={"violations.parquet": trace},
    )
    frame.meta = persist_frame(session, frame)
    return frame


def test_lifecycle_reducers_use_committed_history_without_datasource_queries(
    semantic_project_factory,
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    history = _committed_history(session)
    from marivo.analysis.intents import _event_subject_axes

    executed_queries = 0
    original_execute = _event_subject_axes.execute

    def counted_execute(*args, **kwargs):
        nonlocal executed_queries
        executed_queries += 1
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(_event_subject_axes, "execute", counted_execute)

    distribution = session.lifecycle.distribution(
        history,
        at=(
            "2026-07-06T00:00:00+00:00",
            "2026-07-03T00:00:00Z",
        ),
    )
    transitions = session.lifecycle.transitions(history)
    dwell = session.lifecycle.dwell(history)
    violations = session.lifecycle.violations(history)
    assert executed_queries == 0

    assert distribution.meta.at == (
        "2026-07-03T00:00:00Z",
        "2026-07-06T00:00:00Z",
    )
    assert distribution.meta.known_subject_counts == {
        "2026-07-03T00:00:00Z": 1,
        "2026-07-06T00:00:00Z": 0,
    }
    assert distribution.meta.coverage_censored_subject_counts == {
        "2026-07-03T00:00:00Z": 2,
        "2026-07-06T00:00:00Z": 3,
    }
    assert distribution.to_pandas()["model_state"].tolist() == [
        "created",
        "paid",
        "created",
        "paid",
    ]
    assert distribution.to_pandas()["subject_count"].tolist() == [1, 0, 0, 0]

    transition_rows = transitions.to_pandas()
    assert transition_rows["from_model_state"].tolist() == ["created"]
    assert transition_rows["to_model_state"].tolist() == ["paid"]
    assert transition_rows["transition_count"].tolist() == [1]
    assert transition_rows["share_of_modeled_transitions"].tolist() == [1.0]

    dwell_rows = dwell.to_pandas().set_index("model_state")
    assert dwell_rows.loc["created", "interval_count"] == 2
    assert dwell_rows.loc["created", "completed_count"] == 1
    assert dwell_rows.loc["created", "coverage_censored_count"] == 1
    assert dwell_rows.loc["created", "mean_duration"] == pd.Timedelta(days=4)
    assert pd.isna(dwell_rows.loc["paid", "mean_duration"])

    violation_rows = violations.to_pandas()
    assert violation_rows["violation_kind"].tolist() == ["illegal_transition"]
    assert violation_rows.iloc[0]["subject_identity"] == ("o2",)
    assert violations.meta.source_trace_content_hash.startswith("sha256:")

    for artifact in (distribution, transitions, dwell, violations):
        assert artifact.meta.source_history_ref == history.ref
        assert artifact.meta.source_history_fingerprint == history.meta.content_hash
        assert session.job(artifact.meta.produced_by_job)["queries"] == []

    grouped = session.lifecycle.distribution(
        history,
        at=("2026-07-03T00:00:00Z",),
        axes=[ms.ref.dimension("commerce.orders.region")],
    )
    assert executed_queries == 1
    grouped_rows = grouped.to_pandas()
    assert tuple(grouped_rows.columns) == (
        "region",
        "as_of",
        "model_state",
        "subject_count",
        "share",
    )
    assert grouped.meta.axes[0].anchor == "as_of"
    assert grouped.meta.axes[0].versioning_resolution == "ordinary"
    assert grouped_rows.groupby("model_state", dropna=False)["subject_count"].sum().to_dict() == {
        "created": 1,
        "paid": 0,
    }
    assert len(session.job(grouped.meta.produced_by_job)["queries"]) == 1


def test_lifecycle_reducer_revalidation_tracks_history_dependency(
    semantic_project_factory,
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    history = _committed_history(session)
    reducer = session.lifecycle.transitions(history)

    admissible = session.revalidate(reducer)
    assert admissible.status == "admissible"
    assert admissible.dependency_status == "admissible"

    original_get_frame = session.get_frame

    def changed_source(ref):
        loaded = original_get_frame(ref)
        if ref == history.ref:
            loaded.meta = loaded.meta.model_copy(update={"content_hash": "sha256:changed"})
        return loaded

    monkeypatch.setattr(session, "get_frame", changed_source)
    stale = session.revalidate(reducer)
    assert stale.status == "stale"
    assert stale.dependency_status == "stale"
    assert any(issue.severity == "blocking" for issue in stale.issues)

    session._store.delete_artifact(session.id, history.ref)
    indeterminate = session.revalidate(reducer)
    assert indeterminate.status == "indeterminate"
    assert indeterminate.dependency_status == "indeterminate"


def test_distribution_rejects_invalid_instants_before_any_axis_query(
    semantic_project_factory,
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    history = _committed_history(session)

    with pytest.raises(InvalidDistributionInstantsError) as exc_info:
        session.lifecycle.distribution(
            history,
            at=("2026-07-10T00:00:00Z",),
        )

    error = exc_info.value
    assert error.location == "session.lifecycle.distribution.at"
    assert error.expected
    assert error.received
    assert error.repair.help_target.canonical_id == "lifecycle.distribution"


def test_reducers_reject_non_history_but_ignore_historical_catalog_fingerprint(
    semantic_project_factory,
    tmp_path,
    monkeypatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    history = _committed_history(session)
    distribution = session.lifecycle.distribution(
        history,
        at=("2026-07-03T00:00:00Z",),
    )

    with pytest.raises(SubjectSetMismatchError, match="canonical replay history"):
        session.lifecycle.dwell(distribution)

    history.meta = history.meta.model_copy(
        update={"catalog_definition_fingerprint": "sha256:stale"}
    )
    transitions = session.lifecycle.transitions(history)
    assert transitions.meta.semantic_kind == "transitions"


def test_pure_reducers_keep_dense_zero_population_and_empty_trace() -> None:
    instant = "2026-07-03T00:00:00Z"
    empty_history = pd.DataFrame(columns=LIFECYCLE_HISTORY_COLUMNS)
    distribution = reduce_lifecycle_distribution(
        empty_history,
        instants=((instant, pd.Timestamp(instant)),),
        state_order=("created", "paid"),
        population_count=0,
    )
    assert distribution.rows["subject_count"].tolist() == [0, 0]
    assert distribution.rows["share"].isna().all()
    assert distribution.known_subject_counts == {instant: 0}
    assert distribution.coverage_censored_subject_counts == {instant: 0}

    trigger = LifecycleTriggerBinding(
        kind="transition",
        event_ref=_payload(ms.ref.event("commerce.payment_captured")),
        participant_role="order",
        from_state="created",
        to_state="paid",
    )
    duplicate_trigger = trigger.model_copy()
    transitions = reduce_lifecycle_transitions(
        empty_history,
        triggers=(trigger, duplicate_trigger),
    )
    assert transitions.modeled_pairs == (("created", "paid"),)
    assert transitions.rows["transition_count"].tolist() == [0]
    assert transitions.rows["share_of_modeled_transitions"].isna().all()

    dwell = reduce_lifecycle_dwell(
        empty_history,
        state_order=("created", "paid"),
    )
    assert dwell.rows["interval_count"].tolist() == [0, 0]
    assert dwell.rows["mean_duration"].isna().all()

    empty_trace = pd.DataFrame(columns=LIFECYCLE_VIOLATIONS_COLUMNS)
    violations = reduce_lifecycle_violations(empty_trace)
    assert violations.violation_count == 0
    assert tuple(violations.rows.columns) == LIFECYCLE_VIOLATIONS_COLUMNS
