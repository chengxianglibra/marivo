"""Driver migration boundaries without a running MySQL service."""

from unittest.mock import MagicMock, Mock

import ibis
import pytest

from marivo.datasource.engines import _mysql_backend as mysql
from marivo.datasource.errors import DatasourceConnectionError


def test_mysql_defaults_and_explicit_options_reach_pymysql(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = MagicMock()
    connect = Mock(return_value=connection)
    monkeypatch.setattr(mysql, "Connection", connect)
    monkeypatch.setattr(mysql.getpass, "getuser", lambda: "local_reader")
    backend = mysql.MySQLBackend().connect(host="localhost", database="warehouse")
    connect.assert_called_once_with(
        host="127.0.0.1",
        user="local_reader",
        password="",
        port=3306,
        autocommit=True,
        database="warehouse",
    )
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.execute.assert_called_once_with("SET @@session.time_zone = 'UTC'")
    assert backend._marivo_timezone_name == "UTC"

    connect.reset_mock()
    mysql.MySQLBackend().connect(
        host="mysql.example",
        user="reader",
        password="password",
        port=3307,
        database="warehouse",
        autocommit=False,
        charset="utf8mb4",
        connect_timeout=3,
    )
    connect.assert_called_once_with(
        host="mysql.example",
        user="reader",
        password="password",
        port=3307,
        database="warehouse",
        autocommit=False,
        charset="utf8mb4",
        connect_timeout=3,
    )


@pytest.mark.parametrize(
    "options", [{"ssl_mode": "VERIFY_IDENTITY"}, {"compress": True}, {"auth_plugin": "native"}]
)
def test_retired_driver_options_fail_before_network(
    options: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    network = Mock(side_effect=AssertionError("invalid options must not connect"))
    monkeypatch.setattr(mysql.Connection, "connect", network)
    with pytest.raises(DatasourceConnectionError) as caught:
        mysql.MySQLBackend().connect(host="mysql.example", **options)
    assert caught.value.expected == "JSON-safe PyMySQL connection options"
    assert caught.value.received and caught.value.repair
    network.assert_not_called()


def test_utc_initialization_failure_releases_owned_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = RuntimeError("timezone tables unavailable")
    monkeypatch.setattr(mysql, "Connection", Mock(return_value=connection))
    with pytest.raises(DatasourceConnectionError) as caught:
        mysql.MySQLBackend().connect(database="warehouse")
    assert caught.value.received == "timezone initialization failure"
    assert caught.value.repair and caught.value.expected
    connection.close.assert_called_once_with()
    connection.cursor.return_value.__exit__.assert_called_once()


def test_table_metadata_preserves_quoted_names_unsigned_and_decimal() -> None:
    backend = mysql.MySQLBackend()
    connection = MagicMock()
    backend.con = connection
    connection.get_autocommit.return_value = True
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = (
        ("large_id", "bigint unsigned", "NO", "", None, ""),
        ("amount", "decimal(20,2)", "YES", "", None, ""),
    )
    assert backend.get_schema("order.items", database="sales db") == ibis.schema(
        {"large_id": "!uint64", "amount": "decimal(20,2)"}
    )
    cursor.execute.assert_called_once_with("DESCRIBE `sales db`.`order.items`")


@pytest.mark.parametrize(
    "dtype,invalid,valid",
    [
        ("date", "2026-00-01", "2026-09-26"),
        ("date", "2026-02-31", "2026-09-26"),
        ("timestamp", "2026-00-01 01:02:03", "2026-09-26 01:02:03.123456"),
        ("timestamp", "0000-00-00 00:00:00.000000", "2026-09-26 01:02:03.123456"),
    ],
)
def test_invalid_temporal_strings_preserve_null_decoding(
    dtype: str, invalid: str, valid: str
) -> None:
    import pandas as pd

    result = mysql.convert_mysql_frame(
        pd.DataFrame({"value": [invalid, valid, None]}), ibis.schema({"value": dtype})
    )
    assert result.value.isna().tolist() == [True, False, True]
    assert result.value.iloc[1] == pd.Timestamp(valid)
