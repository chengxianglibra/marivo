"""Smoke tests that the analysis_py package and its subpackages import cleanly."""


def test_package_imports():
    import marivo.analysis_py

    assert marivo.analysis_py is not None


def test_namespace_alias_works():
    import marivo.analysis_py as mv

    assert mv.__name__ == "marivo.analysis_py"
