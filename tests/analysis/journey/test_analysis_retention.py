"""Independent retention population, truth and retained-state checks."""

from dataclasses import replace
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from tests.analysis.journey.retention_fixtures import build_retention
from tests.analysis.lifecycle.lifecycle_fixtures import START, build_lifecycle_public

# Direct tests own table/Parquet, scalar identity, calendar, DST, and quantifier
# behavior. Keep one recovery profile for the distinct composite/string key path.
PROFILE_CASES = (("c", "s", "parquet", "ns", "America/New_York"),)


def test_fixed_omega_truth_quantifiers_and_selection(tmp_path):
    session, anchors, returning, claims = build_retention(tmp_path)
    logical = anchors.retention(
        returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims
    )
    result = logical.execute()
    frame = result.to_pandas()
    assert frame.cell_tag.tolist().count("unknown") == 2
    assert frame.value.tolist().count(True) == 1
    assert frame.value.tolist().count(False) == 1
    facts = dict(result.contract()._facts)
    assert facts["omega_count"] == "4"
    assert facts["deterministic_bounds"] == "[0.25,0.75]"
    for rule, expected in ((mv.any_anchor(), [True, None]), (mv.every_anchor(), [None, False])):
        projected = result.by_subject(rule=rule).execute()
        assert projected.to_pandas().value.tolist() == expected
        assert dict(projected.contract()._facts)["omega_count"] == "2"
    selected = result.known_true().execute()
    assert len(selected.to_pandas()) == 1
    assert dict(selected.contract()._facts)["omega_count"] == "4"
    assert dict(selected.contract()._facts)["deterministic_bounds"] == "[0.25,0.75]"
    image = selected.members(through=selected.subject_binding).execute()
    assert len(image.to_pandas()) == 1
    for view in (result.status, result.known_false(), result.unknown()):
        with pytest.raises(AnalysisError):
            view.members(through=view.subject_binding)
    with pytest.raises(AnalysisError):
        anchors.execute().retention(returning, within=mv.elapsed(mv.duration(seconds=10)))
    assert isinstance(session.artifact(result.state.artifact_ref), mv.MaterializedRetentionResult)


@pytest.mark.parametrize("rule", [mv.any_anchor(), mv.every_anchor()])
def test_closed_rule_repr(rule):
    assert len(repr(rule).splitlines()) == 1


def test_empty_calendar_omega_is_undefined(tmp_path):
    empty = True
    _, anchors, returning, _ = build_retention(tmp_path, empty=empty)
    result = anchors.retention(
        returning, within=mv.calendar_days(1, ZoneInfo("America/New_York"))
    ).execute()
    facts = dict(result.contract()._facts)
    if empty:
        assert facts["omega_count"] == "0"
        assert "Undefined(empty_omega)" in facts["deterministic_bounds"]
        assert result.by_subject(rule=mv.any_anchor()).execute().to_pandas().empty
    else:
        assert facts["known_true_count"] == "1"


def test_raw_partition_and_shared_return(tmp_path):
    rows = [(0, "started", 0, 1), (0, "started", 1, 2), (0, "finished", 5, 3)]
    rows += [(1, "started", 30, 1), (2, "started", 90, 1)]
    _, anchors, returning, claims = build_retention(tmp_path, rows=rows)
    claims = tuple(replace(c, complete_through=START + timedelta(seconds=40)) for c in claims)
    fixed = anchors.retention(
        returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims
    ).execute()
    facts = dict(fixed.contract()._facts)
    assert [
        facts[k]
        for k in (
            "omega_count",
            "known_true_count",
            "known_false_count",
            "unknown_count",
            "deterministic_bounds",
        )
    ] == ["4", "2", "1", "1", "[0.5,0.75]"]
    # Independent raw-row oracle: time membership and coverage, not product helpers.
    expected = {}
    for index, (subject, kind, second, _) in enumerate(rows):
        if kind != "started":
            continue
        witness = any(
            s == subject and k == "finished" and second <= t < second + 10 for s, k, t, _ in rows
        )
        expected[9007199254740993 + index] = (
            True if witness else False if second + 10 <= 40 else None
        )
    actual = {r.coord_1: r.value for r in fixed.to_pandas().itertuples()}
    assert actual == expected


def test_same_event_does_not_count_as_a_later_return(tmp_path):
    _, anchors, _, _ = build_retention(tmp_path, rows=[(0, "started", 0, 1)])
    returning = ms.participant_role(event=ms.ref.event("commerce.started"), name="subject")
    result = anchors.retention(returning, within=mv.elapsed(mv.duration(seconds=10))).execute()
    assert result.to_pandas().value.tolist() == [None]


