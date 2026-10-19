"""Complete-key correspondence kernels, independent of Query wrapper integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition.correspondence import ComparisonAxis, CorrespondenceAxis
from marivo._definition.deferred import RelationshipParameter
from marivo._definition.types import AxisBinding, CalendarCoordinate, ExactRef
from marivo._temporal import (
    anchor_from_datetime,
    builtin_grain,
    certify_period_calendar,
    semantic_grain,
)
from marivo.analysis.binding.correspondence import (
    ComparisonEndpoint,
    CorrespondenceRelation,
    bind_correspondence,
    bind_declared_relation,
    broadcast_mapping,
    comparison_axes,
)
from marivo.analysis.core.data_types import DataColumn, DataType, DecimalDataType, ScalarDataType
from marivo.analysis.dsl.correspondence import corresponding, ordinal, period_offset, previous_year
from marivo.analysis.dsl.temporal import exact_scope
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_exchange import normalize_stage_frame
from marivo.analysis.materialization.graph_reading import ExportRead, OutputRead, ReadLayout
from marivo.analysis.methods.query_comparison import align_frames, broadcast_series, compare_frames
from marivo.refs import ref
from marivo.semantic.reader import SemanticProject

STRING = ScalarDataType("string")
INTEGER = ScalarDataType("int64")
ENTITY = ExactRef(ref.entity("sales.accounts"), "v1")
MEMBER = AxisBinding(ENTITY, None, (), "identity")
FIELD = AxisBinding(ENTITY, ExactRef(ref.dimension("sales.accounts.region"), "v1"), (), "field")


def _calendar(start: str, end: str, grain: str = "day", zone: str = "UTC") -> CalendarCoordinate:
    grid = exact_scope(start, end, zone).by(builtin_grain(grain)).grid
    return CalendarCoordinate(
        grid.identity,
        grid.grain_spec,
        grid.boundary_timezone,
        grid.snapshot_digest,
        tuple(
            (
                anchor_from_datetime(cell.original_start),
                anchor_from_datetime(cell.original_end),
                anchor_from_datetime(cell.start),
                anchor_from_datetime(cell.end),
            )
            for cell in grid.cells
        ),
    )


def _endpoint(
    axes: tuple[ComparisonAxis, ...] = (),
    widths: tuple[int, ...] | None = None,
    dtype: DataType = INTEGER,
) -> ComparisonEndpoint:
    groups: list[tuple[str, ...]] = []
    columns: list[DataColumn] = []
    for axis, width in zip(axes, widths or (1,) * len(axes), strict=True):
        group = tuple(f"key.{len(columns) + i}" for i in range(width))
        groups.append(group)
        columns.extend(
            DataColumn(
                key,
                key,
                INTEGER if isinstance(axis, CalendarCoordinate) else STRING,
                isinstance(axis, AxisBinding) and axis.role == "field",
            )
            for key in group
        )
    keys = tuple(key for group in groups for key in group)
    return ComparisonEndpoint(
        (*columns, DataColumn("value.0", "m", dtype, True, "CNY")), keys, axes, tuple(groups)
    )


def _frame(endpoint: ComparisonEndpoint, *rows: tuple[object, ...]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=[column.slot_id for column in endpoint.columns], dtype=object)


def test_identity_outer_keeps_na_presence_and_complete_composite_keys() -> None:
    endpoint = _endpoint((MEMBER,), (2,))
    left = _frame(endpoint, ("t1", "A", None), ("t2", "A", 0))
    right = _frame(endpoint, ("t1", "A", 7), ("t3", "A", 9))
    with pytest.raises(AnalysisError, match="equal complete endpoint key images"):
        align_frames(bind_correspondence(endpoint, endpoint), left, right)
    binding = bind_correspondence(endpoint, endpoint, how="outer")
    result = align_frames(binding, left, right)
    assert result.axes == (MEMBER,)
    assert result.frame["presence.left"].tolist() == [True, True, False]
    assert result.frame["presence.right"].tolist() == [True, False, True]
    assert result.frame["value.left.0"].isna().tolist() == [True, False, True]
    assert result.frame["projection.left.0.0"].tolist() == ["t1", "t2", None]
    with pytest.raises(AnalysisError, match="unique complete endpoint keys"):
        align_frames(binding, pd.concat((left, left)), right)


def test_compare_signed_baseline_null_zero_policy_and_precision() -> None:
    endpoint = _endpoint((FIELD,))
    binding = bind_correspondence(endpoint, endpoint, operation="compare")
    left = _frame(endpoint, ("A", 15), ("B", 10), ("C", None), ("D", 9007199254740993))
    right = _frame(endpoint, ("A", -10), ("B", 0), ("C", 0), ("D", 9007199254740992))
    result = compare_frames(binding, left, right)
    assert result.frame["value.0.absolute"].tolist() == [25, 10, None, 1]
    assert result.frame["value.0.relative"].iloc[0] == 2.5
    assert result.frame["value.0.relative"].iloc[1:3].isna().all()
    assert result.frame["value.0.current"].iloc[3] == 9007199254740993
    with pytest.raises(AnalysisError, match="nonzero denominator"):
        compare_frames(binding, left, right, zero_denominator="error")
    # A missing current value does not demand division by an otherwise zero baseline.
    compare_frames(binding, left.iloc[2:3], right.iloc[2:3], zero_denominator="error")
    normalize_stage_frame(result.frame, result.columns, result.keys)


def test_compare_schema_is_known_before_values_and_checks_quantity_contracts() -> None:
    endpoint = _endpoint(dtype=DecimalDataType(12, 2))
    binding = bind_correspondence(endpoint, endpoint, operation="compare")
    assert [column.name for column in binding.layout.values] == [
        "m.current",
        "m.baseline",
        "m.absolute",
        "m.relative",
    ]
    result = compare_frames(
        binding, _frame(endpoint, (Decimal("10.15"),)), _frame(endpoint, (Decimal("4.10"),))
    )
    assert result.frame["value.0.absolute"].tolist() == [Decimal("6.05")]
    normalize_stage_frame(result.frame, result.columns, result.keys)
    with pytest.raises(AnalysisError, match="equal units"):
        bind_correspondence(
            endpoint,
            replace(endpoint, columns=(replace(endpoint.columns[0], unit="USD"),)),
            operation="compare",
        )
    with pytest.raises(AnalysisError, match="numeric comparable"):
        bind_correspondence(_endpoint(dtype=STRING), _endpoint(dtype=STRING), operation="compare")


def test_nullable_float_endpoints_keep_their_declared_carrier() -> None:
    endpoint = _endpoint((FIELD,), dtype=ScalarDataType("float64"))
    binding = bind_correspondence(endpoint, endpoint, operation="compare", how="outer")
    result = compare_frames(
        binding, _frame(endpoint, ("A", None), ("B", 1.5)), _frame(endpoint, ("A", 0.0), ("C", 1.0))
    )
    assert result.frame["value.0.current"].isna().tolist() == [True, False, True]
    assert result.frame["value.0.absolute"].isna().all()
    normalize_stage_frame(result.frame, result.columns, result.keys)


def test_field_null_key_is_a_real_shared_coordinate() -> None:
    endpoint = _endpoint((FIELD,))
    result = align_frames(
        bind_correspondence(endpoint, endpoint),
        _frame(endpoint, (None, 10)),
        _frame(endpoint, (pd.NA, 20)),
    )
    assert result.frame["presence.left"].tolist() == [True]
    assert result.frame["presence.right"].tolist() == [True]


def test_ordinal_uses_frozen_positions_after_filtering() -> None:
    a, b = _calendar("2026-10-01", "2026-10-04"), _calendar("2026-09-01", "2026-09-04")
    left, right = _endpoint((a,)), _endpoint((b,))
    binding = bind_correspondence(left, right, match=ordinal(), how="outer")
    assert comparison_axes(left.axes, right.axes, match=ordinal()) == binding.layout.axes
    result = align_frames(binding, _frame(left, (1, 20)), _frame(right, (0, 100), (1, 200)))
    assert len(result.frame) == 2
    assert result.frame["value.left.0"].tolist() == [None, 20]
    assert result.frame["value.right.0"].tolist() == [100, 200]
    assert isinstance(result.axes[0], CorrespondenceAxis)
    assert result.axes[0].projections == {("left",): a, ("right",): b}
    normalize_stage_frame(result.frame, result.columns, result.keys)


def test_ordinal_tail_outer_and_grain_timezone_partial_rejection() -> None:
    a, b = _calendar("2026-10-01", "2026-10-03"), _calendar("2026-09-01", "2026-09-04")
    left, right = _endpoint((a,)), _endpoint((b,))
    with pytest.raises(AnalysisError, match="equal complete calendar bucket counts"):
        bind_correspondence(left, right, match=ordinal())
    result = align_frames(
        bind_correspondence(left, right, match=ordinal(), how="outer"),
        _frame(left, (0, 1), (1, 2)),
        _frame(right, (0, 3), (1, 4), (2, 5)),
    )
    assert result.frame["presence.left"].tolist() == [True, True, False]
    for bad in (
        _calendar("2026-09-01", "2026-11-01", "month"),
        _calendar("2026-09-01", "2026-09-03", zone="Asia/Shanghai"),
        _calendar("2026-09-01T12:00", "2026-09-03"),
    ):
        with pytest.raises(AnalysisError):
            bind_correspondence(left, _endpoint((bad,)), match=ordinal(), how="outer")


def test_leap_day_exact_outer_and_current_projection() -> None:
    left = _endpoint((_calendar("2024-02-28", "2024-03-02"),))
    right = _endpoint((_calendar("2023-02-28", "2023-03-02"),))
    with pytest.raises(AnalysisError, match="complete exact period correspondence"):
        bind_correspondence(left, right, match=previous_year(), operation="compare")
    binding = bind_correspondence(
        left, right, match=previous_year(), operation="compare", how="outer", time_axis="current"
    )
    result = compare_frames(
        binding, _frame(left, (0, 10), (1, 20), (2, 30)), _frame(right, (0, 5), (1, 10))
    )
    assert result.frame["presence.baseline"].tolist() == [True, False, True]
    assert result.frame["value.0.absolute"].tolist() == [5, None, 20]
    assert result.axes == left.axes
    reverse_left = _endpoint((_calendar("2025-02-28", "2025-03-02"),))
    reverse_right = _endpoint((_calendar("2024-02-28", "2024-03-02"),))
    with pytest.raises(AnalysisError, match="current date"):
        reverse = bind_correspondence(
            reverse_left,
            reverse_right,
            match=previous_year(),
            operation="compare",
            how="outer",
            time_axis="current",
        )
        compare_frames(
            reverse,
            _frame(reverse_left, (0, 10), (1, 20)),
            _frame(reverse_right, (0, 3), (1, 4), (2, 5)),
        )


def test_complete_february_and_calendar_period_offset() -> None:
    left = _endpoint((_calendar("2024-02-01", "2024-03-01", "month"),))
    right = _endpoint((_calendar("2023-02-01", "2023-03-01", "month"),))
    assert bind_correspondence(left, right, match=previous_year()).calendar_pairs == ((0, 0),)
    a, b = (
        _endpoint((_calendar("2026-03-01", "2026-03-03"),)),
        _endpoint((_calendar("2026-02-28", "2026-03-02"),)),
    )
    assert bind_correspondence(a, b, match=period_offset(-1)).calendar_pairs == ((0, 0), (1, 1))
    with pytest.raises(AnalysisError):
        period_offset(True)
    week = _endpoint((_calendar("2026-01-05", "2026-01-12", "week"),))
    with pytest.raises(AnalysisError, match="builtin day/month/quarter/year"):
        bind_correspondence(week, week, match=previous_year())


def test_period_offset_counts_dst_folds_in_the_shared_calendar_order() -> None:
    left = _endpoint(
        (
            _calendar(
                "2026-11-01T01:00:00-05:00", "2026-11-01T03:00:00-05:00", "hour", "America/New_York"
            ),
        )
    )
    right = _endpoint(
        (
            _calendar(
                "2026-11-01T01:00:00-04:00", "2026-11-01T02:00:00-05:00", "hour", "America/New_York"
            ),
        )
    )
    assert bind_correspondence(left, right, match=period_offset(-1)).calendar_pairs == (
        (0, 0),
        (1, 1),
    )


def test_nested_presence_and_direct_broadcast_axis_stay_authoritative() -> None:
    endpoint = _endpoint((FIELD,))
    first = align_frames(
        bind_correspondence(endpoint, endpoint, how="outer"),
        _frame(endpoint, ("A", 10)),
        _frame(endpoint, ("B", 20)),
    )
    nested = align_frames(bind_correspondence(first.layout, first.layout), first.frame, first.frame)
    assert nested.frame["presence.left"].tolist() == [True, True]
    assert nested.frame["presence.left.0"].tolist() == [True, False]
    assert nested.frame["presence.left.1"].tolist() == [False, True]
    assert broadcast_mapping(endpoint, first.layout).keys == (("key.0", "key.0"),)
    reference = _frame(endpoint, ("A", 100), ("B", 200))
    assert broadcast_series(
        endpoint, reference, first.layout, first.frame, value_slot="value.0"
    ).tolist() == [100, 200]


def test_current_axis_checks_actual_rows_after_endpoint_filtering() -> None:
    left = _endpoint((_calendar("2025-02-28", "2025-03-02"),))
    right = _endpoint((_calendar("2024-02-28", "2024-03-02"),))
    binding = bind_correspondence(
        left, right, match=previous_year(), operation="compare", how="outer", time_axis="current"
    )
    result = compare_frames(binding, _frame(left, (0, 10), (1, 20)), _frame(right, (0, 3), (2, 5)))
    assert result.frame["value.0.absolute"].tolist() == [7, 15]


def test_semantic_period_offset_requires_exact_certification_and_coverage() -> None:
    start, end = date(2026, 1, 1), date(2026, 1, 7)
    snapshot = certify_period_calendar(
        calendar_ref=ref.period_calendar("sales.fiscal"),
        boundary_timezone="UTC",
        coverage=(start, end),
        rows=({"date": start + timedelta(days=i), "period": i // 2} for i in range(6)),
        levels={"period": "period"},
    )

    def axis(index: int) -> CalendarCoordinate:
        cell = _calendar(f"2026-01-0{index}", f"2026-01-0{index + 2}").cells
        return CalendarCoordinate(
            str(index),
            semantic_grain(calendar=ref.period_calendar("sales.fiscal"), level="period"),
            "UTC",
            snapshot.snapshot_digest,
            ((cell[0][0], cell[-1][1], cell[0][0], cell[-1][1]),),
        )

    left, right = _endpoint((axis(3),)), _endpoint((axis(1),))
    with pytest.raises(AnalysisError, match="complete frozen certified calendar"):
        bind_correspondence(left, right, match=period_offset(-1))
    assert bind_correspondence(
        left, right, match=period_offset(-1), calendar_snapshot=snapshot
    ).calendar_pairs == ((0, 0),)
    with pytest.raises(AnalysisError, match="certified coverage"):
        bind_correspondence(right, left, match=period_offset(-1), calendar_snapshot=snapshot)


def test_broadcast_complete_projection_scalar_and_covering_reference() -> None:
    reference, target = _endpoint((FIELD,)), _endpoint((FIELD, MEMBER), (1, 2))
    values = _frame(reference, ("A", 10), ("B", 20))
    rows = _frame(target, ("B", "t1", "C1", 0), ("A", "t2", "C1", 0))
    assert broadcast_mapping(reference, target).keys == (("key.0", "key.0"),)
    assert broadcast_series(reference, values, target, rows, value_slot="value.0").tolist() == [
        20,
        10,
    ]
    with pytest.raises(AnalysisError, match="reference coverage"):
        broadcast_series(reference, values.iloc[:1], target, rows, value_slot="value.0")
    with pytest.raises(AnalysisError, match="unique complete endpoint keys"):
        broadcast_series(reference, pd.concat((values, values)), target, rows, value_slot="value.0")
    scalar = _endpoint()
    assert broadcast_series(
        scalar, _frame(scalar, (7,)), target, rows, value_slot="value.0"
    ).tolist() == [7, 7]
    with pytest.raises(AnalysisError, match="exactly one zero-axis"):
        broadcast_series(scalar, _frame(scalar), target, rows, value_slot="value.0")
    with pytest.raises(AnalysisError, match="complete target-to-reference"):
        broadcast_mapping(reference, _endpoint((MEMBER,), (2,)))


def test_declared_full_identity_mapping_and_explicit_dependency() -> None:
    other = AxisBinding(ExactRef(ref.entity("billing.accounts"), "v1"), None, (), "identity")
    step = RelationshipParameter(
        ExactRef(ref.relationship("sales.account_match"), "v1"), ENTITY, other.entity, "one_to_one"
    )
    match = corresponding(via=(step,))
    left, right = _endpoint((MEMBER,), (2,)), _endpoint((other,), (2,))
    relation = CorrespondenceRelation(
        (step,), MEMBER, other, ("tenant", "id"), ("id", "tenant"), ((0, 1), (1, 0))
    )
    binding = bind_correspondence(left, right, match=match, relations=(relation,))
    assert comparison_axes(left.axes, right.axes, match=match) == binding.layout.axes
    assert binding.relation_requirement is None
    assert align_frames(
        binding, _frame(left, ("t1", "A", 10)), _frame(right, ("A", "t1", 20))
    ).frame["value.right.0"].tolist() == [20]
    dependent = bind_correspondence(
        left, right, match=match, relations=(replace(relation, direct=()),)
    )
    assert dependent.layout.axes == binding.layout.axes
    assert dependent.relation_requirement is not None
    mapping = pd.DataFrame(
        [["t1", "A", "B", "t2"]],
        columns=[column.slot_id for column in dependent.relation_requirement.columns],
        dtype=object,
    )
    result = align_frames(
        dependent,
        _frame(left, ("t1", "A", 10)),
        _frame(right, ("B", "t2", 20)),
        relation_frame=mapping,
    )
    assert result.frame["value.right.0"].tolist() == [20]
    with pytest.raises(AnalysisError, match="unique nonnull one-to-one"):
        align_frames(
            dependent,
            _frame(left, ("t1", "A", 10)),
            _frame(right, ("B", "t2", 20)),
            relation_frame=pd.concat((mapping, mapping)),
        )
    with pytest.raises(AnalysisError):
        bind_correspondence(left, right)


def test_real_duckdb_observation_frames_and_loaded_composite_relation(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "comparison.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE crm(tenant VARCHAR, id VARCHAR, amount BIGINT)")
        connection.execute("CREATE TABLE billing(tenant VARCHAR, id VARCHAR, amount BIGINT)")
        connection.execute("INSERT INTO crm VALUES ('t1','A',150),('t2','A',0)")
        connection.execute("INSERT INTO billing VALUES ('t1','A',100),('t2','A',100)")
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/models.py": """import marivo.datasource as md
import marivo.semantic as ms
crm = ms.entity(name='crm', datasource=ms.ref.datasource('warehouse'), source=md.table('crm'), primary_key=['tenant','id'])
billing = ms.entity(name='billing', datasource=ms.ref.datasource('warehouse'), source=md.table('billing'), primary_key=['tenant','id'])
crm_tenant = ms.dimension_column(name='tenant', entity=crm, column='tenant')
crm_id = ms.dimension_column(name='id', entity=crm, column='id')
billing_tenant = ms.dimension_column(name='tenant', entity=billing, column='tenant')
billing_id = ms.dimension_column(name='id', entity=billing, column='id')
crm_amount = ms.measure_column(name='amount', entity=crm, column='amount', additivity=ms.additive_all(), unit='CNY')
billing_amount = ms.measure_column(name='amount', entity=billing, column='amount', additivity=ms.additive_all(), unit='CNY')
account_match = ms.relationship(name='account_match', from_entity=crm, to_entity=billing, keys=[ms.join_on(crm_tenant,billing_tenant),ms.join_on(crm_id,billing_id)])
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("kernel-comparison", report_timezone="UTC")
    domains = (
        session.members(ref.entity("sales.crm")),
        session.members(ref.entity("sales.billing")),
    )
    axes = tuple(domain.axes[0] for domain in domains)
    assert isinstance(axes[0], AxisBinding) and isinstance(axes[1], AxisBinding)
    artifacts = tuple(
        session.observe(
            {"m": session.catalog.fields.get(f"sales.{name}.amount").sum()}, by=(axis,)
        ).execute()
        for name, axis in zip(("crm", "billing"), axes, strict=True)
    )
    frames = tuple(
        OutputRead(session.project_root).read(
            ReadLayout(artifact._manifest.data.schema, artifact._manifest.data.rows), ExportRead()
        )
        for artifact in artifacts
    )
    endpoints = tuple(
        ComparisonEndpoint(
            artifact._manifest.data.schema.columns,
            artifact._manifest.data.schema.key_fields,
            (axis,),
            (artifact._manifest.data.schema.key_fields,),
        )
        for artifact, axis in zip(artifacts, axes, strict=True)
    )
    step = session.catalog.relationships.get("sales.account_match")
    assert isinstance(step, RelationshipParameter)
    match = corresponding(via=(step,))
    relation = bind_declared_relation(axes[0], axes[1], match, session.catalog._state.registry)
    result = compare_frames(
        bind_correspondence(
            endpoints[0], endpoints[1], match=match, operation="compare", relations=(relation,)
        ),
        frames[0],
        frames[1],
    )
    assert result.frame["value.0.absolute"].tolist() == [50, -100]
    assert result.frame["value.0.relative"].tolist() == [0.5, -1.0]
