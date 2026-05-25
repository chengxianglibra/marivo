"""ms.help() introspection."""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import marivo.semantic_py as ms


def _capture(symbol: str | None = None) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        ms.help(symbol)
    return buf.getvalue()


def test_top_level_help_lists_decorators_and_helpers() -> None:
    out = _capture()
    assert "datasource" in out
    assert "dataset" in out
    assert "field" in out
    assert "metric" in out
    assert "relationship" in out
    assert "list_metrics" in out
    assert "describe" in out
    assert "reload" in out


def test_help_for_decorator_includes_signature() -> None:
    out = _capture("datasource")
    assert "datasource(" in out
    from marivo.semantic_py.decorators import datasource as datasource_fn

    first_doc_line = (
        (datasource_fn.__doc__ or "").strip().splitlines()[0] if datasource_fn.__doc__ else ""
    )
    if first_doc_line:
        assert first_doc_line in out


def test_help_for_exception_class_resolves_by_name() -> None:
    out = _capture("DatasourceNotRegisteredError")
    assert "DatasourceNotRegisteredError" in out
    assert "datasource" in out.lower()


def test_help_for_unknown_symbol_explains_how_to_list() -> None:
    out = _capture("nonexistent_thing_xyz")
    assert "unknown symbol" in out.lower() or "not found" in out.lower()
    assert "ms.help()" in out