def test_same_time_and_exclusive_deadline_returns_share_one_source(tmp_path):
    rows = [
        (0, "started", 0, 1),
        (0, "finished", 0, 2),
        (1, "started", 0, 1),
        (1, "finished", 10, 2),
        (2, "started", 0, 1),
        (2, "finished", 9, 2),
    ]
    _, anchors, returning, _ = build_retention(tmp_path, rows=rows)
    result = anchors.retention(returning, within=mv.elapsed(mv.duration(seconds=10))).execute()
    frame = result.to_pandas().set_index("coord_1")["value"].to_dict()
    assert frame == {
        9007199254740993: True,
        9007199254740995: None,
        9007199254740997: True,
    }


def test_tie_without_order_and_wrong_coverage_fail_atomically(tmp_path):
    session, anchors, returning, claims = build_retention(
        tmp_path, rows=[(0, "started", 0, 1), (0, "finished", 0, 1)]
    )
    with pytest.raises(AnalysisError):
        anchors.retention(returning, within=mv.elapsed(mv.duration(seconds=10))).execute()
    assert session._runtime.last_run_ref is not None
    bad = tuple(replace(c, inputs=(ms.ref.event("commerce.started"),)) for c in claims)
    with pytest.raises(AnalysisError):
        anchors.retention(returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=bad)


def test_logical_subject_quantifier_and_journey_origin(tmp_path):
    session, anchors, returning, claims = build_retention(tmp_path)
    population = session.members(ms.ref.entity("commerce.subjects"))
    starting = ms.participant_role(event=ms.ref.event("commerce.started"), name="subject")
    journeys = session.events.match(
        mv.sequence(
            mv.step(participant=starting, key="start"), mv.step(participant=returning, key="finish")
        ),
        population=population,
        cohort_window=mv.time_scope(
            start=START.isoformat(), end=(START + timedelta(seconds=100)).isoformat()
        ),
        completion_through=START + timedelta(seconds=110),
        business_order=ms.ref.business_order("commerce.order"),
        matching=mv.every_start(completion_assignment="shared"),
    )
    result = (
        session.anchors(
            journeys,
            population=population,
            during=mv.time_scope(
                start=START.isoformat(), end=(START + timedelta(seconds=100)).isoformat()
            ),
        )
        .retention(returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims)
        .execute()
    )
    assert dict(result.contract()._facts)["omega_count"] == "4"
    assert result.to_pandas().value.tolist() == [True, None, False, None]


