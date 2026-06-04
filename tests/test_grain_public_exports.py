def test_grain_is_public():
    import marivo.analysis as ma
    from marivo.analysis import Grain, GrainInput

    assert "Grain" in ma.__all__
    assert "GrainInput" in ma.__all__
    g = Grain(count=5, unit="minute")
    assert g.to_token() == "5minute"
    assert GrainInput is not None
