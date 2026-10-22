import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.refs import ref


def test_public_grain_value_and_factory() -> None:
    import marivo.analysis as ma

    assert "Grain" in ma.__all__
    assert "grain" in ma.__all__
    assert hasattr(ma, "Grain")
    assert ma.grain("month").kind == "builtin"
    with pytest.raises(TypeError, match="direct construction is not supported"):
        ma.Grain(unit="day")
    g = ma.grain("minute", count=5)
    assert g._to_token() == "5minute"


def test_public_grain_constructors_share_one_value_type():
    calendar = ref.period_calendar("sales.fiscal")
    builtin = mv.grain("minute", count=5)
    semantic = ms.calendar_grain(calendar=ms.ref.period_calendar("sales.fiscal"), level="week")

    assert isinstance(builtin, mv.Grain)
    assert isinstance(semantic, mv.Grain)
    assert type(builtin) is not type(semantic)
    assert builtin.kind == "builtin"
    assert semantic.kind == "semantic"
    assert builtin._to_token() == "5minute"
    assert semantic._calendar == calendar
    assert semantic.level == "week"
    assert semantic._to_token() == "sales.fiscal::week"


def test_semantic_grain_identity_and_by_require_exact_snapshot():
    from datetime import date

    from marivo._temporal import certify_period_calendar_rows, semantic_grain
    from marivo._temporal_grid import bind_grid
    from marivo.analysis.dsl.temporal import exact_scope

    calendar = ref.period_calendar("sales.fiscal")

    def snapshot(key):
        return certify_period_calendar_rows(
            calendar_ref=calendar,
            boundary_timezone="UTC",
            coverage=(date(2026, 8, 1), date(2026, 8, 3)),
            columns=("day", "period"),
            retained_values=tuple({"day": f"2026-08-0{i}", "period": key} for i in (1, 2)),
            date_column="day",
            levels={"period": "period"},
        )

    first, second = snapshot("P1"), snapshot("P2")
    lookup = ms.calendar_grain(calendar=ms.ref.period_calendar("sales.fiscal"), level="period")
    captured = semantic_grain(calendar=calendar, level="period", snapshot=first)
    equivalent = semantic_grain(calendar=calendar, level="period", snapshot=first)
    changed = semantic_grain(calendar=calendar, level="period", snapshot=second)
    assert lookup == ms.calendar_grain(
        calendar=ms.ref.period_calendar("sales.fiscal"), level="period"
    )
    assert captured == equivalent and hash(captured) == hash(equivalent)
    assert len({lookup, captured, changed}) == 3
    assert "unbound" in repr(lookup)
    assert first.snapshot_digest in repr(captured)
    scope = exact_scope("2026-08-01", "2026-08-03", "UTC")
    with pytest.raises(ValueError, match=r"calendar\.grain"):
        scope.by(lookup)
    assert scope.by(captured).grid.snapshot_digest == first.snapshot_digest
    with pytest.raises(ValueError, match="snapshot identities"):
        bind_grid(scope, captured, report_timezone="UTC", snapshot=second)


def test_unzoned_scope_cannot_be_created_or_rehydrated():
    import marivo._temporal as temporal

    assert not hasattr(temporal, "time_scope")
    assert not hasattr(temporal, "absolute_time_scope")
    assert not hasattr(temporal, "_AbsoluteTimeScope")
    bounds = {"start": "2026-08-01", "end": "2026-08-03"}
    with pytest.raises(ValueError, match="boundary_timezone"):
        temporal._validate_time_scope_data(bounds)
    with pytest.raises(ValueError, match="boundary_timezone"):
        temporal._time_scope_from_contract_data(
            {"schema": "time-scope/v1", "kind": "absolute", **bounds}
        )
