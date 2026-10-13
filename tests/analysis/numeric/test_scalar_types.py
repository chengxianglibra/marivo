"""Independent boundary checks for C2 scalar representations."""

import pytest

from marivo.datasource.inspection import _canonical_catalog_type


@pytest.mark.parametrize(
    "declaration,expected",
    [
        ("bigint unsigned", "uint64"),
        ("mediumint unsigned", "uint32"),
        ("tinyint(1)", "int8"),
        ("datetime(6)", "timestamp(6)"),
        ("varchar(20)", "string"),
    ],
)
def test_mysql_physical_metadata_keeps_integer_boolean_distinction(
    declaration: str, expected: str
) -> None:
    assert _canonical_catalog_type(declaration, backend_type="mysql") == expected


@pytest.mark.parametrize("engine", ["postgres", "mysql", "trino", "clickhouse"])
@pytest.mark.parametrize("logical", ["int64", "float64", "string", "boolean"])
def test_canonical_metadata_fallback_does_not_become_unknown(engine: str, logical: str) -> None:
    assert _canonical_catalog_type(logical, backend_type=engine) == logical


@pytest.mark.parametrize(
    "backend,physical",
    [
        ("postgres", "character(8)"),
        ("mysql", "char(8)"),
        ("trino", "char(8)"),
    ],
)
def test_fixed_char_metadata_keeps_physical_distinction(backend: str, physical: str) -> None:
    assert _canonical_catalog_type(physical, backend_type=backend) == physical
