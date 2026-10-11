"""Independent calendar-key oracles and shared source/fixed/native boundaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._temporal import TimeScope, certify_period_calendar, certify_temporal_set
from marivo.analysis._comparison import _freeze_time, _TimeKey
from marivo.analysis.core.graph import MethodNode, method_node
from marivo.analysis.core.rules import CellCompare
from marivo.analysis.core.time_alignment import HolidayTimeMapping, evaluate_time_keys
from marivo.analysis.core.time_grid import BoundTimeGrid, bind_grid
from marivo.analysis.errors import AnalysisError
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_period_alignment import period, single_sql_difference
from tests.semantic.preview_helpers import certified_project, default_scope
from tests.shared_fixtures import DslCaseFactory

KeyKind = Literal["ordinal", "weekday", "holiday", "holiday_then_weekday"]


def occurrences(
    *,
    zone: str = "UTC",
    start: date | datetime = date(2026, 8, 2),
    end: date | datetime = date(2026, 8, 4),
    baseline_start: date = date(2026, 7, 2),
    baseline_end: date = date(2026, 7, 4),
) -> tuple[TimeScope, TimeScope]:
    snapshot = certify_temporal_set(
        temporal_set_ref=ms.ref.temporal_set("sales.holidays"),
        boundary_timezone=zone,
        coverage=(date(2026, 1, 1), date(2027, 1, 1)),
        rows=(
            {"id": "current", "start": start, "end": end},
            {
                "id": "baseline",
                "start": baseline_start if type(start) is date else start,
                "end": baseline_end if type(end) is date else end,
            },
        ),
        occurrence_id="id",
        start="start",
        end="end",
    )
    return snapshot.occurrence_scope("current"), snapshot.occurrence_scope("baseline")


def time_key(kind: KeyKind, *, timestamp: bool = False) -> _TimeKey:
    if kind == "ordinal":
        return mv.ordinal()
    if kind == "weekday":
        return mv.weekday()
    current, baseline = occurrences()
    if timestamp:
        snapshot = certify_temporal_set(
            temporal_set_ref=ms.ref.temporal_set("sales.holidays"),
            boundary_timezone="UTC",
            coverage=(date(2026, 1, 1), date(2027, 1, 1)),
            rows=tuple(
                {
                    "id": name,
                    "start": datetime.fromisoformat(f"2026-{month}-02T00:00:00+00:00"),
                    "end": datetime.fromisoformat(f"2026-{month}-04T00:00:00+00:00"),
                }
                for name, month in (("current", "08"), ("baseline", "07"))
            ),
            occurrence_id="id",
            start="start",
            end="end",
        )
        current, baseline = (
            snapshot.occurrence_scope("current"),
            snapshot.occurrence_scope("baseline"),
        )
    if kind == "holiday":
        return mv.holiday(current=current, baseline=baseline)
    return mv.holiday_then_weekday(current=current, baseline=baseline)


def grid(start: str, end: str, zone: str = "UTC") -> BoundTimeGrid:
    return bind_grid(mv.time_scope(start=start, end=end), mv.grain("day"), report_timezone=zone)


def keys(bound: BoundTimeGrid, key: _TimeKey) -> list[str]:
    return [aligned for _, aligned in evaluate_time_keys(bound, _freeze_time(key), "current")]


def test_weekday_and_ordinal_have_independent_complete_grid_oracles() -> None:
    bound = grid("2026-08-01", "2026-08-10")
    assert keys(bound, mv.weekday()) == [
        "weekday:6:1",
        "weekday:7:1",
        "weekday:1:1",
        "weekday:2:1",
        "weekday:3:1",
        "weekday:4:1",
        "weekday:5:1",
        "weekday:6:2",
        "weekday:7:2",
    ]
    assert keys(bound, mv.ordinal()) == [f"ordinal:{i}" for i in range(1, 10)]


def test_holiday_offsets_and_hybrid_outward_count_survive_window_clipping() -> None:
    current, baseline = occurrences()
    holiday = mv.holiday(current=current, baseline=baseline)
    hybrid = mv.holiday_then_weekday(current=current, baseline=baseline)
    bound = grid("2026-08-01", "2026-08-06")
    assert keys(bound, holiday) == [
        "holiday:-1",
        "holiday:0",
        "holiday:1",
        "holiday:2",
        "holiday:3",
    ]
    assert keys(bound, hybrid) == [
        "holiday_then_weekday:before:6:1",
        "holiday_then_weekday:holiday:0",
        "holiday_then_weekday:holiday:1",
        "holiday_then_weekday:after:2:1",
        "holiday_then_weekday:after:3:1",
    ]
    assert keys(grid("2026-07-18", "2026-07-19"), hybrid) == ["holiday_then_weekday:before:6:3"]
    assert keys(grid("2026-08-18", "2026-08-19"), hybrid) == ["holiday_then_weekday:after:2:3"]
    partial = grid("2026-08-01T12:00:00Z", "2026-08-03T12:00:00Z")
    assert keys(partial, holiday) == ["holiday:-1", "holiday:0", "holiday:1"]


def test_unequal_holidays_use_each_occurrences_own_half_open_boundary() -> None:
    current, baseline = occurrences(baseline_end=date(2026, 7, 5))
    mapping = _freeze_time(mv.holiday_then_weekday(current=current, baseline=baseline))
    assert [
        key for _, key in evaluate_time_keys(grid("2026-08-02", "2026-08-05"), mapping, "current")
    ] == [
        "holiday_then_weekday:holiday:0",
        "holiday_then_weekday:holiday:1",
        "holiday_then_weekday:after:2:1",
    ]
    assert [
        key for _, key in evaluate_time_keys(grid("2026-07-02", "2026-07-05"), mapping, "baseline")
    ] == [
        "holiday_then_weekday:holiday:0",
        "holiday_then_weekday:holiday:1",
        "holiday_then_weekday:holiday:2",
    ]


def test_dst_and_certified_day_use_frozen_civil_authority() -> None:
    current, baseline = occurrences(
        zone="America/New_York", start=date(2026, 3, 8), end=date(2026, 3, 10)
    )
    bound = grid("2026-03-07", "2026-03-10", "America/New_York")
    assert (bound.cells[1].end - bound.cells[1].start).total_seconds() == 23 * 3600
    assert keys(bound, mv.holiday(current=current, baseline=baseline)) == [
        "holiday:-1",
        "holiday:0",
        "holiday:1",
    ]
    snapshot = certify_period_calendar(
        calendar_ref=ms.ref.period_calendar("sales.fiscal"),
        boundary_timezone="UTC",
        coverage=(date(2026, 8, 1), date(2026, 8, 4)),
        levels={"week": "week"},
        rows=[{"date": date(2026, 8, 1) + timedelta(days=i), "week": "w"} for i in range(3)],
    )
    certified = bind_grid(
        mv.time_scope(start="2026-08-01", end="2026-08-04"),
        ms.calendar_grain(calendar=ms.ref.period_calendar("sales.fiscal"), level="day"),
        report_timezone="UTC",
        snapshot=snapshot,
    )
    assert keys(certified, mv.weekday()) == ["weekday:6:1", "weekday:7:1", "weekday:1:1"]


def test_holiday_admission_and_whole_day_timestamp() -> None:
    absolute = mv.time_scope(start="2026-08-01", end="2026-08-02")
    with pytest.raises(AnalysisError, match="certified TemporalSet occurrence"):
        mv.holiday(current=absolute, baseline=absolute)
    current, baseline = occurrences(
        start=datetime.fromisoformat("2026-03-08T05:00:00+00:00"),
        end=datetime.fromisoformat("2026-03-09T04:00:00+00:00"),
        zone="America/New_York",
    )
    key = mv.holiday(current=current, baseline=baseline)
    assert keys(grid("2026-03-08", "2026-03-09", "America/New_York"), key) == ["holiday:0"]
    with pytest.raises(AnalysisError, match="differs from grid"):
        keys(grid("2026-03-08", "2026-03-09"), key)
    current, baseline = occurrences(
        start=datetime.fromisoformat("2026-08-02T12:00:00+00:00"),
        end=datetime.fromisoformat("2026-08-03T12:00:00+00:00"),
    )
    with pytest.raises(AnalysisError, match="whole-day"):
        mv.holiday_then_weekday(current=current, baseline=baseline)
    weekly = bind_grid(absolute, mv.grain("week"), report_timezone="UTC")
    with pytest.raises(AnalysisError, match="single-day"):
        keys(weekly, mv.weekday())


def test_holiday_descriptors_keep_bounded_identity_and_show(
    capsys: pytest.CaptureFixture[str],
) -> None:
    long_key = "occurrence" * 2000
    snapshot = certify_temporal_set(
        temporal_set_ref=ms.ref.temporal_set("sales.holidays"),
        boundary_timezone="UTC",
        coverage=(date(2026, 1, 1), date(2027, 1, 1)),
        rows=({"id": long_key, "start": date(2026, 8, 1), "end": date(2026, 8, 4)},),
        occurrence_id="id",
        start="start",
        end="end",
    )
    scope = snapshot.occurrence_scope(long_key)
    for factory in (mv.holiday, mv.holiday_then_weekday):
        descriptor = factory(current=scope, baseline=scope)
        assert len(repr(descriptor)) < 100 and "\n" not in repr(descriptor)
        assert "capture=" in repr(descriptor)
        descriptor.show()
        text = capsys.readouterr().out
        assert len(text.encode()) <= 8193
        assert ".current.show()" in text and ".baseline.show()" in text
        descriptor.show(max_output_bytes=None)
        assert long_key in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["ordinal", "weekday", "holiday", "holiday_then_weekday"])
def test_all_time_keys_source_fixed_and_native_sql(
    analysis_dsl_case_factory: DslCaseFactory,
    kind: KeyKind,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    _, current = period(members, 8, 3)
    _, baseline = period(members, 7, 4)
    key = time_key(kind)
    comparison = current.compare(baseline, align=mv.outer(time=key))
    source = comparison.execute()
    fixed = current.execute().compare(baseline.execute(), align=mv.outer(time=key)).execute()
    assert source.to_pandas().equals(fixed.to_pandas())
    assert single_sql_difference(comparison).to_pandas().equals(source.absolute.to_pandas())
    assert source._dataset is not None and fixed._dataset is not None
    from marivo.analysis.materialization.cell_arrow import logical_table

    source_parts = {p.role: logical_table(p.table) for p in source._dataset.verified().parts}
    fixed_parts = {p.role: logical_table(p.table) for p in fixed._dataset.verified().parts}
    assert source_parts.keys() == fixed_parts.keys()
    assert all(table.equals(fixed_parts[role]) for role, table in source_parts.items())
    assert source._dataset.verified().primary.schema.field("key_1").type == pa.string()
    for result in (comparison, source, fixed):
        result.contract().show()
        assert f"aligned_time={kind} string keys" in capsys.readouterr().out
    source.show(n=0)
    assert f"aligned {kind} string keys" in capsys.readouterr().out
    if kind == "weekday":
        expected = {
            "weekday:6:1",
            "weekday:7:1",
            "weekday:1:1",
            "weekday:3:1",
            "weekday:4:1",
            "weekday:5:1",
        }
    elif kind == "holiday":
        expected = {"holiday:-1", "holiday:0", "holiday:1", "holiday:2"}
    elif kind == "holiday_then_weekday":
        expected = {
            "holiday_then_weekday:before:6:1",
            "holiday_then_weekday:before:3:1",
            "holiday_then_weekday:holiday:0",
            "holiday_then_weekday:holiday:1",
            "holiday_then_weekday:after:6:1",
        }
    else:
        expected = {"ordinal:1", "ordinal:2", "ordinal:3", "ordinal:4"}
    assert set(source.to_pandas().coord_0) == expected
    with pytest.raises(AnalysisError, match="equal aligned"):
        current.compare(baseline, align=mv.exact(time=key))
    with pytest.raises(AnalysisError, match="unobserved"):
        current.compare(baseline, align=mv.outer(missing="empty_value", time=key))
    for endpoint in (current, current.execute()):
        with pytest.raises(AnalysisError, match=f"{kind} exact ratio"):
            endpoint.ratio(endpoint, align=mv.exact(time=key))


def test_weekday_exact_uses_key_images_instead_of_equal_lengths(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    _, current = period(members, 8, 3)
    _, baseline = period(members, 7, 3)
    with pytest.raises(AnalysisError, match="equal aligned"):
        current.compare(baseline, align=mv.exact(time=mv.weekday()))
    _, current = period(members, 8, 7)
    _, baseline = period(members, 7, 7)
    result = current.compare(baseline, align=mv.exact(time=mv.weekday())).execute()
    assert set(result.to_pandas().coord_0) == {f"weekday:{i}:1" for i in range(1, 8)}


@pytest.mark.parametrize("kind", ["ordinal", "weekday", "holiday", "holiday_then_weekday"])
@pytest.mark.parametrize("metric", ["order_count", "revenue"])
def test_equal_key_images_keep_dual_cells_for_exact_and_empty_finish(
    analysis_dsl_case_factory: DslCaseFactory, kind: KeyKind, metric: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    current_grid, current = period(members, 8, 7, metric=metric, grouped=True)
    baseline_grid, baseline = period(members, 7, 7, metric=metric, grouped=True)
    region = ms.ref.dimension("sales.customer.region")
    current_grouped = current.group_by(region, current_grid).rollup()
    baseline_grouped = baseline.group_by(region, baseline_grid).rollup()
    key: _TimeKey
    if kind in ("ordinal", "weekday"):
        key = time_key(kind)
    else:
        current_scope, baseline_scope = occurrences(
            start=date(2026, 8, 1),
            end=date(2026, 8, 8),
            baseline_start=date(2026, 7, 1),
            baseline_end=date(2026, 7, 8),
        )
        factory = mv.holiday if kind == "holiday" else mv.holiday_then_weekday
        key = factory(current=current_scope, baseline=baseline_scope)
    for alignment in (mv.exact(time=key), mv.outer(missing="empty_value", time=key)):
        source = current_grouped.compare(baseline_grouped, align=alignment).execute()
        fixed = (
            current_grouped.execute().compare(baseline_grouped.execute(), align=alignment).execute()
        )
        assert source.to_pandas().equals(fixed.to_pandas())
        frame = source.to_pandas()
        assert len(frame) == 21
        empty = frame[frame.group.isin(("south", "west"))]
        if metric == "revenue":
            for field in ("absolute", "relative"):
                assert set(empty[field + "_cell_tag"]) == {"null"}
                assert set(empty[field + "_cell_reason"]) == {"empty_contribution"}
        else:
            assert set(empty.absolute_cell_tag) == {"defined"}
            assert set(empty.absolute) == {0}
            assert set(empty.relative_cell_tag) == {"undefined"}
            assert set(empty.relative_cell_reason) == {"zero_baseline"}


@pytest.mark.parametrize("kind", ["holiday", "holiday_then_weekday"])
def test_exact_holiday_absolute_keeps_original_attribution(
    analysis_dsl_case_factory: DslCaseFactory, kind: KeyKind
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    current_grid, current = period(members, 8, 3, grouped=True)
    baseline_grid, baseline = period(members, 7, 3, grouped=True)
    region = ms.ref.dimension("sales.customer.region")
    current_rollup = current.group_by(current_grid).rollup()
    baseline_rollup = baseline.group_by(baseline_grid).rollup()
    current_scope, baseline_scope = occurrences(
        start=date(2026, 8, 1),
        end=date(2026, 8, 4),
        baseline_start=date(2026, 7, 1),
        baseline_end=date(2026, 7, 4),
    )
    factory = mv.holiday if kind == "holiday" else mv.holiday_then_weekday
    alignment = mv.exact(time=factory(current=current_scope, baseline=baseline_scope))
    for left, right in (
        (current_rollup, baseline_rollup),
        (current_rollup.execute(), baseline_rollup.execute()),
    ):
        result = left.compare(right, align=alignment).execute()
        assert (
            result.absolute.attribute(axes=(region,)).execute().contribution.to_pandas().value.sum()
            == 1
        )


def test_occurrence_identity_and_snapshot_enter_definition_and_integrity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    _, current = period(members, 8, 3)
    _, baseline = period(members, 7, 3)
    first, second = occurrences()
    original = current.compare(
        baseline, align=mv.exact(time=mv.holiday(current=first, baseline=second))
    )
    node = original._node.root
    assert isinstance(node, MethodNode) and isinstance(node.parameters, CellCompare)
    assert node.parameters.time_keys is not None
    frozen = node.parameters.time_keys
    assert isinstance(frozen.mapping, HolidayTimeMapping)
    changed = replace(frozen.mapping.current, key="another")
    mapping = replace(frozen.mapping, current=changed)
    new = method_node(
        node.inputs,
        replace(node.parameters, time_keys=replace(frozen, mapping=mapping)),
        value_type=node.value_type,
    )
    assert new.fingerprint != node.fingerprint
    mapping = replace(
        frozen.mapping, current=replace(frozen.mapping.current, snapshot_digest="another-snapshot")
    )
    new = method_node(
        node.inputs,
        replace(node.parameters, time_keys=replace(frozen, mapping=mapping)),
        value_type=node.value_type,
    )
    assert new.fingerprint != node.fingerprint
    with pytest.raises(AnalysisError, match="complete frozen"):
        method_node(
            node.inputs,
            replace(
                node.parameters,
                time_keys=replace(
                    frozen,
                    current=tuple(
                        (bucket, "holiday:99" if i == 0 else aligned)
                        for i, (bucket, aligned) in enumerate(frozen.current)
                    ),
                ),
            ),
            value_type=node.value_type,
        )


def test_catalog_occurrence_round_trip_supplies_alignment_capture(
    tmp_path: Path, semantic_project_factory: Callable[[dict[str, str]], SemanticProject]
) -> None:
    catalog = certified_project(
        tmp_path=tmp_path, semantic_project_factory=semantic_project_factory
    )
    catalog.preview(
        ms.ref.temporal_set("sales.named_campaigns"), scope=default_scope(max_rows=2), limit=1
    )
    campaigns = catalog.temporal_sets.get("sales.named_campaigns")
    current, baseline = campaigns.occurrence("spring"), campaigns.occurrence("incident")
    mapping = _freeze_time(mv.holiday(current=current, baseline=baseline))
    assert isinstance(mapping, HolidayTimeMapping)
    assert mapping.current.snapshot_digest == current.snapshot_digest
    assert keys(
        grid("2026-03-01", "2026-03-04"), mv.holiday(current=current, baseline=baseline)
    ) == ["holiday:0", "holiday:1", "holiday:2"]


@pytest.mark.parametrize("damage", ["string_key", "snapshot_digest"])
def test_hybrid_snapshot_rejects_changed_frozen_authority(
    analysis_dsl_case_factory: DslCaseFactory, damage: str
) -> None:
    import base64
    import zlib

    from marivo.analysis.materialization.errors import IntegrityError
    from marivo.analysis.materialization.graph_protocol import thaw_graph
    from marivo.analysis.materialization.graph_snapshot import PREFIX, document_json, graph_document

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    _, current = period(members, 8, 3)
    _, baseline = period(members, 7, 3)
    current_scope, baseline_scope = occurrences()
    result = current.compare(
        baseline,
        align=mv.outer(
            time=mv.holiday_then_weekday(current=current_scope, baseline=baseline_scope)
        ),
    )
    body = document_json(graph_document(result._node.root))
    original, replacement = (
        ("holiday_then_weekday:holiday:0", "holiday_then_weekday:holiday:00")
        if damage == "string_key"
        else (current_scope.snapshot_digest, "forged-snapshot")
    )
    assert original in body
    changed = body.replace(original, replacement, 1)
    frozen = PREFIX + base64.b64encode(zlib.compress(changed.encode(), level=9)).decode("ascii")
    with pytest.raises(IntegrityError):
        thaw_graph(frozen)


@pytest.mark.parametrize("kind", ["holiday", "holiday_then_weekday"])
def test_timestamp_holiday_definition_survives_wire_round_trip(
    analysis_dsl_case_factory: DslCaseFactory, kind: KeyKind
) -> None:
    from marivo.analysis.materialization.graph_snapshot import freeze_graph, thaw_graph

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    _, current = period(members, 8, 3)
    _, baseline = period(members, 7, 3)
    comparison = current.compare(baseline, align=mv.outer(time=time_key(kind, timestamp=True)))
    node = comparison._node.root
    restored = thaw_graph(freeze_graph(node))
    assert restored.fingerprint == node.fingerprint
    assert restored.signature == node.signature


def test_timestamp_capture_round_trip_keeps_dst_civil_dates() -> None:
    from pydantic import TypeAdapter

    from marivo.analysis.core.time_alignment import TimeKeyMapping

    current, baseline = occurrences(
        start=datetime.fromisoformat("2026-03-08T05:00:00+00:00"),
        end=datetime.fromisoformat("2026-03-09T04:00:00+00:00"),
        zone="America/New_York",
    )
    original = _freeze_time(mv.holiday(current=current, baseline=baseline))
    adapter: TypeAdapter[TimeKeyMapping] = TypeAdapter(TimeKeyMapping)
    restored = adapter.validate_json(adapter.dump_json(original))
    assert repr(restored) == repr(original)
    assert isinstance(restored, HolidayTimeMapping)
    assert restored.current.dates == (date(2026, 3, 8), date(2026, 3, 9))
    assert isinstance(restored.current.start, datetime)
    assert isinstance(restored.current.end, datetime)
    assert restored.current.end - restored.current.start == timedelta(hours=23)
    assert evaluate_time_keys(
        grid("2026-03-08", "2026-03-09", "America/New_York"), restored, "current"
    ) == evaluate_time_keys(
        grid("2026-03-08", "2026-03-09", "America/New_York"), original, "current"
    )
