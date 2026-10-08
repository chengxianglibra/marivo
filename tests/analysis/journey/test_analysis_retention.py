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


@pytest.mark.runtime
@pytest.mark.parametrize("form", ["table", "parquet"])
def test_fixed_omega_truth_quantifiers_and_selection(tmp_path, form):
    session, anchors, returning, claims = build_retention(tmp_path, form=form)
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


@pytest.mark.runtime
@pytest.mark.parametrize("empty", [False, True])
def test_calendar_and_empty_omega(tmp_path, empty):
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


@pytest.mark.runtime
def test_raw_25_5_70_partition_and_shared_return(tmp_path):
    rows = [(0, "started", 0, i) for i in range(25)]
    rows += [(0, "finished", 5, 26)]
    rows += [(1, "started", 30, i) for i in range(5)]
    rows += [(2, "started", 90, i) for i in range(70)]
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
    ] == ["100", "25", "5", "70", "[0.25,0.95]"]
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
    for view, count in ((fixed.known_true(), 25), (fixed.known_false(), 5), (fixed.unknown(), 70)):
        selected = view.execute()
        assert len(selected.to_pandas()) == count
        assert dict(selected.contract()._facts)["deterministic_bounds"] == "[0.25,0.95]"
    for rule in (mv.any_anchor(), mv.every_anchor()):
        result = fixed.by_subject(rule=rule).execute()
        assert result.to_pandas().value.tolist() == [True, False, None]
        selected = result.known_true().execute()
        assert len(selected.members().execute().to_pandas()) == 1


@pytest.mark.runtime
@pytest.mark.parametrize(
    "same_event,second,sequence,expected",
    [(True, 0, 1, None), (False, 0, 2, True), (False, 10, 2, None), (False, 9, 2, True)],
)
def test_self_order_and_exclusive_deadline(tmp_path, same_event, second, sequence, expected):
    rows = (
        [(0, "started", 0, 1)]
        if same_event
        else [(0, "started", 0, 1), (0, "finished", second, sequence)]
    )
    _, anchors, returning, _ = build_retention(tmp_path, rows=rows)
    if same_event:
        returning = ms.participant_role(event=ms.ref.event("commerce.started"), name="subject")
    result = anchors.retention(returning, within=mv.elapsed(mv.duration(seconds=10))).execute()
    assert result.to_pandas().value.tolist() == [expected]


@pytest.mark.runtime
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


@pytest.mark.runtime
def test_logical_subject_quantifier_and_journey_origin(tmp_path):
    session, anchors, returning, claims = build_retention(tmp_path)
    logical = anchors.retention(
        returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims
    )
    assert logical.by_subject(rule=mv.any_anchor()).execute().to_pandas().value.tolist() == [
        True,
        None,
    ]
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


@pytest.mark.runtime
@pytest.mark.parametrize(
    "subject,occurrence", [(s, o) for s in ("s", "i", "c") for o in ("s", "i", "c")]
)
@pytest.mark.parametrize(
    "form,unit,zone",
    [("table", "us", z) for z in ("UTC", "America/New_York")]
    + [("parquet", u, z) for u in ("s", "ms", "us", "ns") for z in ("UTC", "America/New_York")],
)
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


@pytest.mark.parametrize(
    "target",
    [
        "analysis.AnyAnchor",
        "analysis.EveryAnchor",
        "analysis.dsl.any_anchor",
        "analysis.dsl.every_anchor",
        "analysis.LogicalRetentionResult",
        "analysis.MaterializedRetentionResult",
        "analysis.LogicalSubjectRetentionResult",
        "analysis.MaterializedSubjectRetentionResult",
        "analysis.dsl.AnchorDomain.retention",
        "analysis.dsl.InstanceRetention.by_subject",
        "analysis.dsl.Retention.known_true",
        "analysis.dsl.Retention.known_false",
        "analysis.dsl.Retention.unknown",
    ],
)
def test_retention_help_resolves_independently(target, capsys):
    import marivo

    marivo.help(target)
    rendered = capsys.readouterr().out
    assert target in rendered
    assert "Example" in rendered or "Acquire" in rendered


