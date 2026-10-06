"""Native SQLite Journey-origin Anchor binding retains exact assignments."""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.materialization.graph_exchange import ExchangeResult
from marivo.datasource.adapters import SourceSession
from tests.analysis.lifecycle.lifecycle_fixtures import START, build_lifecycle_public


@pytest.mark.runtime
def test_sqlite_calendar_metric_observation_excludes_dst_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    beginning = datetime(2026, 3, 7, 12, tzinfo=ZoneInfo("America/New_York"))
    offset = int((beginning - START).total_seconds())
    build_lifecycle_public(
        tmp_path,
        backend_name="sqlite",
        rows=[(0, "started", offset, 1), (0, "finished", offset + 23 * 3600, 2)],
    )
    model = tmp_path / "models/semantic/commerce/objects.py"
    model.write_text(
        model.read_text()
        + "\nscore = ms.measure_column(name='score',entity=facts,column='seq',additivity=ms.additive_all(),unit='1')\nscore_sum = ms.aggregate(name='score_sum',measure=score,agg='sum',time=instant,nulls=ms.nulls.ignore(),empty=ms.empty.zero())\n"
    )
    ms.load(workspace_dir=tmp_path)
    session = mv.session.get_or_create("calendar-observe", report_timezone="America/New_York")
    anchors = session.anchors(
        ms.participant_role(event=ms.ref.event("commerce.started"), name="subject"),
        population=session.members(ms.ref.entity("commerce.subjects")),
        during=mv.time_scope(start=beginning, end=beginning + timedelta(seconds=1)),
        business_order=ms.ref.business_order("commerce.order"),
    )
    for window, expected in (
        (mv.calendar_days(1, ZoneInfo("America/New_York")), 0),
        (mv.elapsed(mv.duration(hours=24)), 2),
    ):
        result = anchors.observe(
            ms.ref.metric("commerce.score_sum"),
            within=window,
            via=ms.ref.relationship("commerce.participant"),
        ).execute()
        assert result.to_pandas().value.tolist() == [expected]
    assert session._runtime.store.resources(session._runtime.session_ref) == ()


@pytest.mark.runtime
def test_sqlite_journey_anchor_preserves_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.methods import journey_matching

    monkeypatch.chdir(tmp_path)
    session, population, window, claims, _ = build_lifecycle_public(
        tmp_path,
        backend_name="sqlite",
        rows=[
            (0, "started", 0, 1),
            (0, "finished", 5, 2),
            (1, "started", 2, 1),
            (1, "finished", 7, 2),
        ],
    )
    pattern = mv.EventPattern(
        steps=(
            mv.step(
                participant=ms.participant_role(
                    event=ms.ref.event("commerce.started"), name="subject"
                ),
                key="start",
            ),
            mv.step(
                participant=ms.participant_role(
                    event=ms.ref.event("commerce.finished"), name="subject"
                ),
                key="finish",
            ),
        )
    )
    original = session.events.match(
        pattern,
        population=population,
        cohort_window=window,
        completion_through=START + timedelta(seconds=110),
        matching=mv.every_start(completion_assignment="exclusive"),
        business_order=ms.ref.business_order("commerce.order"),
        completeness=tuple(
            replace(
                claim,
                inputs=tuple(
                    event
                    for event in claim.inputs
                    if event.path in ("commerce.started", "commerce.finished")
                ),
            )
            for claim in claims
        ),
    )
    source = session.anchors(original, population=population, during=window).execute()
    assert source._dataset is not None
    anchor = next(p.table for p in source._dataset.verified().parts if p.role == "anchor")
    assert anchor.column("anchor__started_at").to_pylist() == [START, START + timedelta(seconds=2)]
    assert all(anchor.column("anchor__assignment").to_pylist())
    assignments = anchor.column("anchor__assignment").to_pylist()
    retained_journey, retained_population = original.execute(), population.execute()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Retained Journey Anchor binding accessed source or rematched")

    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(journey_matching, "match", forbidden)
    fixed = session.anchors(
        retained_journey, population=retained_population, during=window
    ).execute()
    assert fixed._dataset is not None
    fixed_anchor = next(p.table for p in fixed._dataset.verified().parts if p.role == "anchor")
    assert fixed_anchor.column("anchor__assignment").to_pylist() == assignments
    assert (
        fixed_anchor.column("anchor__started_at").to_pylist()
        == anchor.column("anchor__started_at").to_pylist()
    )
    assert session._runtime.store.resources(session._runtime.session_ref) == ()


