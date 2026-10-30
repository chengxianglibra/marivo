"""Source-check discovery from analysis to bounded semantic validation."""

import pytest

import marivo


def test_observe_routes_to_source_check_factories_and_validation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    marivo.help("analysis.session.observe")
    observe = capsys.readouterr().out
    assert "marivo.help('semantic.source_check')" in observe

    marivo.help("semantic.source_check")
    source_check = capsys.readouterr().out
    assert "explicit bounded scope" in source_check
    methods = (
        "not_null",
        "allowed_values",
        "unique",
        "freshness",
        "relationship_matches",
        "relationship_cardinality",
    )
    for method in methods:
        target = f"semantic.source_check.{method}"
        assert f'marivo.help("{target}")' in source_check
        marivo.help(target)
        assert f"Target: {target}" in capsys.readouterr().out

    assert 'marivo.help("semantic.validate")' in source_check
    marivo.help("semantic.validate")
    validate = capsys.readouterr().out
    assert "catalog.validate" in validate
    assert "Required with data checks" in validate
