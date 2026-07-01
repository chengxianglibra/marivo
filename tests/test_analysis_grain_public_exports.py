def test_grain_is_internal_to_windows_surface():
    import marivo.analysis as ma
    from marivo.analysis.windows.grain import Grain
    from marivo.analysis.windows.spec import GrainInput

    assert "Grain" not in ma.__all__
    assert "GrainInput" not in ma.__all__
    assert not hasattr(ma, "Grain")
    assert not hasattr(ma, "GrainInput")
    g = Grain(count=5, unit="minute")
    assert g.to_token() == "5minute"
    assert GrainInput is not None