@pytest.mark.slow
@pytest.mark.parametrize("subject,occurrence,form,unit,zone", PROFILE_CASES)
def test_profiles_source_fixed_cold(tmp_path, subject, occurrence, form, unit, zone):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    config = {
        "subject": subject,
        "occurrence": occurrence,
        "form": form,
        "unit": unit,
        "zone": zone,
    }
    (tmp_path / "config.json").write_text(json.dumps(config))
    receipts = []
    for phase in ("produce", "continue", "cold"):
        run = subprocess.run(
            [sys.executable, "-m", "tests.analysis.journey.retention_worker", phase, str(tmp_path)],
            env=dict(os.environ, MARIVO_PROJECT_ROOT=str(tmp_path)),
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        receipts.append(json.loads(run.stdout.splitlines()[-1]))
    evidence = os.getenv("MARIVO_R78_EVIDENCE_DIR")
    if evidence:
        folder = Path(evidence)
        folder.mkdir(parents=True, exist_ok=True)
        (
            folder / (f"{subject}-{occurrence}-{form}-{unit}-{zone.replace('/', '_')}.json")
        ).write_text(json.dumps({"config": config, "phases": receipts}, sort_keys=True))


@pytest.mark.parametrize("calendar_start,hours", [("2026-03-07T17:00:00+00:00", 23)])
def test_dst_retention_deadline_is_exclusive(tmp_path, calendar_start, hours):
    from datetime import datetime

    first = datetime.fromisoformat(calendar_start)
    offset = int((first - START).total_seconds())
    session, members, _, claims, _ = build_lifecycle_public(
        tmp_path, rows=[(0, "started", offset, 1), (0, "finished", offset + hours * 3600, 2)]
    )
    starting = ms.participant_role(event=ms.ref.event("commerce.started"), name="subject")
    returning = ms.participant_role(event=ms.ref.event("commerce.finished"), name="subject")
    anchors = session.anchors(
        starting,
        population=members,
        during=mv.time_scope(
            start=first.isoformat(), end=(first + timedelta(seconds=1)).isoformat()
        ),
        business_order=ms.ref.business_order("commerce.order"),
    )
    claims = tuple(
        replace(c, inputs=(returning.event,), complete_through=first + timedelta(hours=hours))
        for c in claims
    )
    result = anchors.retention(
        returning, within=mv.calendar_days(1, ZoneInfo("America/New_York")), completeness=claims
    ).execute()
    assert result.to_pandas().value.tolist() == [False]


def test_known_true_subject_image_then_source_preparation(tmp_path, monkeypatch):
    from marivo.analysis.materialization import retention_execution
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.journey.anchors_fixtures import build_anchors, event_anchors

    session, population, window, _, _ = build_anchors(tmp_path)
    execute = retention_execution.execute
    batches = SourceSession.batches
    local_started = False

    def local(*args, **kwargs):
        nonlocal local_started
        result = execute(*args, **kwargs)
        local_started = True
        return result

    def source(*args, **kwargs):
        assert not local_started, "source read followed local retention consumption"
        return batches(*args, **kwargs)

    monkeypatch.setattr(retention_execution, "execute", local)
    monkeypatch.setattr(SourceSession, "batches", source)
    returning = ms.participant_role(event=ms.ref.event("commerce.finished"), name="subject")
    for empty, filtered in ((False, True), (True, False)):
        selected_population = population
        if filtered:
            identity = population.read(ms.ref.dimension("commerce.subjects.sid"))
            selected_population = identity.where(identity.value.eq(9007199254740993)).members()
        logical = event_anchors(session, selected_population, window).retention(
            returning, within=mv.calendar_days(1, ZoneInfo("UTC"))
        )
        selected = logical.known_true()
        if empty:
            # Empty true selection retains a decidable-true certificate.
            selected = selected.where(selected.value.eq(False))
        members = selected.members(through=selected.subject_binding)
        operation = members.observe(
            ms.ref.metric("commerce.fact_count"),
            during=window,
            via=ms.ref.relationship("commerce.participant"),
        )
        local_started = False
        result = operation.execute()
        assert result.to_pandas().value.tolist() == ([] if empty else [6])


def test_native_elapsed_does_not_call_local_retention(tmp_path, monkeypatch):
    from marivo.analysis.materialization import retention_execution

    _, anchors, returning, claims = build_retention(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("native elapsed retention called the local kernel")

    monkeypatch.setattr(retention_execution, "execute", forbidden)
    result = anchors.retention(
        returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims
    ).execute()
    assert dict(result.contract()._facts)["deterministic_bounds"] == "[0.25,0.75]"


def test_retention_publication_failure_is_atomic(tmp_path):
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline

    session, anchors, returning, claims = build_retention(tmp_path)
    logical = anchors.retention(
        returning, within=mv.calendar_days(1, ZoneInfo("UTC")), completeness=claims
    )
    previous = logical.execute()

    for point in ("before_commit", "cancel", "deadline"):
        deadline_token = None

        def inject(actual, injection_point=point):
            nonlocal deadline_token
            if actual == (
                "before_commit" if injection_point in ("cancel", "deadline") else injection_point
            ):
                if injection_point == "cancel":
                    raise KeyboardInterrupt("injected retention cancellation")
                if injection_point == "deadline":
                    deadline_token = CURRENT.set(ExecuteDeadline(0, clock=lambda: 601))
                else:
                    raise RuntimeError("injected retention publication failure")

        session._runtime._hook = inject
        try:
            with pytest.raises((AnalysisError, KeyboardInterrupt)):
                logical.execute()
        finally:
            session._runtime._hook = None
            if deadline_token is not None:
                CURRENT.reset(deadline_token)
        assert (
            session.artifact(previous.state.artifact_ref).to_pandas().equals(previous.to_pandas())
        )
        with session._runtime.store._read() as connection:
            assert connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == 1
            assert (
                connection.execute("SELECT COUNT(*) FROM action_resource_journal").fetchone()[0]
                == 0
            )


def test_fixed_inputs_and_invalid_rule_reject_before_io_and_run(tmp_path, monkeypatch):
    from marivo.datasource.adapters import SourceSession

    session, anchors, returning, _ = build_retention(tmp_path)
    fixed = anchors.execute()
    retained = anchors.retention(returning, within=mv.elapsed(mv.duration(seconds=10))).execute()
    before = session.runs().items

    def forbidden(*args, **kwargs):
        raise AssertionError("construction opened source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    with pytest.raises(AnalysisError) as error:
        fixed.retention(returning, within=mv.elapsed(mv.duration(seconds=10)))
    assert error.value.constraint_id == "r7.retention_inputs"
    assert "before executing" in error.value.repair.action
    with pytest.raises(AnalysisError):
        retained.by_subject(rule=None)
    with pytest.raises(AnalysisError):
        anchors.retention(returning, within=mv.elapsed(mv.duration(nanoseconds=1)))
    assert session.runs().items == before


@pytest.mark.parametrize("kind,calendar", [("enum", False), ("precedence", True)])
def test_captured_same_instant_order_variants(tmp_path, kind, calendar):
    import re

    import ibis

    from marivo.analysis.session.core import Session

    session, _, _, _ = build_retention(tmp_path, rows=[(0, "started", 0, 1), (0, "finished", 0, 2)])
    model = tmp_path / "models/semantic/commerce/objects.py"
    text = model.read_text()
    if kind == "enum":
        backend = ibis.duckdb.connect(tmp_path / "source.duckdb")
        try:
            backend.raw_sql("ALTER TABLE facts ALTER seq TYPE VARCHAR")
        finally:
            backend.disconnect()
        text = text.replace('order="integer"', 'order=("1", "2")')
    else:
        text = re.sub(
            r"sequences=\(.*?\), ai_context=",
            "sequences=(), conflicts=(ms.precedes(ms.participant_role(event=started,name='subject'),ms.participant_role(event=finished,name='subject')),), ai_context=",
            text,
        )
    if kind == "precedence":
        text = text.replace("business_order=order,", "")
    model.write_text(text)
    ms.load(workspace_dir=tmp_path)
    session = Session._from_runtime(session._runtime)
    population = session.members(ms.ref.entity("commerce.subjects"))
    starting = ms.participant_role(event=ms.ref.event("commerce.started"), name="subject")
    returning = ms.participant_role(event=ms.ref.event("commerce.finished"), name="subject")
    anchors = session.anchors(
        starting,
        population=population,
        during=mv.time_scope(
            start=START.isoformat(), end=(START + timedelta(seconds=100)).isoformat()
        ),
        business_order=ms.ref.business_order("commerce.order"),
    )
    within = (
        mv.calendar_days(1, ZoneInfo("UTC")) if calendar else mv.elapsed(mv.duration(seconds=10))
    )
    assert anchors.retention(returning, within=within).execute().to_pandas().value.tolist() == [
        True
    ]


def test_typed_ledger_rejects_inconsistent_semantics(tmp_path):
    import pyarrow as pa

    from marivo.analysis.materialization.graph_exchange import ExchangePart
    from marivo.analysis.materialization.graph_protocol import encode
    from marivo.analysis.materialization.retention_execution import LEDGER, read, validate

    _, anchors, returning, claims = build_retention(tmp_path)
    result = anchors.retention(
        returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims
    ).execute()
    exchange = result._dataset.verified()
    scope = next(part for part in exchange.parts if part.role == "retention")
    ledger = read(scope)
    first = ledger.instances[0]
    variants = [
        replace(
            ledger,
            instances=(
                replace(first, deadline=first.deadline + timedelta(seconds=1)),
                *ledger.instances[1:],
            ),
        ),
        replace(ledger, instances=(replace(first, status="false"), *ledger.instances[1:])),
        replace(ledger, coverage=(replace(ledger.coverage[0], scope="foreign_capture"),)),
        replace(ledger, instances=ledger.instances[:-1]),
        replace(
            ledger,
            instances=(
                replace(first, uses=(replace(first.uses[0], instant=first.deadline),)),
                *ledger.instances[1:],
            ),
        ),
    ]
    for variant in variants:
        corrupted = ExchangePart(
            "retention", pa.table({"retention__retained": [encode(variant, LEDGER)]})
        )
        parts = tuple(corrupted if p.role == "retention" else p for p in exchange.parts)
        with pytest.raises(AnalysisError):
            validate(exchange.contract, exchange.primary, parts)


def test_source_retention_reexecutes_changed_facts(tmp_path):
    import ibis

    _, anchors, returning, claims = build_retention(tmp_path)
    logical = anchors.retention(
        returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims
    )
    original = logical.execute()
    backend = ibis.duckdb.connect(tmp_path / "source.duckdb")
    try:
        backend.raw_sql("UPDATE facts SET kind='pulse' WHERE kind='finished'")
    finally:
        backend.disconnect()
    current = logical.execute()
    assert original.state.artifact_ref != current.state.artifact_ref
    assert dict(original.contract()._facts)["known_true_count"] == "1"
    assert dict(current.contract()._facts)["known_true_count"] == "0"


@pytest.mark.parametrize("form,calendar", [("table", False), ("parquet", True)])
def test_return_capture_keeps_snapshot_and_validity_path(tmp_path, form, calendar):
    from tests.analysis.journey.anchors_fixtures import build_anchors, event_anchors

    session, population, window, _, _ = build_anchors(
        tmp_path, form=form, qualification_history=True
    )
    returning = ms.participant_role(event=ms.ref.event("commerce.finished"), name="subject")
    within = (
        mv.calendar_days(1, ZoneInfo("UTC")) if calendar else mv.elapsed(mv.duration(seconds=10))
    )
    result = (
        event_anchors(session, population, window).retention(returning, within=within).execute()
    )
    facts = dict(result.contract()._facts)
    assert facts["omega_count"] == "3"
    assert facts["known_true_count"] == ("2" if calendar else "1")
    assert (
        dict(result.by_subject(rule=mv.any_anchor()).execute().contract()._facts)["omega_count"]
        == "2"
    )


def test_retention_combines_exact_coverage_intervals(tmp_path, monkeypatch):
    from datetime import datetime

    from marivo.analysis.domains.completeness import BoundedCoverageStartV1, EventCoverageReceiptV1
    from marivo.datasource.adapters import SourceSession

    session, anchors, returning, claims = build_retention(tmp_path, rows=[(0, "started", 0, 1)])
    cases = (
        (0, 10, 0, 5, False),
        (5, 10, 0, 5, False),
        (6, 10, 0, 5, None),
    )
    active_case = cases[0]

    def point(value: int) -> datetime:
        return START + timedelta(seconds=value)

    original_enter = SourceSession.__enter__

    def enter(source):
        result = original_enter(source)

        def provider(backend, request):
            if request.event_ref != returning.event:
                return None
            observed_from, observed_through, _, _, _ = active_case
            assert source.domain_authority is not None
            return EventCoverageReceiptV1(
                event_ref=request.event_ref,
                event_fingerprint=request.event_fingerprint,
                source_entity_ref=request.source_entity_ref,
                source_origin_ref=request.source_origin_ref,
                occurred_at_ref=request.occurred_at_ref,
                coverage_start=BoundedCoverageStartV1(complete_from=point(observed_from)),
                complete_through=point(observed_through),
                authority="Exact-bound regression provider",
                observed_at=START,
                source_revision=source.domain_authority["digest"],
                source_binding_fingerprint=request.source_binding_fingerprint,
                execution_domain_id=request.execution_domain_id,
            )

        source._domain_coverage_provider = provider
        return result

    monkeypatch.setattr(SourceSession, "__enter__", enter)
    for case in cases:
        active_case = case
        _, _, declared_from, declared_through, expected = case
        declarations = tuple(
            mv.BoundedCompletenessDeclarationV1(
                inputs=(returning.event,),
                complete_from=point(declared_from),
                complete_through=point(declared_through),
                rationale="Exact-bound regression declaration",
            )
            for c in claims
        )
        result = anchors.retention(
            returning,
            within=mv.elapsed(mv.duration(seconds=10)),
            completeness=declarations,
        ).execute()
        frame = result.to_pandas()
        assert frame.value.tolist() == [expected]
        facts = dict(result.contract()._facts)
        assert facts["omega_count"] == "1"
        assert facts["deterministic_bounds"] == ("[0.0,0.0]" if expected is False else "[0.0,1.0]")
        assert session.artifact(result.state.artifact_ref).to_pandas().equals(frame)
        projected = result.by_subject(rule=mv.every_anchor()).execute()
        assert projected.to_pandas().value.tolist() == [expected]


def test_external_unknown_predicate_keeps_receiver_truth_source_and_fixed(tmp_path):
    _, anchors, returning, claims = build_retention(tmp_path)
    left = anchors.retention(
        returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims
    )
    right = anchors.retention(
        returning, within=mv.calendar_days(1, ZoneInfo("UTC")), completeness=claims
    )
    for materialized in (False, True):
        receiver, predicate = left, right
        if materialized:
            receiver, predicate = receiver.execute(), predicate.execute()
        result = receiver.status.where(mv.not_(predicate.status.value.is_defined())).execute()
        frame = result.to_pandas()
        assert frame.coord_1.tolist() == [
            9007199254740995,
            9007199254740996,
            9007199254740997,
        ]
        assert frame.value.tolist() == [None, False, None]
        facts = dict(result.contract()._facts)
        assert facts["selection"] == "predicate"
        assert facts["omega_count"] == "4"
        assert facts["deterministic_bounds"] == "[0.25,0.75]"
        with pytest.raises(AnalysisError):
            result.members(through=result.subject_binding)
