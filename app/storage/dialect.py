from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class MetadataDialect(ABC):
    """SQL generation rules for a metadata backend."""

    backend_name: str
    placeholder: str

    def compile_sql(self, sql: str) -> str:
        if self.placeholder == "?":
            return sql
        return _replace_placeholders(sql, self.placeholder)

    @abstractmethod
    def now_sql(self) -> str:
        """Return a backend-native current timestamp SQL expression."""

    @abstractmethod
    def insert_ignore_sql(self, table: str, columns: list[str]) -> str:
        """Return an idempotent insert SQL statement using canonical placeholders."""

    @abstractmethod
    def upsert_sql(
        self,
        table: str,
        insert_columns: list[str],
        conflict_columns: list[str],
        update_columns: list[str],
        *,
        updated_at_column: str | None = None,
    ) -> str:
        """Return an upsert SQL statement using canonical placeholders."""

    def placeholders(self, count: int) -> str:
        return ", ".join("?" for _ in range(count))


class SQLiteMetadataDialect(MetadataDialect):
    def __init__(self) -> None:
        super().__init__(backend_name="sqlite", placeholder="?")

    def now_sql(self) -> str:
        return "datetime('now')"

    def insert_ignore_sql(self, table: str, columns: list[str]) -> str:
        return (
            f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) "
            f"VALUES ({self.placeholders(len(columns))})"
        )

    def upsert_sql(
        self,
        table: str,
        insert_columns: list[str],
        conflict_columns: list[str],
        update_columns: list[str],
        *,
        updated_at_column: str | None = None,
    ) -> str:
        assignments = [f"{column} = excluded.{column}" for column in update_columns]
        if updated_at_column is not None:
            assignments.append(f"{updated_at_column} = {self.now_sql()}")
        return (
            f"INSERT INTO {table} ({', '.join(insert_columns)}) "
            f"VALUES ({self.placeholders(len(insert_columns))}) "
            f"ON CONFLICT({', '.join(conflict_columns)}) DO UPDATE SET "
            f"{', '.join(assignments)}"
        )


class MySQLMetadataDialect(MetadataDialect):
    def __init__(self) -> None:
        super().__init__(backend_name="mysql", placeholder="%s")

    def now_sql(self) -> str:
        return "CURRENT_TIMESTAMP"

    def insert_ignore_sql(self, table: str, columns: list[str]) -> str:
        return (
            f"INSERT IGNORE INTO {table} ({', '.join(columns)}) "
            f"VALUES ({self.placeholders(len(columns))})"
        )

    def upsert_sql(
        self,
        table: str,
        insert_columns: list[str],
        conflict_columns: list[str],
        update_columns: list[str],
        *,
        updated_at_column: str | None = None,
    ) -> str:
        del conflict_columns
        assignments = [f"{column} = VALUES({column})" for column in update_columns]
        if updated_at_column is not None:
            assignments.append(f"{updated_at_column} = {self.now_sql()}")
        return (
            f"INSERT INTO {table} ({', '.join(insert_columns)}) "
            f"VALUES ({self.placeholders(len(insert_columns))}) "
            f"ON DUPLICATE KEY UPDATE {', '.join(assignments)}"
        )


def _replace_placeholders(sql: str, replacement: str) -> str:
    result: list[str] = []
    i = 0
    length = len(sql)
    state = "normal"
    while i < length:
        char = sql[i]
        next_char = sql[i + 1] if i + 1 < length else ""

        if state == "normal":
            if char == "'":
                state = "single"
                result.append(char)
            elif char == '"':
                state = "double"
                result.append(char)
            elif char == "`":
                state = "backtick"
                result.append(char)
            elif char == "-" and next_char == "-":
                state = "line_comment"
                result.extend([char, next_char])
                i += 1
            elif char == "#":
                state = "line_comment"
                result.append(char)
            elif char == "/" and next_char == "*":
                state = "block_comment"
                result.extend([char, next_char])
                i += 1
            elif char == "?":
                result.append(replacement)
            else:
                result.append(char)
        elif state == "single":
            result.append(char)
            if char == "'" and next_char == "'":
                result.append(next_char)
                i += 1
            elif char == "'":
                state = "normal"
        elif state == "double":
            result.append(char)
            if char == '"' and next_char == '"':
                result.append(next_char)
                i += 1
            elif char == '"':
                state = "normal"
        elif state == "backtick":
            result.append(char)
            if char == "`":
                state = "normal"
        elif state == "line_comment":
            result.append(char)
            if char == "\n":
                state = "normal"
        elif state == "block_comment":
            result.append(char)
            if char == "*" and next_char == "/":
                result.append(next_char)
                i += 1
                state = "normal"
        i += 1
    return "".join(result)


SQLITE_METADATA_DIALECT = SQLiteMetadataDialect()
MYSQL_METADATA_DIALECT = MySQLMetadataDialect()
