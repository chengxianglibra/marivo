"""Private PyMySQL transport for Ibis-compiled Marivo reads.

The transaction, schema and temporal conversion behavior follows Ibis 12's
MySQL backend (Apache-2.0), without importing its mysqlclient-dependent package.
SQL compilation remains owned by Ibis.
"""

from __future__ import annotations

import getpass
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import time
from functools import cached_property

import ibis.common.exceptions as com
import ibis.expr.operations as ops
import ibis.expr.schema as sch
import ibis.expr.types as ir
import pandas as pd
import sqlglot.expressions as sge
from ibis.backends.sql import SQLBackend
from ibis.backends.sql.compilers.mysql import compiler
from ibis.formats.pandas import PandasData
from pymysql import ProgrammingError
from pymysql.connections import Connection
from pymysql.constants import ER
from pymysql.converters import convert_datetime
from pymysql.cursors import Cursor

from marivo.datasource._lifetime import BackendLease
from marivo.datasource.errors import DatasourceConnectionError, repair


def _time_value(value: pd.Timedelta) -> time:
    components = value.components
    return time(
        components.hours,
        components.minutes,
        components.seconds,
        components.milliseconds * 1000 + components.microseconds,
    )


def _temporal_value(value: object) -> object:
    if isinstance(value, str):
        # PyMySQL retains invalid dates as strings, whereas mysqlclient returns
        # NULL. Its decoder also handles partial zero dates and fractional times.
        parsed = convert_datetime(value)
        return None if isinstance(parsed, str) else parsed
    return value


def convert_mysql_frame(frame: pd.DataFrame, schema: sch.Schema) -> pd.DataFrame:
    """Preserve Ibis MySQL temporal decoding before generic pandas conversion."""
    for name, dtype in schema.items():
        column = frame[name]
        if dtype.is_time():
            frame[name] = column.map(_time_value, na_action="ignore")
        elif (dtype.is_date() or dtype.is_timestamp()) and column.dtype == object:
            frame[name] = column.map(_temporal_value)
    result: pd.DataFrame = PandasData.convert_table(frame, schema)
    return result


# Ibis SQLBackend has no typing metadata. All driver-facing methods are typed here.
class MySQLBackend(SQLBackend):  # type: ignore[misc]
    name = "mysql"
    compiler = compiler
    supports_create_or_replace = False
    con: Connection

    def do_connect(
        self,
        host: str = "localhost",
        user: str | None = None,
        password: str | None = None,
        port: int = 3306,
        autocommit: bool = True,
        **kwargs: object,
    ) -> None:
        try:
            # PyMySQL validates the authored JSON kwargs at this single driver
            # boundary; their value types depend on the actual option names.
            self.con = Connection(  # type: ignore[call-overload]
                host="127.0.0.1" if host == "localhost" else host,
                user=user or getpass.getuser(),
                password=password or "",
                port=port,
                autocommit=autocommit,
                **kwargs,
            )
        except (TypeError, NotImplementedError) as cause:
            raise DatasourceConnectionError(
                message="The MySQL connection options are not supported by PyMySQL.",
                expected="JSON-safe PyMySQL connection options",
                received=str(cause),
                repair=repair(
                    kind="configure",
                    canonical_id="mysql",
                    action=(
                        "Update datasource extra to supported PyMySQL options. "
                        "Use its ssl_* options for TLS; mysqlclient ssl_mode, "
                        "auth_plugin and protocol compression are not supported."
                    ),
                ),
            ) from cause
        self._post_connect()

    def _post_connect(self) -> None:
        try:
            with self.con.cursor() as cursor:
                # Fixed UTC does not require MySQL's optional named time zone tables.
                cursor.execute("SET @@session.time_zone = '+00:00'")
        except Exception as cause:
            BackendLease(self).close(suppress_errors=False)
            raise DatasourceConnectionError(
                message="The MySQL reader could not initialize UTC.",
                expected="successful UTC reader initialization",
                received="timezone initialization failure",
                repair=repair(
                    kind="reconnect",
                    canonical_id="datasource_catalog.test",
                    action="Restore MySQL UTC timezone support and retry the datasource connection.",
                ),
            ) from cause
        self._marivo_timezone_name = "UTC"

    @cached_property
    def version(self) -> str:
        # PyMySQL's stubs leave this native string-returning extension untyped.
        return str(self.con.get_server_info())  # type: ignore[no-untyped-call]

    def get_schema(
        self, name: str, *, catalog: str | None = None, database: str | None = None
    ) -> sch.Schema:
        table = sge.table_(name, db=database, catalog=catalog, quoted=self.compiler.quoted)
        with self.begin() as cursor:
            try:
                cursor.execute(sge.Describe(this=table).sql(self.dialect))
            except ProgrammingError as cause:
                if cause.args[0] == ER.NO_SUCH_TABLE:
                    raise com.TableNotFound(name) from cause
                raise
            rows = cursor.fetchall()
        return sch.Schema(
            {
                str(row[0]): self.compiler.type_mapper.from_string(
                    str(row[1]), nullable=row[2] == "YES"
                )
                for row in rows
            }
        )

    def _get_schema_using_query(self, query: str) -> sch.Schema:
        raise com.UnsupportedOperationError(
            "The Marivo MySQL reader binds declared tables and views; "
            "arbitrary SQL schema inference is not supported."
        )

    def create_table(
        self,
        name: str,
        /,
        obj: object = None,
        *,
        schema: sch.Schema | None = None,
        database: str | None = None,
        temp: bool = False,
        overwrite: bool = False,
    ) -> ir.Table:
        raise com.UnsupportedOperationError("The Marivo MySQL reader does not create tables.")

    def _register_in_memory_table(self, op: ops.InMemoryTable) -> None:
        raise com.UnsupportedOperationError(
            "The Marivo MySQL reader does not upload in-memory tables."
        )

    def list_tables(
        self, like: str | None = None, database: tuple[str, str] | str | None = None
    ) -> list[str]:
        raise com.UnsupportedOperationError("The Marivo MySQL reader binds named tables and views.")

    @contextmanager
    def begin(self) -> Iterator[Cursor]:
        with self.con.cursor() as cursor:
            autocommit = self.con.get_autocommit()
            if not autocommit:
                self.con.begin()
            try:
                yield cursor
            except Exception:
                if not autocommit:
                    self.con.rollback()
                raise
            else:
                if not autocommit:
                    self.con.commit()

    def raw_sql(self, query: str) -> Cursor:
        cursor = self.con.cursor()
        autocommit = self.con.get_autocommit()
        try:
            if not autocommit:
                self.con.begin()
            cursor.execute(query)
            if not autocommit:
                self.con.commit()
        except Exception:
            try:
                if not autocommit:
                    self.con.rollback()
            finally:
                cursor.close()
            raise
        return cursor

    @contextmanager
    def _safe_raw_sql(self, query: str) -> Iterator[Cursor]:
        with self.raw_sql(query) as cursor:
            yield cursor

    def _fetch_from_cursor(self, cursor: Cursor, schema: sch.Schema) -> pd.DataFrame:
        frame = pd.DataFrame.from_records(
            cursor.fetchall(), columns=schema.names, coerce_float=True
        )
        return convert_mysql_frame(frame, schema)
