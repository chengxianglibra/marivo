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
    assert g.to_token() == "5minute"


def test_public_grain_constructors_share_one_value_type():
    calendar = ref.period_calendar("sales.fiscal")
    builtin = mv.grain("minute", count=5)
    semantic = ms.calendar_grain(calendar=calendar, level="week")

    assert isinstance(builtin, mv.Grain)
    assert isinstance(semantic, mv.Grain)
    assert type(builtin) is not type(semantic)
    assert builtin.kind == "builtin"
    assert semantic.kind == "semantic"
    assert builtin.to_token() == "5minute"
    assert semantic.calendar == calendar
    assert semantic.level == "week"