@pytest.mark.runtime
@pytest.mark.parametrize("calendar", [False, True], ids=["elapsed", "calendar"])
def test_sqlite_journey_anchor_observation_uses_original_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calendar: bool
) -> None:
    from marivo.analysis.materialization import anchor_execution
    from marivo.analysis.methods import journey_matching

    monkeypatch.chdir(tmp_path)
    _, _, window, claims, _ = build_lifecycle_public(
        tmp_path,
        backend_name="sqlite",
        rows=[
            (0, "started", 0, 1),
            (0, "finished", 0, 2),
            (0, "started", 8, 3),
            (0, "finished", 9, 4),
            (0, "finished", 10, 5),
            (0, "finished", 18, 6),
            (1, "started", 0, 1),
            (1, "started", 8, 2),
        ],
    )
    model = tmp_path / "models/semantic/commerce/objects.py"
    model.write_text(
        model.read_text()
        + "\nscore = ms.measure_column(name='score',entity=facts,column='seq',additivity=ms.additive_all(),unit='1')\nscore_sum = ms.aggregate(name='score_sum',measure=score,agg='sum',time=instant,nulls=ms.nulls.ignore(),empty=ms.empty.zero())\n"
    )
    ms.load(workspace_dir=tmp_path)
    session = mv.session.get_or_create("journey-observe", report_timezone="UTC")
    population = session.members(ms.ref.entity("commerce.subjects"))
    original = session.events.match(
        mv.EventPattern(
            steps=tuple(
                mv.step(
                    participant=ms.participant_role(
                        event=ms.ref.event("commerce." + name), name="subject"
                    ),
                    key=key,
                )
                for name, key in (("started", "start"), ("finished", "finish"))
            )
        ),
        population=population,
        cohort_window=window,
        completion_through=START + timedelta(seconds=110),
        matching=mv.every_start(completion_assignment="exclusive"),
        business_order=ms.ref.business_order("commerce.order"),
        completeness=tuple(
            replace(
                claim,
                inputs=tuple(
                    event
                    for event in claim.inputs
                    if event.path in ("commerce.started", "commerce.finished")
                ),
            )
            for claim in claims
        ),
    )
    anchors = session.anchors(original, population=population, during=window)
    bind = anchor_execution.bind

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Journey observation read source after binding or repeated matching")

    def guarded_bind(
        node: MethodNode,
        selected: ExchangeResult,
        binding: str,
        captured: ExchangeResult | None = None,
    ) -> ExchangeResult:
        result = bind(node, selected, binding, captured)
        monkeypatch.setattr(SourceSession, "batches", forbidden)
        monkeypatch.setattr(journey_matching, "match", forbidden)
        return result

    monkeypatch.setattr(anchor_execution, "bind", guarded_bind)
    result = anchors.observe(
        ms.ref.metric("commerce.score_sum"),
        within=mv.calendar_days(1, ZoneInfo("UTC"))
        if calendar
        else mv.elapsed(mv.duration(seconds=10)),
        via=ms.ref.relationship("commerce.participant"),
    ).execute()
    expected = [20, 15, 2, 0] if calendar else [9, 9, 2, 0]
    assert result.to_pandas().value.tolist() == expected
    assert result._dataset is not None
    anchor = next(p.table for p in result._dataset.verified().parts if p.role == "anchor")
    assert all(anchor.column("anchor__assignment").to_pylist())
    assert [len(pool) for pool in anchor.column("anchor__uses_0").to_pylist()] == (
        [5, 3, 1, 0] if calendar else [3, 2, 1, 0]
    )
    fixed = result.where(result.value.is_defined()).execute()
    assert fixed.to_pandas().value.tolist() == expected
    assert session._runtime.store.resources(session._runtime.session_ref) == ()


@pytest.mark.runtime
@pytest.mark.parametrize("calendar", [False, True], ids=["elapsed", "calendar"])
def test_sqlite_journey_retention_preserves_original_opportunities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calendar: bool
) -> None:
    monkeypatch.chdir(tmp_path)
    session, population, window, claims, _ = build_lifecycle_public(
        tmp_path,
        backend_name="sqlite",
        rows=[
            (0, "started", 0, 1),
            (0, "finished", 0, 2),
            (0, "started", 8, 3),
            (0, "finished", 9, 4),
            (1, "started", 0, 1),
            (1, "started", 8, 2),
        ],
    )
    original = session.events.match(
        mv.EventPattern(
            steps=tuple(
                mv.step(
                    participant=ms.participant_role(
                        event=ms.ref.event("commerce." + name), name="subject"
                    ),
                    key=key,
                )
                for name, key in (("started", "start"), ("finished", "finish"))
            )
        ),
        population=population,
        cohort_window=window,
        completion_through=START + timedelta(seconds=110),
        matching=mv.every_start(completion_assignment="exclusive"),
        business_order=ms.ref.business_order("commerce.order"),
        completeness=tuple(
            replace(
                claim,
                inputs=tuple(
                    event
                    for event in claim.inputs
                    if event.path in ("commerce.started", "commerce.finished")
                ),
            )
            for claim in claims
        ),
    )
    anchors = session.anchors(original, population=population, during=window)
    returning = ms.participant_role(event=ms.ref.event("commerce.finished"), name="subject")
    result = anchors.retention(
        returning,
        within=mv.calendar_days(1, ZoneInfo("UTC"))
        if calendar
        else mv.elapsed(mv.duration(seconds=10)),
        completeness=tuple(
            replace(
                claim, inputs=(returning.event,), complete_through=START + timedelta(seconds=10)
            )
            for claim in claims
        ),
    ).execute()
    frame = result.to_pandas()
    assert frame.value.tolist() == (
        [True, True, None, None] if calendar else [True, True, False, None]
    )
    assert frame.cell_tag.tolist() == (
        ["defined", "defined", "unknown", "unknown"]
        if calendar
        else ["defined", "defined", "defined", "unknown"]
    )
    facts = dict(result.contract()._facts)
    assert facts["omega_count"] == "4"
    assert facts["deterministic_bounds"] == ("[0.5,1.0]" if calendar else "[0.5,0.75]")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Retained Journey-origin retention accessed source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    for rule, expected in (
        (mv.any_anchor(), [True, None]),
        (mv.every_anchor(), [True, None] if calendar else [True, False]),
    ):
        assert result.by_subject(rule=rule).execute().to_pandas().value.tolist() == expected
    selected = result.known_true().execute()
    assert dict(selected.contract()._facts)["omega_count"] == "4"
    assert selected.members(through=selected.subject_binding).execute().to_pandas()[
        "member"
    ].tolist() == [9007199254740993]
    assert session._runtime.store.resources(session._runtime.session_ref) == ()
