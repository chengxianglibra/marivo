"""Current complete-key Frame correspondence and independent numerical oracles."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition.types import CalendarCoordinate
from marivo._temporal import anchor_from_datetime, builtin_grain
from marivo.analysis.core.artifact_data import DataAxis, DataColumn, DataSchema, OutputAttachment
from marivo.analysis.core.data_schema import PairedDataAxis
from marivo.analysis.core.data_types import DataType, DecimalDataType, ScalarDataType
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_calendar import freeze_calendar
from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
from marivo.analysis.dsl.temporal import exact_scope
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.semantic.reader import SemanticProject

STRING = ScalarDataType("string")
INTEGER = ScalarDataType("int64")
FIELD = DataAxis("region", (DataColumn("key.0", "region", STRING, True),), name="region")
MEMBER = DataAxis(
    "accounts",
    (DataColumn("key.0", "tenant", STRING, False), DataColumn("key.1", "id", STRING, False)),
    kind="member",
    name="accounts",
)


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "complete-key-oracles", timezone="UTC").local


def _memory(
    local: LocalFrameServices,
    axes: tuple[DataAxis, ...],
    rows: tuple[tuple[object, ...], ...],
    *,
    dtype: DataType = INTEGER,
    unit: str = "CNY",
    attachments: tuple[OutputAttachment, ...] = (),
) -> mv.AnalysisFrame:
    schema = DataSchema(
        axes, (DataColumn("value.0", "m", dtype, True, unit),), value_paths=(("m",),)
    )
    data = CompletedFrameData(
        schema,
        pd.DataFrame(rows, columns=[column.slot_id for column in schema.columns], dtype=object),
        attachments,
    )
    metadata = FrameResultMetadata(
        "oracle",
        FrameAudit((), EvaluationRecord(datetime(2026, 1, 1, tzinfo=timezone.utc), (), ())),
    )
    return mv.AnalysisFrame._from_memory(local, CompletedFrameResult(data, metadata))


def _on(
    left: mv.AnalysisFrame, right: mv.AnalysisFrame
) -> tuple[tuple[mv.FrameAxis, mv.FrameAxis], ...]:
    return tuple(zip(left.axes, right.axes, strict=True))


def _calendar(
    local: LocalFrameServices,
    start: str,
    end: str,
    rows: tuple[tuple[object, ...], ...],
    *,
    grain: str = "day",
    zone: str = "UTC",
) -> mv.AnalysisFrame:
    grid = exact_scope(start, end, zone).by(builtin_grain(grain)).grid
    coordinate = CalendarCoordinate(
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
        civil_bounds=grid.civil_bounds,
    )
    frozen = freeze_calendar(
        coordinate,
        token="calendar",
        name="period",
        key=DataColumn("key.0", "bucket", INTEGER, False),
    )
    return _memory(local, (frozen.axis,), rows, attachments=(frozen.directory,))


def test_identity_outer_keeps_na_presence_and_complete_composite_keys(
    local: LocalFrameServices,
) -> None:
    left = _memory(local, (MEMBER,), (("t1", "A", None), ("t2", "A", 0)))
    right = _memory(local, (MEMBER,), (("t1", "A", 7), ("t3", "A", 9)))
    with pytest.raises(AnalysisError):
        left.align(right, on=_on(left, right)).compute()
    result = left.align(right, on=_on(left, right), how="outer").compute().to_pandas()
    assert result.iloc[:, 0].tolist() == [("t1", "A"), ("t2", "A"), ("t3", "A")]
    assert result[("presence", "left_present")].tolist() == [True, True, False]
    assert result[("presence", "right_present")].tolist() == [True, False, True]
    assert result[("value", "left.m")].isna().tolist() == [True, False, True]
    with pytest.raises(AnalysisError):
        _memory(local, (MEMBER,), (("t1", "A", 1), ("t1", "A", 2)))


def test_compare_signed_baseline_null_zero_policy_and_precision(local: LocalFrameServices) -> None:
    left = _memory(local, (FIELD,), (("A", 15), ("B", 10), ("C", None), ("D", 9007199254740993)))
    right = _memory(local, (FIELD,), (("A", -10), ("B", 0), ("C", 0), ("D", 9007199254740992)))
    result = left.compare({"base": right}, on={"base": _on(left, right)}).compute().to_pandas()
    assert result[("value", "base.absolute.m")].tolist() == [25, 10, pd.NA, 1]
    assert result[("value", "base.relative.m")].iloc[0] == 2.5
    assert result[("value", "base.relative.m")].iloc[1:3].isna().all()
    assert result[("value", "m")].iloc[3] == 9007199254740993
    with pytest.raises(AnalysisError, match="nonzero denominator"):
        left.compare(
            {"base": right}, on={"base": _on(left, right)}, zero_denominator="error"
        ).compute()
    missing = left.where(left["m"].is_null())
    zero = right.where(right["m"] == 0)
    zero = zero.where(zero.coord(zero.axes[0]) == "C")
    missing.compare(
        {"base": zero}, on={"base": _on(missing, zero)}, zero_denominator="error"
    ).compute()


def test_compare_schema_is_known_before_values_and_checks_quantity_contracts(
    local: LocalFrameServices,
) -> None:
    left = _memory(local, (), ((Decimal("10.15"),),), dtype=DecimalDataType(12, 2))
    right = _memory(local, (), ((Decimal("4.10"),),), dtype=DecimalDataType(12, 2))
    comparison = left.compare({"base": right})
    assert comparison.output_names == ("m", "base.baseline.m", "base.absolute.m", "base.relative.m")
    assert comparison.compute().to_pandas()[("value", "base.absolute.m")].tolist() == [
        Decimal("6.05")
    ]
    with pytest.raises(AnalysisError):
        left.compare(
            {
                "base": _memory(
                    local, (), ((Decimal("4.10"),),), dtype=DecimalDataType(12, 2), unit="USD"
                )
            }
        )
    text = _memory(local, (), (("text",),), dtype=STRING)
    with pytest.raises(AnalysisError):
        text.compare({"base": text})


def test_nullable_float_endpoints_keep_their_declared_carrier(local: LocalFrameServices) -> None:
    left = _memory(local, (FIELD,), (("A", None), ("B", 1.5)), dtype=ScalarDataType("float64"))
    right = _memory(local, (FIELD,), (("A", 0.0), ("C", 1.0)), dtype=ScalarDataType("float64"))
    result = (
        left.compare({"base": right}, on={"base": _on(left, right)}, how="outer")
        .compute()
        .to_pandas()
    )
    assert result[("value", "m")].isna().tolist() == [True, False, True]
    assert result[("value", "base.absolute.m")].isna().all()
    assert pd.api.types.is_float_dtype(result[("value", "m")].dtype)


def test_field_null_key_is_a_real_shared_coordinate(local: LocalFrameServices) -> None:
    left, right = _memory(local, (FIELD,), ((None, 10),)), _memory(local, (FIELD,), ((pd.NA, 20),))
    result = left.align(right, on=_on(left, right)).compute().to_pandas()
    assert result[("presence", "left_present")].tolist() == [True]
    assert result[("presence", "right_present")].tolist() == [True]


def test_ordinal_uses_frozen_positions_after_filtering(local: LocalFrameServices) -> None:
    left = _calendar(local, "2026-10-01", "2026-10-04", ((1, 20),))
    right = _calendar(local, "2026-09-01", "2026-09-04", ((0, 100), (1, 200)))
    result = left.align(right, match=mv.ordinal(), how="outer").compute().to_pandas()
    assert len(result) == 2
    assert result[("value", "left.m")].isna().tolist() == [False, True]
    assert result[("value", "right.m")].tolist() == [200, 100]


def test_ordinal_tail_outer_and_grain_timezone_partial_rejection(local: LocalFrameServices) -> None:
    left = _calendar(local, "2026-10-01", "2026-10-03", ((0, 1), (1, 2)))
    right = _calendar(local, "2026-09-01", "2026-09-04", ((0, 3), (1, 4), (2, 5)))
    with pytest.raises(AnalysisError):
        left.align(right, match=mv.ordinal()).compute()
    result = left.align(right, match=mv.ordinal(), how="outer").compute().to_pandas()
    assert result[("presence", "left_present")].tolist() == [True, True, False]
    for start, end, grain, zone in (
        ("2026-09-01", "2026-11-01", "month", "UTC"),
        ("2026-09-01", "2026-09-03", "day", "Asia/Shanghai"),
        ("2026-09-01T12:00", "2026-09-03", "day", "UTC"),
    ):
        bad = _calendar(local, start, end, ((0, 3),), grain=grain, zone=zone)
        with pytest.raises(AnalysisError):
            left.align(bad, match=mv.ordinal(), how="outer").compute()


def test_leap_day_exact_outer_keeps_presence_and_values(local: LocalFrameServices) -> None:
    left = _calendar(local, "2024-02-28", "2024-03-02", ((0, 10), (1, 20), (2, 30)))
    right = _calendar(local, "2023-02-28", "2023-03-02", ((0, 5), (1, 10)))
    with pytest.raises(AnalysisError):
        left.compare({"base": right}, match={"base": mv.offset(years=-1)}).compute()
    result = (
        left.compare({"base": right}, match={"base": mv.offset(years=-1)}, how="outer")
        .compute()
        .to_pandas()
    )
    assert result[("presence", "base.present")].tolist() == [True, False, True]
    assert result[("value", "base.absolute.m")].tolist() == [5, pd.NA, 20]


def test_filtered_leap_endpoint_uses_actual_complete_rows(local: LocalFrameServices) -> None:
    left = _calendar(local, "2025-02-28", "2025-03-02", ((0, 10), (1, 20)))
    right = _calendar(local, "2024-02-28", "2024-03-02", ((0, 3), (2, 5)))
    result = (
        left.compare({"base": right}, match={"base": mv.offset(years=-1)}, how="outer")
        .compute()
        .to_pandas()
    )
    assert result[("value", "base.absolute.m")].tolist() == [7, 15]
    assert result[("presence", "current.present")].tolist() == [True, True]
    assert result[("presence", "base.present")].tolist() == [True, True]


def test_complete_february_and_calendar_period_offset(local: LocalFrameServices) -> None:
    left = _calendar(local, "2024-02-01", "2024-03-01", ((0, 10),), grain="month")
    right = _calendar(local, "2023-02-01", "2023-03-01", ((0, 5),), grain="month")
    assert left.compare({"base": right}, match={"base": mv.offset(years=-1)}).compute().to_pandas()[
        ("value", "base.absolute.m")
    ].tolist() == [5]
    a = _calendar(local, "2026-03-01", "2026-03-03", ((0, 1), (1, 2)))
    b = _calendar(local, "2026-02-28", "2026-03-02", ((0, 4), (1, 5)))
    assert a.align(b, match=mv.offset(periods=-1)).compute().to_pandas()[
        ("value", "right.m")
    ].tolist() == [4, 5]
    with pytest.raises(AnalysisError):
        mv.offset(periods=True)
    week = _calendar(local, "2026-01-05", "2026-01-12", ((0, 1),), grain="week")
    with pytest.raises(AnalysisError):
        week.align(week, match=mv.offset(years=-1)).compute()


def test_full_quarter_offset_and_ordinal_align_retain_paired_values(
    local: LocalFrameServices,
) -> None:
    current = _calendar(local, "2026-01-01", "2026-04-01", ((0, 10),), grain="quarter")
    baseline = _calendar(local, "2025-01-01", "2025-04-01", ((0, 3),), grain="quarter")
    for rule in (mv.offset(years=-1), mv.ordinal()):
        aligned = current.align(baseline, match=rule).compute()
        assert isinstance(aligned._schema.axes[0], PairedDataAxis)
        rows = aligned.to_pandas()
        assert rows[("value", "left.m")].tolist() == [10]
        assert rows[("value", "right.m")].tolist() == [3]


def test_nested_presence_stays_authoritative(local: LocalFrameServices) -> None:
    left = _memory(local, (FIELD,), (("A", 10),))
    right = _memory(local, (FIELD,), (("B", 20),))
    first = left.align(right, on=_on(left, right), how="outer").compute()
    nested = first.align(first, on=_on(first, first)).compute().to_pandas()
    assert nested[("presence", "left_present")].tolist() == [True, True]
    assert nested[("presence", "left.left_present")].tolist() == [True, False]
    assert nested[("presence", "left.right_present")].tolist() == [False, True]


def test_real_duckdb_observation_frames_with_explicit_complete_axis_mapping(
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
    session = mv.session.create("kernel-comparison", timezone="UTC")
    domains = (
        session.members(session.catalog.entities.get("sales.crm")),
        session.members(session.catalog.entities.get("sales.billing")),
    )
    frames = tuple(
        session.observe(
            {"m": session.catalog.fields.get(f"sales.{name}.amount").sum()}, by=domain
        ).persist()
        for name, domain in zip(("crm", "billing"), domains, strict=True)
    )
    left, right = frames
    result = left.compare({"base": right}, on={"base": _on(left, right)}).compute().to_pandas()
    assert result[("value", "base.absolute.m")].tolist() == [50, -100]
    assert result[("value", "base.relative.m")].tolist() == [0.5, -1.0]
    assert session.persisted_frame(left.artifact.id).to_pandas().iloc[:, 0].tolist() == [
        ("t1", "A"),
        ("t2", "A"),
    ]
