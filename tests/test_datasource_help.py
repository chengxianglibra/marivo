"""md.help() introspection."""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

import marivo.datasource as md


def _capture(symbol: str | None = None) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        md.help(symbol)
    return buf.getvalue()


def _capture_json(symbol: str | None = None) -> tuple[dict[str, object], str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = md.help(symbol, format="json")
    assert isinstance(result, dict)
    return result, buf.getvalue()


# --- text mode tests ---


def test_md_help_text_top_level_lists_entries() -> None:
    out = _capture()
    assert "datasource" in out
    assert "load_datasources" in out
    assert "AiContext" in out
    assert "DatasourceIR" in out


def test_md_help_text_for_datasource_shows_signature() -> None:
    out = _capture("datasource")
    assert "datasource(" in out


def test_md_help_text_for_load_datasources() -> None:
    out = _capture("load_datasources")
    assert "load_datasources(" in out


def test_md_help_text_for_class() -> None:
    out = _capture("DatasourceIR")
    assert "class DatasourceIR" in out


def test_md_help_text_for_module() -> None:
    out = _capture("errors")
    assert "module errors" in out


def test_md_help_text_for_validations() -> None:
    out = _capture("validations")
    assert "datasource_name_format" in out
    assert "datasource_sensitive_env_only" in out


def test_md_help_text_unknown_symbol() -> None:
    out = _capture("nonexistent_xyz")
    assert "unknown symbol" in out.lower()


def test_md_help_text_returns_none() -> None:
    assert md.help() is None


# --- json mode tests ---


def test_md_help_json_top_level_returns_dict() -> None:
    result, stdout = _capture_json()
    assert stdout == ""
    assert result["schema_version"] == "1"
    assert result["surface"] == "marivo.datasource"
    assert isinstance(result["entries"], list)
    assert len(result["entries"]) > 0
    assert isinstance(result["validations"], list)
    assert len(result["validations"]) > 0


def test_md_help_json_datasource_symbol() -> None:
    result, stdout = _capture_json("datasource")
    assert stdout == ""
    assert result["kind"] == "callable"
    assert "datasource" in result["signature"]
    assert isinstance(result["doc"], str)
    assert isinstance(result["validations"], list)


def test_md_help_json_validations_symbol() -> None:
    result, stdout = _capture_json("validations")
    assert stdout == ""
    assert isinstance(result["validations"], list)
    assert len(result["validations"]) > 0
    first = result["validations"][0]
    assert "id" in first
    assert "applies_to" in first
    assert "title" in first
    assert "hint" in first


def test_md_help_json_class_symbol() -> None:
    result, stdout = _capture_json("DatasourceIR")
    assert stdout == ""
    assert result["kind"] == "class"


def test_md_help_json_unknown_symbol() -> None:
    result, stdout = _capture_json("nonexistent_xyz")
    assert stdout == ""
    assert "error" in result


def test_md_help_json_does_not_print(capsys) -> None:
    md.help(format="json")
    assert capsys.readouterr().out == ""


def test_md_help_invalid_format_raises() -> None:
    with pytest.raises(ValueError, match="format must be"):
        md.help(format="yaml")


def test_md_help_text_not_public() -> None:
    assert not hasattr(md, "help_text")
    assert not hasattr(md, "_help_text")


def test_md_help_in_all() -> None:
    assert "help" in md.__all__


def test_md_help_callable() -> None:
    assert callable(md.help)