@pytest.mark.runtime
@pytest.mark.parametrize("fault", ["missing", "corrupt", "swapped"])
def test_required_retention_part_rejects_before_continuation_run(tmp_path, fault):
    session, anchors, returning, claims = build_retention(tmp_path)
    result = anchors.retention(
        returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims
    ).execute()
    projected = anchors.retention(
        returning, within=mv.calendar_days(1, ZoneInfo("UTC")), completeness=claims
    ).execute()
    target = next(p for p in result._dataset.artifact.descriptor.parts if p.role == "retention")
    path = tmp_path / target.local.project_relative_path / "data.parquet"
    original = path.read_bytes()
    before = session.runs().items
    if fault == "missing":
        path.unlink()
    elif fault == "corrupt":
        path.write_bytes(b"invalid retained partition")
    else:
        other = next(
            p for p in projected._dataset.artifact.descriptor.parts if p.role == "retention"
        )
        path.write_bytes(
            (tmp_path / other.local.project_relative_path / "data.parquet").read_bytes()
        )
    try:
        with pytest.raises(AnalysisError):
            session.artifact(result.state.artifact_ref).by_subject(rule=mv.every_anchor()).execute()
        assert session.runs().items == before
    finally:
        path.write_bytes(original)


@pytest.mark.runtime
@pytest.mark.parametrize(
    "calendar_start,hours", [("2026-03-07T17:00:00+00:00", 23), ("2026-10-31T16:00:00+00:00", 25)]
)
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


@pytest.mark.runtime
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("filtered", [False, True])
def test_known_true_subject_image_then_source_preparation(tmp_path, empty, filtered, monkeypatch):
    from marivo.analysis.materialization import retention_execution
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.journey.anchors_fixtures import build_anchors, event_anchors

    session, population, window, _, _ = build_anchors(tmp_path)
    if filtered:
        identity = population.read(ms.ref.dimension("commerce.subjects.sid"))
        population = identity.where(identity.value.eq(9007199254740993)).members()
    returning = ms.participant_role(event=ms.ref.event("commerce.finished"), name="subject")
    logical = event_anchors(session, population, window).retention(
        returning, within=mv.calendar_days(1, ZoneInfo("UTC"))
    )
    selected = logical.known_false() if empty else logical.known_true()
    if empty:
        # Empty true selection retains a decidable-true certificate.
        selected = logical.known_true()
        selected = selected.where(selected.value.eq(False))
    members = selected.members(through=selected.subject_binding)
    operation = members.observe(
        ms.ref.metric("commerce.fact_count"),
        during=window,
        via=ms.ref.relationship("commerce.participant"),
    )
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
    result = operation.execute()
    assert result.to_pandas().value.tolist() == ([] if empty else [6] if filtered else [6, 1])


@pytest.mark.runtime
@pytest.mark.parametrize("language", ["docs", "zh-cn/docs"])
def test_latest_retention_example_executes(tmp_path, monkeypatch, language, capsys):

    session, _, _, _ = build_retention(tmp_path)
    monkeypatch.setattr(mv.session, "get_or_create", lambda *args, **kwargs: session)
    from tests.support.documentation import _example

    code = _example(language, "anchor-retention")
    code = code.replace(
        'start="2026-08-01", end="2026-09-01"',
        'start="2026-02-01T00:00:00+00:00", end="2026-02-01T00:01:40+00:00"',
    )
    namespace = {}
    exec(compile(code, "latest-retention-example", "exec"), namespace)
    assert dict(namespace["retention"].contract()._facts)["omega_count"] == "4"
    assert len(namespace["subjects"].to_pandas()) == 2
    capsys.readouterr()


@pytest.mark.runtime
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


