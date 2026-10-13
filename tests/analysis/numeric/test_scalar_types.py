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


def test_scalar_dataframe_converter_failure_is_structured() -> None:
    import ibis

    from marivo.datasource.engines.scalar_decode import checked_dataframe
    from marivo.datasource.errors import DatasourcePreviewError

    class Cursor:
        def fetchall(self) -> list[tuple[int]]:
            return [(1,)]

    with pytest.raises(DatasourcePreviewError, match="non-dataframe"):
        checked_dataframe(Cursor(), ibis.schema({"value": "int64"}), lambda frame, schema: None)


@pytest.mark.parametrize("value", [1.5, 2**64, "invalid"])
def test_integer_dataframe_conversion_failure_is_structured(value: object) -> None:
    import ibis
    from ibis.formats.pandas import PandasData

    from marivo.datasource.engines.scalar_decode import checked_dataframe
    from marivo.datasource.errors import DatasourcePreviewError

    class Cursor:
        def fetchall(self) -> list[tuple[object]]:
            return [(value,)]

    with pytest.raises(DatasourcePreviewError, match="incompatible integer storage"):
        checked_dataframe(Cursor(), ibis.schema({"value": "int64"}), PandasData.convert_table)
