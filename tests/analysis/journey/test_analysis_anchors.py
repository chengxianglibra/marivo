"""Anchor windows, exact component uses and isolated source/fixed/cold cells."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import marivo
import marivo.analysis as mv
from marivo.analysis.anchors import deadline
from marivo.analysis.errors import AnalysisError
from tests.analysis.journey.anchors_fixtures import (
    build_anchors,
    event_anchors,
    journey,
    observations,
)
from tests.analysis.journey.anchors_oracle import assert_result
from tests.analysis.lifecycle.lifecycle_fixtures import RECOVERY_PROFILES

PROFILES = RECOVERY_PROFILES
KEYS = {"string": "s", "int64": "i", "composite(string,int64)": "c"}


@pytest.mark.parametrize(
    "kwargs,unit,ticks",
    [
        ({"hours": 2}, "s", 7200),
        ({"minutes": 3}, "s", 180),
        ({"seconds": -1}, "s", -1),
        ({"milliseconds": 2}, "ms", 2),
        ({"microseconds": 3}, "us", 3),
        ({"nanoseconds": 4}, "ns", 4),
    ],
)
def test_named_duration(kwargs, unit, ticks):
    result = mv.duration(**kwargs)
    assert (result.unit, result.ticks) == (unit, ticks)
    assert len(repr(result).splitlines()) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"seconds": 1, "hours": 1},
        {"seconds": True},
        {"seconds": 1.0},
        {"hours": 2**63},
        {"nanoseconds": -(2**63) - 1},
    ],
)
def test_duration_rejects_inexact_or_overflow(kwargs):
    with pytest.raises(AnalysisError):
        mv.duration(**kwargs)


@pytest.mark.parametrize(
    "instant,elapsed_hours",
    [(datetime(2026, 3, 7, 17, tzinfo=UTC), 23), (datetime(2026, 10, 31, 16, tzinfo=UTC), 25)],
)
def test_calendar_wall_time_differs_from_elapsed_across_dst(instant, elapsed_hours):
    window = mv.calendar_days(1, ZoneInfo("America/New_York"))
    assert deadline(instant, window) - instant == timedelta(hours=elapsed_hours)
    assert deadline(instant, mv.elapsed(mv.duration(hours=24))) - instant == timedelta(hours=24)


@pytest.mark.parametrize(
    "instant", [datetime(2026, 3, 7, 7, 30, tzinfo=UTC), datetime(2026, 10, 31, 5, 30, tzinfo=UTC)]
)
def test_calendar_gap_and_fold_reject_with_repair(instant):
    with pytest.raises(AnalysisError) as failure:
        deadline(instant, mv.calendar_days(1, ZoneInfo("America/New_York")))
    assert failure.value.constraint_id == "r7.calendar_deadline"
    assert "elapsed" in failure.value.repair.action


def test_positive_window_and_exact_deadline():
    for ticks in (0, -1):
        with pytest.raises(AnalysisError):
            mv.elapsed(mv.duration(seconds=ticks))
    with pytest.raises(AnalysisError):
        deadline(datetime(2026, 1, 1, tzinfo=UTC), mv.elapsed(mv.duration(nanoseconds=1)))
    assert (
        deadline(
            datetime(2026, 1, 1, tzinfo=UTC), mv.elapsed(mv.duration(nanoseconds=1000))
        ).microsecond
        == 1
    )
    with pytest.raises(AnalysisError):
        mv.calendar_days(1, UTC)


@pytest.mark.parametrize(
    "target",
    [
        "analysis.dsl.duration",
        "analysis.dsl.elapsed",
        "analysis.dsl.calendar_days",
        "analysis.Duration",
        "analysis.ElapsedWindow",
        "analysis.CalendarWindow",
        "analysis.LogicalAnchorDomain",
        "analysis.MaterializedAnchorDomain",
        "analysis.session.anchors",
        "analysis.dsl.AnchorDomain.observe",
    ],
)
def test_anchor_help_independently_resolves(target, capsys):
    marivo.help(target)
    text = capsys.readouterr().out
    assert target in text
    assert "Example" in text or "Acquire:" in text


@pytest.mark.runtime
@pytest.mark.parametrize(
    "key,time", PROFILES, ids=[key["id"] + "-" + time["id"] for key, time in PROFILES]
)
def test_anchor_views_source_fixed_cold(tmp_path, key, time):
    config = {
        "subject": KEYS[key["subject"]],
        "occurrence": KEYS[key["occurrence"]],
        "form": "table" if time["source_form"] == "duckdb_native_table" else "parquet",
        "unit": time["occurrence_unit"],
        "zone": time["report_timezone"],
        "qualification_history": True,
    }
    (tmp_path / "config.json").write_text(json.dumps(config))
    receipts = []
    for phase in ("produce", "continue", "cold"):
        run = subprocess.run(
            [sys.executable, "-m", "tests.analysis.journey.anchors_worker", phase, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=580,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        receipts.append(json.loads(run.stdout.splitlines()[-1]))
    assert receipts[-1]["exact_hit"]
    evidence = os.getenv("MARIVO_R77_EVIDENCE_DIR")
    if evidence:
        destination = Path(evidence)
        destination.mkdir(exist_ok=True, parents=True)
        (destination / (key["id"] + "-" + time["id"] + ".json")).write_text(
            json.dumps({"key": key["id"], "time": time["id"], "phases": receipts}, sort_keys=True)
        )


@pytest.mark.runtime
def test_overlap_parts_and_fixed_transport(tmp_path):
    session, members, window, claims, values = build_anchors(tmp_path)
    logical = observations(event_anchors(session, members, window))[0]
    fixed = logical.execute()
    assert_result(fixed, values, "i", "i", 0)
    assert "rollup" not in {a.call for a in fixed.contract().actions}
    with pytest.raises(AnalysisError):
        fixed.rollup()
    selected = fixed.where(fixed.value.is_defined()).execute()
    assert selected._dataset.verified().parts == fixed._dataset.verified().parts


@pytest.mark.runtime
@pytest.mark.parametrize(
    "kwargs,unit",
    [
        ({"seconds": 10}, "s"),
        ({"milliseconds": 10000}, "ms"),
        ({"microseconds": 10000000}, "us"),
        ({"nanoseconds": 10000000000}, "ns"),
    ],
)
def test_elapsed_units_keep_exact_window_through_recovery(tmp_path, kwargs, unit, monkeypatch):
    import marivo.semantic as ms
    from marivo.analysis.core.model import AnchorObservationPart
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.journey.anchors_worker import forbidden

    session, members, window, _, values = build_anchors(tmp_path)
    result = (
        event_anchors(session, members, window)
        .observe(
            ms.ref.metric("commerce.fact_count"),
            within=mv.elapsed(mv.duration(**kwargs)),
            via=ms.ref.relationship("commerce.participant"),
        )
        .execute()
    )
    assert_result(result, values, "i", "i", 0)
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    recovered = session.artifact(result.state.artifact_ref.ref)
    declaration = next(
        part
        for part in recovered._node.root.signature.parts
        if isinstance(part, AnchorObservationPart)
    )
    assert declaration.window.duration.unit == unit
    selected = recovered.where(recovered.value.is_defined()).execute()
    assert selected._dataset.verified().parts == recovered._dataset.verified().parts


@pytest.mark.runtime
@pytest.mark.parametrize(
    "point",
    [
        "insert_artifact",
        "insert_evidence",
        "insert_findings",
        "insert_terminal",
        "before_commit",
        "cancel",
        "deadline",
    ],
)
def test_anchor_failure_is_atomic(tmp_path, point):
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline

    session, members, window, _, _ = build_anchors(tmp_path)
    logical = observations(event_anchors(session, members, window), calendar=True)[0]
    fixed = logical.execute()
    reference, frame = fixed.state.artifact_ref.ref, fixed.to_pandas()

    def inject(actual):
        if actual == ("insert_findings" if point in ("cancel", "deadline") else point):
            if point == "cancel":
                raise KeyboardInterrupt("injected Anchor cancellation")
            if point == "deadline":
                CURRENT.set(ExecuteDeadline(0, clock=lambda: 601))
            else:
                raise RuntimeError("injected Anchor publication failure")

    session._runtime._hook = inject
    with pytest.raises((AnalysisError, KeyboardInterrupt)):
        logical.execute()
    session._runtime._hook = None
    assert session.artifact(reference).to_pandas().equals(frame)
    with session._runtime.store._read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM dataset_evidence").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM action_resource_journal").fetchone()[0] == 0


@pytest.mark.runtime
@pytest.mark.parametrize("role", ["primary", "subject", "anchor", "original_state", "coverage"])
@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_receipt_damage_rejects_read_continuation_and_hit(tmp_path, role, fault):
    session, members, window, _, _ = build_anchors(tmp_path)
    fixed = observations(event_anchors(session, members, window))[0].execute()
    logical = fixed.where(fixed.value.is_defined())
    continued = logical.execute()
    descriptor = continued._dataset.artifact.descriptor
    receipt = (
        descriptor.primary_receipt.local
        if role == "primary"
        else next(part.local for part in descriptor.parts if part.role == role)
    )
    path = tmp_path / receipt.project_relative_path / receipt.file_manifest[0].relative_path
    path.unlink() if fault == "missing" else path.write_bytes(b"corrupt Anchor receipt")
    before = session.runs().items
    with pytest.raises(AnalysisError):
        session.artifact(continued.state.artifact_ref.ref)
    with pytest.raises(AnalysisError):
        logical.execute()
    assert session.runs().items == before
    fixed.to_pandas()


@pytest.mark.runtime
@pytest.mark.parametrize("fault", ["deadline", "uses", "subject", "state", "definition"])
def test_exchange_rejects_wrong_bound_parts(tmp_path, fault):
    import pyarrow as pa

    from marivo.analysis.materialization.graph_exchange import from_arrow

    session, members, window, _, _ = build_anchors(tmp_path)
    fixed = observations(event_anchors(session, members, window))[0].execute()
    verified = fixed._dataset.verified()
    parts, contract = list(verified.parts), verified.contract
    if fault == "definition":
        declaration = next(
            part
            for part in contract.signature.parts
            if type(part).__name__ == "AnchorObservationPart"
        )
        domain = replace(
            declaration.domain,
            during_start=(datetime(2027, 1, 1, tzinfo=UTC)).isoformat(),
            during_end=(datetime(2027, 2, 1, tzinfo=UTC)).isoformat(),
        )
        contract = replace(
            contract,
            signature=replace(
                contract.signature,
                parts=tuple(
                    replace(part, domain=domain) if part is declaration else part
                    for part in contract.signature.parts
                ),
            ),
        )
    else:
        role, field = {
            "deadline": ("anchor", "anchor__deadline"),
            "uses": ("anchor", "anchor__uses_0"),
            "subject": ("subject", "subject__key_0"),
            "state": ("original_state", "original_state__count"),
        }[fault]
        index = next(i for i, part in enumerate(parts) if part.role == role)
        table = parts[index].table
        rows = table.to_pylist()
        if fault == "deadline":
            rows[0][field] += timedelta(microseconds=1)
        elif fault == "uses":
            rows[0][field] = []
        else:
            rows[0][field] += 1
        parts[index] = replace(parts[index], table=pa.Table.from_pylist(rows, schema=table.schema))
    with pytest.raises(AnalysisError):
        from_arrow(verified.primary, contract, parts=tuple(parts))


@pytest.mark.runtime
def test_anchor_inputs_reject_before_run_or_rows(tmp_path, monkeypatch):
    import marivo.semantic as ms
    from marivo.analysis.session.core import Session
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.journey.anchors_worker import forbidden

    session, members, window, claims, _ = build_anchors(tmp_path)
    fixed = members.execute()
    wrong_subject = session.members(ms.ref.entity("commerce.facts"))
    logical_journey = journey(session, members, window, claims)
    from marivo.analysis.materialization.admission import DatasetRuntime

    other = Session._from_runtime(
        DatasetRuntime(
            session._runtime.store, session._runtime.store.create_session("foreign").session_ref
        )
    )
    before = session.runs().items
    monkeypatch.setattr(SourceSession, "batches", forbidden)
    role = ms.participant_role(event=ms.ref.event("commerce.started"), name="subject")
    with pytest.raises(AnalysisError):
        session.anchors(role, population=fixed, during=window)
    with pytest.raises(AnalysisError):
        other.anchors(role, population=members, during=window)
    with pytest.raises(AnalysisError):
        session.anchors(
            logical_journey,
            population=members,
            during=window,
            business_order=ms.ref.business_order("commerce.order"),
        )
    with pytest.raises(AnalysisError):
        session.anchors(logical_journey, population=fixed, during=window)
    with pytest.raises(AnalysisError):
        session.anchors(role, population=wrong_subject, during=window)
    with pytest.raises(AnalysisError):
        session.anchors(logical_journey, population=wrong_subject, during=window)
    assert session.runs().items == before


@pytest.mark.runtime
def test_source_prefix_finishes_before_calendar_consumption(tmp_path, monkeypatch):
    from marivo.analysis.materialization import anchor_execution
    from marivo.datasource.adapters import SourceSession

    session, members, window, _, values = build_anchors(tmp_path)
    logical = observations(event_anchors(session, members, window), calendar=True)[0]
    started = False
    read, observe = SourceSession.batches, anchor_execution.observe

    def batches(*args, **kwargs):
        assert not started, "source-after-local Anchor consumption"
        return read(*args, **{**kwargs, "chunk_size": 1})

    def consume(*args, **kwargs):
        nonlocal started
        started = True
        return observe(*args, **kwargs)

    monkeypatch.setattr(SourceSession, "batches", batches)
    monkeypatch.setattr(anchor_execution, "observe", consume)
    assert_result(logical.execute(), values, "i", "i", 0, calendar=True)
    assert started


@pytest.mark.runtime
@pytest.mark.parametrize(
    "calendar_start,hours",
    [(datetime(2026, 3, 7, 17, tzinfo=UTC), 23), (datetime(2026, 10, 31, 16, tzinfo=UTC), 25)],
)
def test_runtime_dst_deadline_is_exclusive(tmp_path, calendar_start, hours):
    from zoneinfo import ZoneInfo

    import marivo.semantic as ms
    from tests.analysis.lifecycle.lifecycle_fixtures import START

    offset = int((calendar_start - START).total_seconds())
    rows = [
        (0, "started", offset, 1),
        (0, "pulse", offset + hours * 3600 - 1, 2),
        (0, "pulse", offset + hours * 3600, 3),
    ]
    session, members, _, _, _ = build_anchors(tmp_path, rows=rows)
    scope = mv.time_scope(
        start=calendar_start.isoformat(), end=(calendar_start + timedelta(seconds=1)).isoformat()
    )
    anchors = event_anchors(session, members, scope)
    route = ms.ref.relationship("commerce.participant")
    calendar = anchors.observe(
        ms.ref.metric("commerce.fact_count"),
        within=mv.calendar_days(1, ZoneInfo("America/New_York")),
        via=route,
    ).execute()
    elapsed = anchors.observe(
        ms.ref.metric("commerce.fact_count"), within=mv.elapsed(mv.duration(hours=24)), via=route
    ).execute()
    assert calendar.to_pandas().value.tolist() == [1]
    assert elapsed.to_pandas().value.tolist() == ([2] if hours == 23 else [0])


@pytest.mark.runtime
def test_same_instant_without_order_rejects_and_no_anchor_subject_is_omitted(tmp_path):
    import marivo.semantic as ms

    rows = [(0, "started", 0, 1), (0, "pulse", 0, 2)]
    session, members, window, _, _ = build_anchors(tmp_path, rows=rows, ordered=False)
    role = ms.participant_role(event=ms.ref.event("commerce.started"), name="subject")
    anchors = session.anchors(role, population=members, during=window)
    assert len(anchors.execute().to_pandas()) == 1
    for within in (mv.elapsed(mv.duration(seconds=1)), mv.calendar_days(1, ZoneInfo("UTC"))):
        with pytest.raises(AnalysisError) as failure:
            anchors.observe(
                ms.ref.metric("commerce.fact_count"),
                within=within,
                via=ms.ref.relationship("commerce.participant"),
            ).execute()
        assert failure.value.constraint_id.startswith("r7.business_order")


@pytest.mark.runtime
def test_every_disclosed_fixed_K_in_three_processes(tmp_path):
    config = {
        "subject": "c",
        "occurrence": "c",
        "form": "parquet",
        "unit": "us",
        "zone": "America/New_York",
        "all_K": True,
        "qualification_history": True,
    }
    (tmp_path / "config.json").write_text(json.dumps(config))
    receipts = []
    for phase in ("produce", "continue", "cold"):
        run = subprocess.run(
            [sys.executable, "-m", "tests.analysis.journey.anchors_worker", phase, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=580,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        receipts.append(json.loads(run.stdout.splitlines()[-1]))
    assert receipts[1]["executed_K"] == receipts[2]["executed_K"]
    assert receipts[2]["fixed_K"] == 152
    evidence = os.getenv("MARIVO_R77_EVIDENCE_DIR")
    if evidence:
        path = Path(evidence) / "all-K.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipts, sort_keys=True))


@pytest.mark.runtime
@pytest.mark.parametrize("calendar", [False, True])
def test_empty_anchor_domain_retains_exact_schema(tmp_path, calendar):
    session, members, window, _, _ = build_anchors(tmp_path, empty=True)
    anchors = event_anchors(session, members, window)
    for operation in observations(anchors, calendar=calendar):
        result = operation.execute()
        assert result.to_pandas().empty
        continued = result.where(result.value.is_defined()).execute()
        assert continued.to_pandas().empty
        assert session.artifact(result.state.artifact_ref).to_pandas().empty


@pytest.mark.runtime
@pytest.mark.parametrize("calendar", [False, True])
@pytest.mark.parametrize(
    "field,value,index",
    [
        ("integer", "9223372036854775807", 1),
        ("amount", "1e308", 2),
        ("decimal", "99999999999999999999999999999999.000000", 3),
        ("ticks", "to_microseconds(9223372036854775807)", 4),
    ],
)
def test_numeric_overflow_is_atomic(tmp_path, calendar, field, value, index):
    import ibis

    session, members, window, _, _ = build_anchors(tmp_path)
    old = members.execute()
    backend = ibis.duckdb.connect(tmp_path / "source.duckdb")
    try:
        backend.raw_sql(f"UPDATE facts SET {field}={value}")
    finally:
        backend.disconnect()
    with pytest.raises((AnalysisError, OverflowError)):
        observations(event_anchors(session, members, window), calendar=calendar)[index].execute()
    assert session.artifact(old.state.artifact_ref).to_pandas().equals(old.to_pandas())
    with session._runtime.store._read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == 1


@pytest.mark.runtime
@pytest.mark.parametrize("form", ["table", "parquet"])
@pytest.mark.parametrize("kind", ["snapshot", "validity"])
@pytest.mark.parametrize("calendar", [False, True])
def test_historical_subject_mapping_at_component_time(tmp_path, form, kind, calendar, monkeypatch):
    import marivo.semantic as ms
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.journey.anchors_fixtures import with_history_route
    from tests.analysis.journey.anchors_worker import forbidden

    rows = [(0, "started", 0, 1), (1, "started", 0, 1), (0, "pulse", 5, 2), (0, "pulse", 86405, 3)]
    session, _, during, _, _ = build_anchors(tmp_path, rows=rows, form=form)
    session, members = with_history_route(tmp_path, session, kind=kind, form=form)
    anchors = event_anchors(session, members, during)
    window = mv.calendar_days(2, ZoneInfo("UTC")) if calendar else mv.elapsed(mv.duration(hours=48))
    fixed = anchors.observe(
        ms.ref.metric("commerce.fact_count"),
        within=window,
        via=mv.routes(
            mv.route(
                ms.ref.entity("commerce.facts"),
                through=(
                    ms.ref.relationship("commerce.facts_history"),
                    ms.ref.relationship("commerce.history_subject"),
                ),
            )
        ),
    ).execute()
    assert sorted(fixed.to_pandas()["value"].tolist()) == [1, 1]
    monkeypatch.setattr(SourceSession, "batches", forbidden)
    assert fixed.where(fixed.value.is_defined()).execute().to_pandas()["value"].tolist() == [1, 1]
    assert session.artifact(fixed.state.artifact_ref).to_pandas()["value"].tolist() == [1, 1]


@pytest.mark.runtime
def test_latest_anchor_example_executes(tmp_path, monkeypatch):

    session, _, _, _, values = build_anchors(tmp_path)
    monkeypatch.setattr(mv.session, "get_or_create", lambda *args, **kwargs: session)
    from tests.support.documentation import _example

    code = _example("en", "anchor-observation")
    code = code.replace(
        'start="2026-08-01", end="2026-09-01"',
        'start="2026-02-01T00:00:00+00:00", end="2026-02-01T00:01:40+00:00"',
    )
    namespace = {}
    exec(compile(code, "latest-anchor-example", "exec"), namespace)
    assert_result(namespace["relative"], values, "i", "i", 2, calendar=True)


@pytest.mark.runtime
@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("calendar", [False, True])
def test_journey_origin_observes_without_rematch(tmp_path, shared, calendar, monkeypatch):
    from marivo.analysis.materialization import anchor_execution
    from marivo.analysis.methods import journey_matching
    from tests.analysis.journey.anchors_worker import forbidden

    session, members, window, claims, values = build_anchors(tmp_path)
    original = journey(session, members, window, claims, shared=shared)
    anchors = session.anchors(original, population=members, during=window)
    bind = anchor_execution.bind

    def guarded(*args, **kwargs):
        result = bind(*args, **kwargs)
        monkeypatch.setattr(journey_matching, "match", forbidden)
        return result

    monkeypatch.setattr(anchor_execution, "bind", guarded)
    result = observations(anchors, calendar=calendar)[0].execute()
    assert_result(result, values, "i", "i", 0, calendar=calendar)


@pytest.mark.runtime
@pytest.mark.parametrize("calendar", [False, True])
def test_first_journey_start_preserves_assignment_in_fixed_bind(tmp_path, calendar, monkeypatch):
    from marivo.analysis.methods import journey_matching
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.journey.anchors_worker import forbidden

    session, members, window, claims, values = build_anchors(tmp_path)
    original = journey(session, members, window, claims, first=True)
    logical = session.anchors(original, population=members, during=window)
    result = observations(logical, calendar=calendar)[0].execute()
    assert_result(result, values, "i", "i", 0, calendar=calendar, first=True)
    fixed_journey, fixed_members = original.execute(), members.execute()
    monkeypatch.setattr(journey_matching, "match", forbidden)
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    fixed = session.anchors(fixed_journey, population=fixed_members, during=window).execute()
    assert fixed.to_pandas().shape[0] == result.to_pandas().shape[0]
    assignments = next(p.table for p in fixed._dataset.verified().parts if p.role == "anchor")
    assert all(row["anchor__assignment"] for row in assignments.to_pylist())


@pytest.mark.runtime
@pytest.mark.parametrize("calendar", [False, True])
def test_journey_relative_numeric_families(tmp_path, calendar):
    session, members, window, claims, values = build_anchors(tmp_path)
    original = journey(session, members, window, claims, shared=True)
    anchors = session.anchors(original, population=members, during=window)
    for index, operation in enumerate(observations(anchors, calendar=calendar)):
        assert_result(operation.execute(), values, "i", "i", index, calendar=calendar)


@pytest.mark.runtime
@pytest.mark.parametrize("fault", ["cancel", "deadline"])
def test_local_consumption_failure_closes_prepared_resources(tmp_path, fault, monkeypatch):
    from marivo.analysis.materialization import anchor_execution
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline

    session, members, window, _, _ = build_anchors(tmp_path)
    previous = members.execute()
    observe = anchor_execution.observe

    def fail(*args, **kwargs):
        if fault == "cancel":
            raise KeyboardInterrupt("cancelled after source preparation")
        CURRENT.set(ExecuteDeadline(0, clock=lambda: 601))
        return observe(*args, **kwargs)

    monkeypatch.setattr(anchor_execution, "observe", fail)
    with pytest.raises((AnalysisError, KeyboardInterrupt)):
        observations(event_anchors(session, members, window), calendar=True)[0].execute()
    session.artifact(previous.state.artifact_ref).to_pandas()
    with session._runtime.store._read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM action_resource_journal").fetchone()[0] == 0


@pytest.mark.runtime
@pytest.mark.parametrize("calendar", [False, True])
def test_null_contributions_and_empty_policies(tmp_path, calendar):
    from decimal import Decimal

    import ibis
    import pyarrow as pa

    import marivo.semantic as ms
    from marivo.analysis.session.core import Session

    rows = [(0, "started", 0, 1), (0, "pulse", 5, 2)]
    session, members, window, _, _ = build_anchors(tmp_path, rows=rows)
    backend = ibis.duckdb.connect(tmp_path / "source.duckdb")
    try:
        backend.raw_sql("UPDATE facts SET amount=NULL, integer=NULL, decimal=NULL, ticks=NULL")
    finally:
        backend.disconnect()
    operations = observations(event_anchors(session, members, window), calendar=calendar)
    for index, expected_value in enumerate((1, 0, 0.0, Decimal("0.000000"), 0)):
        primary = operations[index].execute()._dataset.verified().primary
        values = primary["value"].cast(pa.int64()) if index == 4 else primary["value"]
        assert values.to_pylist() == [expected_value]
        assert primary["cell_tag"].to_pylist() == ["defined"]
    path = tmp_path / "models/semantic/commerce/objects.py"
    path.write_text(path.read_text().replace("empty=ms.empty.zero()", "empty=ms.empty.null()"))
    ms.load(workspace_dir=tmp_path)
    session = Session._from_runtime(session._runtime)
    members = session.members(ms.ref.entity("commerce.subjects"))
    result = observations(event_anchors(session, members, window), calendar=calendar)[1].execute()
    primary = result._dataset.verified().primary
    assert primary["value"].to_pylist() == [None]
    assert primary["cell_reason"].to_pylist() == ["empty_contribution"]


@pytest.mark.runtime
def test_fixed_anchor_rejects_new_live_metric_before_run(tmp_path, monkeypatch):
    import marivo.semantic as ms
    from marivo.datasource.adapters import SourceSession
    from marivo.semantic.reader import SemanticProject
    from tests.analysis.journey.anchors_worker import forbidden

    session, members, window, _, _ = build_anchors(tmp_path)
    fixed = event_anchors(session, members, window).execute()
    before = session.runs().items
    monkeypatch.setattr(SourceSession, "batches", forbidden)
    monkeypatch.setattr(SemanticProject, "load", forbidden)
    with pytest.raises(AnalysisError) as failure:
        fixed.observe(
            ms.ref.metric("commerce.fact_count"),
            within=mv.elapsed(mv.duration(seconds=10)),
            via=ms.ref.relationship("commerce.participant"),
        )
    assert "no retained Metric input" in failure.value.received
    assert "numeric Artifact" in failure.value.repair.action
    assert session.runs().items == before


@pytest.mark.runtime
@pytest.mark.parametrize("empty", [False, True])
def test_calendar_checks_actual_anchor_deadlines(tmp_path, empty):
    import marivo.semantic as ms
    from tests.analysis.lifecycle.lifecycle_fixtures import START

    point = datetime(2026, 3, 7, 7, 30, tzinfo=UTC)
    offset = int((point - START).total_seconds())
    session, members, _, _, _ = build_anchors(
        tmp_path, rows=[(0, "started", offset, 1)], empty=empty
    )
    anchors = event_anchors(
        session,
        members,
        mv.time_scope(start=point.isoformat(), end=(point + timedelta(seconds=1)).isoformat()),
    )
    operation = anchors.observe(
        ms.ref.metric("commerce.fact_count"),
        within=mv.calendar_days(1, ZoneInfo("America/New_York")),
        via=ms.ref.relationship("commerce.participant"),
    )
    if empty:
        assert operation.execute().to_pandas().empty
    else:
        with pytest.raises(AnalysisError) as failure:
            operation.execute()
        assert failure.value.constraint_id == "r7.calendar_deadline"


@pytest.mark.runtime
def test_native_float_cancellation_uses_analysis_error_owner(tmp_path):
    import ibis

    from marivo.analysis.methods.comparison import roundoff

    rows = [(0, "started", 0, 1), (0, "pulse", 1, 2), (0, "pulse", 2, 3), (0, "pulse", 3, 4)]
    session, members, window, _, _ = build_anchors(tmp_path, rows=rows)
    backend = ibis.duckdb.connect(tmp_path / "source.duckdb")
    try:
        backend.raw_sql(
            "UPDATE facts SET amount=CASE seq WHEN 2 THEN 1e16 WHEN 3 THEN 1.0 WHEN 4 THEN -1e16 ELSE 0 END"
        )
    finally:
        backend.disconnect()
    result = observations(event_anchors(session, members, window))[2].execute()
    assert abs(result.to_pandas()["value"].iloc[0] - 1.0) <= roundoff(2e16 + 1)
    result.where(result.value.is_defined()).execute()


@pytest.mark.runtime
def test_relative_template_cannot_execute_as_a_metric(tmp_path, monkeypatch):
    import marivo.semantic as ms
    from marivo.analysis.materialization.graph_observation import observe_members
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.journey.anchors_worker import forbidden

    session, members, window, _, _ = build_anchors(tmp_path)
    live = members._node.binding
    template = observe_members(
        live.graph,
        ms.ref.metric("commerce.fact_count"),
        during=window,
        via=ms.ref.relationship("commerce.participant"),
        sidecar=live.sidecar,
        report_timezone="UTC",
        relative=True,
    )
    before = session.runs().items
    monkeypatch.setattr(SourceSession, "batches", forbidden)
    with pytest.raises(AnalysisError):
        template.execute()
    assert session.runs().items == before


@pytest.mark.runtime
def test_anchor_root_routes_keep_declared_root_authority(tmp_path, monkeypatch):
    import marivo.semantic as ms
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.journey.anchors_worker import forbidden

    session, members, window, _, _ = build_anchors(tmp_path)
    anchors = event_anchors(session, members, window)
    monkeypatch.setattr(SourceSession, "batches", forbidden)
    before = session.runs().items
    with pytest.raises(AnalysisError):
        anchors.observe(
            ms.ref.metric("commerce.fact_count"),
            within=mv.elapsed(mv.duration(seconds=10)),
            via=mv.routes(
                mv.route(
                    ms.ref.entity("commerce.other"),
                    through=(ms.ref.relationship("commerce.participant"),),
                )
            ),
        )
    assert session.runs().items == before


@pytest.mark.runtime
@pytest.mark.parametrize("empty", [False, True])
def test_actual_anchor_subject_image_source_first(tmp_path, empty, monkeypatch):
    import marivo.semantic as ms
    from marivo.analysis.materialization import anchor_execution, graph_local_execution
    from marivo.datasource.adapters import SourceSession

    session, members, window, _, _ = build_anchors(tmp_path)
    numeric = observations(event_anchors(session, members, window))[0]
    selected = numeric.where(numeric.value.lt(0) if empty else numeric.value.gt(0))
    operation = selected.members().observe(
        ms.ref.metric("commerce.fact_count"),
        during=window,
        via=ms.ref.relationship("commerce.participant"),
    )
    reads = 0
    batches = SourceSession.batches

    def guard(*args, **kwargs):
        nonlocal reads
        reads += 1
        return batches(*args, **kwargs)

    def reject_local(*args, **kwargs):
        raise AssertionError("event-origin elapsed observation left its Ibis route")

    monkeypatch.setattr(SourceSession, "batches", guard)
    monkeypatch.setattr(anchor_execution, "observe", reject_local)
    monkeypatch.setattr(graph_local_execution, "execute_fixed_transport", reject_local)
    fixed = operation.execute()
    assert reads > 0
    assert fixed.to_pandas()["value"].tolist() == ([] if empty else [6])
    with session._runtime.store._read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == 1
