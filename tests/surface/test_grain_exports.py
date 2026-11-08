import pytest


def test_public_grain_value_and_factory() -> None:
    import marivo.analysis as ma

    assert "Grain" in ma.__all__
    assert "grain" in ma.__all__
    assert hasattr(ma, "Grain")
    assert ma.grain("month").unit == "month"
    with pytest.raises(TypeError):
        ma.Grain(unit="day")
    g = ma.grain("minute", count=5)
    assert g._to_token() == "5minute"


def test_unzoned_scope_cannot_be_created_or_rehydrated() -> None:
    import marivo._temporal as temporal

    assert not hasattr(temporal, "time_scope")
    assert not hasattr(temporal, "absolute_time_scope")
    assert not hasattr(temporal, "_AbsoluteTimeScope")
    bounds = {"start": "2026-08-01", "end": "2026-08-03"}
    with pytest.raises(ValueError):
        temporal._validate_time_scope_data(bounds)
    with pytest.raises(ValueError):
        temporal._time_scope_from_contract_data(
            {"schema": "time-scope/v1", "kind": "absolute", **bounds}
        )