@pytest.mark.runtime
@pytest.mark.parametrize("point", ["insert_artifact", "before_commit", "cancel", "deadline"])
def test_retention_publication_failure_is_atomic(tmp_path, point):
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline

    session, anchors, returning, claims = build_retention(tmp_path)
    logical = anchors.retention(
        returning, within=mv.calendar_days(1, ZoneInfo("UTC")), completeness=claims
    )
    previous = logical.execute()

    def inject(actual):
        if actual == ("before_commit" if point in ("cancel", "deadline") else point):
            if point == "cancel":
                raise KeyboardInterrupt("injected retention cancellation")
            if point == "deadline":
                CURRENT.set(ExecuteDeadline(0, clock=lambda: 601))
            else:
                raise RuntimeError("injected retention publication failure")

    session._runtime._hook = inject
    try:
        with pytest.raises((AnalysisError, KeyboardInterrupt)):
            logical.execute()
    finally:
        session._runtime._hook = None
    assert session.artifact(previous.state.artifact_ref).to_pandas().equals(previous.to_pandas())
    with session._runtime.store._read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM action_resource_journal").fetchone()[0] == 0


@pytest.mark.runtime
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


@pytest.mark.runtime
@pytest.mark.parametrize("kind", ["enum", "precedence"])
@pytest.mark.parametrize("calendar", [False, True])
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


@pytest.mark.runtime
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


@pytest.mark.runtime
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


@pytest.mark.runtime
@pytest.mark.parametrize("form", ["table", "parquet"])
@pytest.mark.parametrize("calendar", [False, True])
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


@pytest.mark.runtime
@pytest.mark.parametrize("calendar", [False, True])
@pytest.mark.parametrize(
    "observed_from,observed_through,declared_from,declared_through,expected",
    [
        (0, 10, 0, 5, False),
        (0, 10, None, 5, False),
        (5, 10, 0, 5, False),
        (6, 10, 0, 5, None),
        (0, 5, 5, 10, False),
    ],
)
def test_retention_combines_exact_coverage_intervals(
    tmp_path,
    monkeypatch,
    calendar,
    observed_from,
    observed_through,
    declared_from,
    declared_through,
    expected,
):
    from marivo.analysis.domains.completeness import BoundedCoverageStartV1, EventCoverageReceiptV1
    from marivo.datasource.adapters import SourceSession

    session, anchors, returning, claims = build_retention(tmp_path, rows=[(0, "started", 0, 1)])
    span = 86400 if calendar else 10
    point = lambda value: START + timedelta(seconds=span * value // 10)
    declarations = tuple(
        replace(c, complete_through=point(declared_through))
        if declared_from is None
        else mv.BoundedCompletenessDeclarationV1(
            inputs=(returning.event,),
            complete_from=point(declared_from),
            complete_through=point(declared_through),
            rationale="Exact-bound regression declaration",
        )
        for c in claims
    )
    original_enter = SourceSession.__enter__

    def enter(source):
        result = original_enter(source)

        def provider(backend, request):
            if request.event_ref != returning.event:
                return None
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
    result = anchors.retention(
        returning,
        within=mv.calendar_days(1, ZoneInfo("UTC"))
        if calendar
        else mv.elapsed(mv.duration(seconds=span)),
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


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
def test_external_unknown_predicate_keeps_receiver_truth(tmp_path, fixed):
    _, anchors, returning, claims = build_retention(tmp_path)
    left = anchors.retention(
        returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims
    )
    right = anchors.retention(
        returning, within=mv.calendar_days(1, ZoneInfo("UTC")), completeness=claims
    )
    if fixed:
        left, right = left.execute(), right.execute()
    receiver, predicate = left.status, right.status
    result = receiver.where(mv.not_(predicate.value.is_defined())).execute()
    frame = result.to_pandas()
    assert frame.coord_1.tolist() == [9007199254740995, 9007199254740996, 9007199254740997]
    assert frame.value.tolist() == [None, False, None]
    facts = dict(result.contract()._facts)
    assert facts["selection"] == "predicate"
    assert facts["omega_count"] == "4"
    assert facts["deterministic_bounds"] == "[0.25,0.75]"
    with pytest.raises(AnalysisError):
        result.members(through=result.subject_binding)
