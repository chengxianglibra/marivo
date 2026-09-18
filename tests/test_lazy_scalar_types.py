"""Independent boundary checks for C2 scalar representations."""

from datetime import datetime
from decimal import Decimal

import pyarrow as pa
import pytest

from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.scalar_sql_execution import _cell
from marivo.datasource.engines.sqlite import declared_scalar_type
from marivo.datasource.inspection import _canonical_catalog_type


@pytest.mark.parametrize("value", [0, 2**53 + 1, 2**63, 2**64 - 1])
def test_unsigned_transport_never_uses_float(value: int) -> None:
    for source in (value, Decimal(value)):
        assert _cell(source, pa.uint64()) == value
        assert type(_cell(source, pa.uint64())) is int


@pytest.mark.parametrize("value", [-1, 2**64, 1.0, True, Decimal("0.5")])
def test_unsigned_invalid_cells_fail_before_arrow(value: object) -> None:
    with pytest.raises(MaterializationError):
        _cell(value, pa.uint64())


@pytest.mark.parametrize("value", [2, -1, "true", "1", 1.0])
def test_boolean_invalid_storage_is_not_truthiness(value: object) -> None:
    with pytest.raises(MaterializationError):
        _cell(value, pa.bool_())


def test_boolean_and_timestamp_exact_cells() -> None:
    assert _cell(1, pa.bool_()) is True
    assert _cell(0, pa.bool_()) is False
    assert _cell(None, pa.bool_()) is None
    assert _cell("0001-01-01 00:00:00.000001", pa.timestamp("us")) == datetime(
        1, 1, 1, microsecond=1
    )
    with pytest.raises(MaterializationError, match="precision"):
        _cell(datetime(2024, 2, 29, microsecond=1), pa.timestamp("ms"))


@pytest.mark.parametrize(
    "value",
    [
        "2024-02-29 00:00:00",
        "2024-02-30 00:00:00.000000",
        "2024-01-01T00:00:00.000000",
        "2024-01-01 00:00:00.000000Z",
    ],
)
def test_timestamp_text_rejects_noncanonical_values(value: str) -> None:
    with pytest.raises(MaterializationError):
        _cell(value, pa.timestamp("us"))


@pytest.mark.parametrize(
    "declaration,expected",
    [
        ("INT2", "int64"),
        ("tinyint", "int64"),
        ("FLOAT", "float64"),
        ("VARCHAR(20)", "string"),
        ("CHAR(3)", "string"),
        ("BOOL", "boolean"),
        ("DATETIME", "timestamp(6)"),
    ],
)
def test_sqlite_metadata_and_runtime_share_physical_mapping(
    declaration: str, expected: str
) -> None:
    assert str(declared_scalar_type(declaration)) == expected
    assert _canonical_catalog_type(declaration, backend_type="sqlite") == expected


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
    from ibis.backends.sqlite.converter import SQLitePandasData

    from marivo.datasource.engines.scalar_decode import checked_dataframe
    from marivo.datasource.errors import DatasourcePreviewError

    class Cursor:
        def fetchall(self) -> list[tuple[object]]:
            return [(value,)]

    with pytest.raises(DatasourcePreviewError, match="incompatible integer storage"):
        checked_dataframe(Cursor(), ibis.schema({"value": "int64"}), SQLitePandasData.convert_table)
