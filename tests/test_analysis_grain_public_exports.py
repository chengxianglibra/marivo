import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.windows.spec import normalize_grain
from marivo.refs import ref


def test_public_grain_is_distinct_from_legacy_window_normalization_surface():
    import marivo.analysis as ma
    from marivo.analysis.windows.grain import Grain
    from marivo.analysis.windows.spec import GrainInput

    assert "Grain" in ma.__all__
    assert "grain" in ma.__all__
    assert "GrainInput" not in ma.__all__
    assert hasattr(ma, "Grain")
    assert not hasattr(ma, "GrainInput")
    assert ma.grain("month").kind == "builtin"
    with pytest.raises(TypeError, match="direct construction is not supported"):
        ma.Grain(unit="day")
    g = Grain(count=5, unit="minute")
    assert g.to_token() == "5minute"
    assert GrainInput is not None


def test_public_grain_constructors_share_one_value_type():
    calendar = ref.period_calendar("sales.fiscal")
    builtin = mv.grain("minute", count=5)
    semantic = ms.calendar_grain(calendar=calendar, level="week")

    assert isinstance(builtin, mv.Grain)
    assert isinstance(semantic, mv.Grain)
    assert type(builtin) is not type(semantic)
    assert builtin.kind == "builtin"
    assert semantic.kind == "semantic"
    assert normalize_grain(builtin).to_token() == "5minute"
    assert normalize_grain(semantic) is semantic
