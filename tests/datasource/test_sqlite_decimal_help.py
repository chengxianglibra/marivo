"""Disclosure for the authorized SQLite native Decimal refusal."""

import pytest

import marivo
from marivo._help import help as public_help


def test_sqlite_help_discloses_native_decimal_refusal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert marivo.help is public_help
    public_help("datasource.sqlite")
    rendered = capsys.readouterr().out
    assert "native NUMERIC cannot supply exact Decimal" in rendered
    assert "Entrypoint: md.sqlite" in rendered
    assert "type_map" in rendered
