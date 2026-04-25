"""Trino catalog adapter — reads schema/table/column metadata via information_schema."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from importlib import import_module
from typing import Any

from app.adapters.base import (
    MAX_PREVIEW_ROWS,
    CatalogAdapter,
    CatalogCapabilities,
    PhysicalObject,
    PreviewResult,
)


class TrinoCatalogAdapter(CatalogAdapter):
    """Catalog adapter for Trino using information_schema queries."""

    def __init__(
        self,
        host: str,
        port: int = 8080,
        user: str = "marivo",
        password: str | None = None,
        http_scheme: str = "http",
        catalog: str = "hive",
        schema: str = "default",
        client_tags: list[str] | None = None,
        source: str | None = None,
        http_headers: dict[str, str] | None = None,
        request_timeout: float = 600.0,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._http_scheme = http_scheme
        self._catalog = catalog
        self._schema = schema
        self._client_tags = client_tags
        self._source = source
        self._http_headers = http_headers
        self._request_timeout = request_timeout

    def _connect(self) -> Any:
        connect_fn: Callable[..., Any] = import_module("trino.dbapi").connect
        # Filter out Trino reserved headers to avoid conflicts with
        # parameters (client_tags, source) that the client sets internally.
        _reserved_prefixes = ("x-trino-",)
        safe_headers: dict[str, str] | None = None
        if self._http_headers:
            safe_headers = {
                k: v
                for k, v in self._http_headers.items()
                if not k.lower().startswith(_reserved_prefixes)
            } or None

        kwargs: dict[str, Any] = {
            "host": self._host,
            "port": self._port,
            "user": self._user,
            "http_scheme": self._http_scheme,
            "catalog": self._catalog,
            "schema": self._schema,
            "request_timeout": self._request_timeout,
        }
        if self._password is not None:
            basic_authentication: Callable[[str, str], Any] = import_module(
                "trino.auth"
            ).BasicAuthentication
            kwargs["auth"] = basic_authentication(self._user, self._password)
        if self._client_tags is not None:
            kwargs["client_tags"] = self._client_tags
        if self._source is not None:
            kwargs["source"] = self._source
        if safe_headers is not None:
            kwargs["http_headers"] = safe_headers
        return connect_fn(**kwargs)

    def _query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            columns = [col[0] for col in cur.description]
            return [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]
        finally:
            conn.close()

    def _quote_identifier(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def source_type(self) -> str:
        return "trino"

    def capabilities(self) -> CatalogCapabilities:
        return CatalogCapabilities(
            supports_schemas=True,
            supports_column_stats=True,
            supports_partitions=False,
            supports_lineage=False,
            supports_tags=False,
            supports_access_control=False,
            supports_column_comments=True,
            supports_table_properties=True,
            supports_table_preview=True,
        )

    def test_connection(self) -> bool:
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()
            finally:
                conn.close()
            return True
        except Exception:
            return False

    def list_catalogs(self) -> list[PhysicalObject]:
        """Return all catalogs visible to this Trino connection."""
        rows = self._query("SHOW CATALOGS")
        return [
            PhysicalObject(
                native_name=next(iter(r.values())),
                native_id=None,
                object_type="catalog",
                parent_path=None,
            )
            for r in rows
        ]

    def list_schemas(self, catalog_name: str | None = None) -> list[PhysicalObject]:
        if catalog_name is None:
            # Aggregate schemas from all visible catalogs
            from contextlib import suppress

            schemas: list[PhysicalObject] = []
            for cat_obj in self.list_catalogs():
                with suppress(Exception):
                    # Some catalogs (e.g., jmx, system) may not support SHOW SCHEMAS
                    schemas.extend(self.list_schemas(cat_obj.native_name))
            return schemas
        # Use SHOW SCHEMAS FROM <catalog> instead of information_schema
        # because presto-gateway may not properly populate information_schema.schemata
        rows = self._query(f"SHOW SCHEMAS FROM {catalog_name}")
        return [
            PhysicalObject(
                native_name=r["Schema"],
                native_id=None,
                object_type="schema",
                parent_path=catalog_name,
            )
            for r in rows
            if r["Schema"] != "information_schema"
        ]

    def list_tables(self, schema_name: str) -> list[PhysicalObject]:
        table_rows = self._query(f"SHOW TABLES FROM {self._quote_identifier(schema_name)}")
        if not table_rows:
            return []

        try:
            column_rows = self._query(
                "SELECT table_name, COUNT(column_name) AS column_count "
                "FROM information_schema.columns "
                "WHERE table_catalog = ? AND table_schema = ? "
                "GROUP BY table_name",
                [self._catalog, schema_name],
            )
            column_counts = {
                str(row["table_name"]): int(row.get("column_count", 0) or 0) for row in column_rows
            }
        except Exception:
            column_counts = {
                str(row["Table"]): len(self._get_columns_metadata(schema_name, str(row["Table"])))
                for row in table_rows
            }
        return [
            PhysicalObject(
                native_name=str(r["Table"]),
                native_id=None,
                object_type="table",
                parent_path=schema_name,
                properties={
                    "table_type": "BASE TABLE",
                    "column_count": column_counts.get(str(r["Table"]), 0),
                },
            )
            for r in table_rows
        ]

    def _get_column_comments(self, schema_name: str, table_name: str) -> dict[str, str]:
        """Retrieve column comments using SHOW COLUMNS.

        Returns a dict mapping column_name -> comment (empty string if no comment).
        """
        try:
            rows = self._query(f'SHOW COLUMNS FROM "{schema_name}"."{table_name}"')
            # SHOW COLUMNS returns: Column, Type, Extra, Comment
            return {r["Column"]: r.get("Comment", "") or "" for r in rows}
        except Exception:
            # If SHOW COLUMNS fails (e.g., permission issue), return empty
            return {}

    def _get_columns_metadata(self, schema_name: str, table_name: str) -> list[dict[str, Any]]:
        """Retrieve ordered column metadata, preferring information_schema with SHOW fallback."""
        try:
            rows = self._query(
                "SELECT column_name, data_type, ordinal_position, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_catalog = ? AND table_schema = ? AND table_name = ? "
                "ORDER BY ordinal_position",
                [self._catalog, schema_name, table_name],
            )
        except Exception:
            rows = []
        if rows:
            return rows

        show_rows = self._query(f'SHOW COLUMNS FROM "{schema_name}"."{table_name}"')
        return [
            {
                "column_name": row["Column"],
                "data_type": row["Type"],
                "ordinal_position": position,
                "is_nullable": "YES",
            }
            for position, row in enumerate(show_rows, start=1)
        ]

    def _get_table_properties(self, schema_name: str, table_name: str) -> dict[str, Any]:
        """Retrieve table properties from Trino hidden metadata tables.

        Iceberg exposes ``table$properties`` as ``key`` / ``value`` rows. Hive exposes the same
        hidden table as a single wide row with one column per property. Support both shapes.
        """
        try:
            rows = self._query(f'SELECT key, value FROM "{schema_name}"."{table_name}$properties"')
            return {r["key"]: r["value"] for r in rows}
        except Exception:
            try:
                rows = self._query(f'SELECT * FROM "{schema_name}"."{table_name}$properties"')
            except Exception:
                # If both metadata paths fail (permissions, connector limitations, etc.), return
                # empty so sync/detail can continue.
                return {}
            if not rows:
                return {}
            return {k: v for k, v in rows[0].items() if v is not None}

    def _spark_type_to_trino_type(self, spark_type: str) -> str:
        normalized = spark_type.strip().lower()
        primitives = {
            "string": "varchar",
            "long": "bigint",
            "integer": "integer",
            "int": "integer",
            "short": "smallint",
            "byte": "tinyint",
            "double": "double",
            "float": "real",
            "boolean": "boolean",
            "date": "date",
            "timestamp": "timestamp",
            "binary": "varbinary",
        }
        return primitives.get(normalized, spark_type)

    def _parse_view_schema_fields(self, table_props: dict[str, Any]) -> list[dict[str, Any]]:
        schema_parts = [
            (key, value)
            for key, value in table_props.items()
            if key.startswith("spark.sql.sources.schema.part.")
        ]
        if not schema_parts:
            return []
        ordered_parts = [
            str(value)
            for key, value in sorted(
                schema_parts,
                key=lambda item: int(item[0].rsplit(".", 1)[1]),
            )
        ]
        try:
            payload = json.loads("".join(ordered_parts))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        fields = payload.get("fields")
        return list(fields) if isinstance(fields, list) else []

    def _parse_view_output_names(self, table_props: dict[str, Any]) -> list[str]:
        raw_count = table_props.get("view.query.out.numcols")
        if raw_count is None:
            return []
        try:
            count = int(str(raw_count))
        except ValueError:
            return []
        output_names: list[str] = []
        for position in range(count):
            key = f"view.query.out.col.{position}"
            value = table_props.get(key)
            if not isinstance(value, str) or not value.strip():
                return []
            output_names.append(value.strip())
        return output_names

    @staticmethod
    def _looks_like_view_properties(table_props: dict[str, Any]) -> bool:
        return any(
            key.startswith("view.query.out.") or key.startswith("view.catalogandnamespace.")
            for key in table_props
        )

    def _expand_view_column_rows(
        self,
        col_rows: list[dict[str, Any]],
        table_props: dict[str, Any],
        comments: dict[str, str],
    ) -> list[dict[str, Any]]:
        output_names = self._parse_view_output_names(table_props)
        schema_fields = self._parse_view_schema_fields(table_props)
        if not output_names or not schema_fields:
            return col_rows

        schema_by_name = {
            str(field.get("name")): field
            for field in schema_fields
            if field.get("name") is not None
        }
        rows_by_name = {str(row["column_name"]): row for row in col_rows}
        expanded_rows: list[dict[str, Any]] = []
        for position, column_name in enumerate(output_names, start=1):
            field = schema_by_name.get(column_name)
            if field is not None:
                metadata = dict(field.get("metadata") or {})
                expanded_rows.append(
                    {
                        "column_name": column_name,
                        "data_type": self._spark_type_to_trino_type(str(field.get("type") or "")),
                        "ordinal_position": position,
                        "is_nullable": "YES" if bool(field.get("nullable", True)) else "NO",
                        "comment": str(metadata.get("comment") or comments.get(column_name) or ""),
                    }
                )
                continue

            existing = rows_by_name.get(column_name)
            if existing is not None:
                expanded_rows.append(
                    {
                        **existing,
                        "ordinal_position": position,
                        "comment": comments.get(column_name, ""),
                    }
                )
                continue

            expanded_rows.append(
                {
                    "column_name": column_name,
                    "data_type": "unknown",
                    "ordinal_position": position,
                    "is_nullable": "YES",
                    "comment": comments.get(column_name, ""),
                }
            )
        return expanded_rows

    def _effective_column_rows(
        self,
        schema_name: str,
        table_name: str,
        *,
        table_props: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        col_rows = self._get_columns_metadata(schema_name, table_name)
        if not col_rows:
            return []
        comments = self._get_column_comments(schema_name, table_name)
        effective_table_props = (
            table_props
            if table_props is not None
            else self._get_table_properties(schema_name, table_name)
        )
        expanded_rows = self._expand_view_column_rows(col_rows, effective_table_props, comments)
        return [
            {
                **row,
                "comment": str(row.get("comment", "") or comments.get(str(row["column_name"]), "")),
            }
            for row in expanded_rows
        ]

    def _split_top_level(self, value: str) -> list[str]:
        parts: list[str] = []
        current: list[str] = []
        paren_depth = 0
        bracket_depth = 0
        in_quote = False
        i = 0
        while i < len(value):
            char = value[i]
            if char == "'":
                current.append(char)
                if in_quote and i + 1 < len(value) and value[i + 1] == "'":
                    current.append(value[i + 1])
                    i += 2
                    continue
                in_quote = not in_quote
                i += 1
                continue
            if not in_quote:
                if char == "(":
                    paren_depth += 1
                elif char == ")":
                    paren_depth -= 1
                elif char == "[":
                    bracket_depth += 1
                elif char == "]":
                    bracket_depth -= 1
                elif char == "," and paren_depth == 0 and bracket_depth == 0:
                    part = "".join(current).strip()
                    if part:
                        parts.append(part)
                    current = []
                    i += 1
                    continue
            current.append(char)
            i += 1
        tail = "".join(current).strip()
        if tail:
            parts.append(tail)
        return parts

    def _parse_show_create_value(self, raw_value: str) -> Any:
        value = raw_value.strip()
        upper_value = value.upper()
        if upper_value == "NULL":
            return None
        if upper_value == "TRUE":
            return True
        if upper_value == "FALSE":
            return False
        if value.startswith("'") and value.endswith("'"):
            return value[1:-1].replace("''", "'")
        if upper_value.startswith("ARRAY[") and value.endswith("]"):
            inner = value[value.find("[") + 1 : -1].strip()
            if not inner:
                return []
            return [self._parse_show_create_value(item) for item in self._split_top_level(inner)]
        try:
            number = Decimal(value)
        except Exception:
            return value
        if number == number.to_integral_value():
            return int(number)
        return float(number)

    def _extract_with_clause(self, create_sql: str) -> str | None:
        marker = "WITH ("
        upper_sql = create_sql.upper()
        start = upper_sql.find(marker)
        if start < 0:
            return None
        i = start + len(marker)
        depth = 1
        in_quote = False
        content: list[str] = []
        while i < len(create_sql):
            char = create_sql[i]
            if char == "'":
                content.append(char)
                if in_quote and i + 1 < len(create_sql) and create_sql[i + 1] == "'":
                    content.append(create_sql[i + 1])
                    i += 2
                    continue
                in_quote = not in_quote
                i += 1
                continue
            if not in_quote:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        return "".join(content).strip()
            content.append(char)
            i += 1
        return None

    def _get_show_create_properties(self, schema_name: str, table_name: str) -> dict[str, Any]:
        """Parse explicit WITH (...) properties from SHOW CREATE TABLE output."""
        try:
            rows = self._query(f'SHOW CREATE TABLE "{schema_name}"."{table_name}"')
        except Exception:
            return {}
        if not rows:
            return {}
        create_sql = next(iter(rows[0].values()), None)
        if not isinstance(create_sql, str):
            return {}
        with_clause = self._extract_with_clause(create_sql)
        if with_clause is None:
            return {}
        properties: dict[str, Any] = {}
        for assignment in self._split_top_level(with_clause):
            key, sep, raw_value = assignment.partition("=")
            if not sep:
                continue
            properties[key.strip()] = self._parse_show_create_value(raw_value)
        return properties

    def get_table_detail(self, schema_name: str, table_name: str) -> PhysicalObject:
        # Verify table exists
        table_rows = self._query(
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_catalog = ? AND table_schema = ? AND table_name = ?",
            [self._catalog, schema_name, table_name],
        )
        if not table_rows:
            raise KeyError(f"Table not found: {schema_name}.{table_name}")

        raw_table_props = self._get_table_properties(schema_name, table_name)
        show_create_props = self._get_show_create_properties(schema_name, table_name)
        table_props = dict(raw_table_props)
        table_props.update(show_create_props)
        col_rows = self._effective_column_rows(schema_name, table_name, table_props=table_props)
        raw_table_type = str(table_rows[0].get("table_type", "") or "")
        table_type = (
            "VIEW"
            if raw_table_type.upper() == "BASE TABLE"
            and self._looks_like_view_properties(table_props)
            else raw_table_type
        )

        columns = [
            {
                "name": r["column_name"],
                "type": r["data_type"],
                "position": r["ordinal_position"],
                "nullable": r["is_nullable"] == "YES",
                "comment": str(r.get("comment", "") or ""),
            }
            for r in col_rows
        ]

        properties: dict[str, Any] = {
            "columns": columns,
            "column_count": len(columns),
            "table_type": table_type,
        }

        # Add table properties if available
        if table_props:
            properties["table_properties"] = table_props
            if raw_table_props and show_create_props:
                properties["raw_table_properties"] = raw_table_props
            # Extract commonly-used properties as top-level for convenience
            if "comment" in table_props:
                properties["comment"] = table_props["comment"]
            if "owner" in table_props:
                properties["owner"] = table_props["owner"]

        return PhysicalObject(
            native_name=table_name,
            native_id=None,
            object_type="table",
            parent_path=schema_name,
            properties=properties,
        )

    def list_columns(self, schema_name: str, table_name: str) -> list[PhysicalObject]:
        rows = self._effective_column_rows(schema_name, table_name)

        return [
            PhysicalObject(
                native_name=r["column_name"],
                native_id=None,
                object_type="column",
                parent_path=f"{schema_name}.{table_name}",
                properties={
                    "data_type": r["data_type"],
                    "nullable": r["is_nullable"] == "YES",
                    "comment": str(r.get("comment", "") or ""),
                },
            )
            for r in rows
        ]

    def get_table_stats(self, schema_name: str, table_name: str) -> dict[str, Any]:
        """Return column-level stats via SHOW STATS."""
        rows = self._query(f'SHOW STATS FOR "{schema_name}"."{table_name}"')
        stats: dict[str, Any] = {"columns": {}}
        for r in rows:
            col_name = r.get("column_name")
            if col_name is None:
                # Summary row with row_count
                stats["row_count"] = r.get("row_count")
            else:
                stats["columns"][col_name] = {
                    "data_size": r.get("data_size"),
                    "distinct_count": r.get("distinct_values_count"),
                    "nulls_fraction": r.get("nulls_fraction"),
                    "row_count": r.get("row_count"),
                    "low_value": r.get("low_value"),
                    "high_value": r.get("high_value"),
                }
        return stats

    def preview_table(
        self,
        schema_name: str,
        table_name: str,
        limit: int = 100,
        columns: list[str] | None = None,
    ) -> PreviewResult:
        """Preview sample rows from a Trino table."""
        effective_limit = min(max(1, limit), MAX_PREVIEW_ROWS)

        # 1. Get column metadata and verify table exists
        raw_table_props = self._get_table_properties(schema_name, table_name)
        show_create_props = self._get_show_create_properties(schema_name, table_name)
        table_props = dict(raw_table_props)
        table_props.update(show_create_props)
        col_rows = self._effective_column_rows(schema_name, table_name, table_props=table_props)
        if not col_rows:
            raise KeyError(f"Table {schema_name}.{table_name} not found")

        all_columns = {str(r["column_name"]): r["data_type"] for r in col_rows}

        # 2. Validate column selection
        if columns is not None:
            invalid = [c for c in columns if c not in all_columns]
            if invalid:
                raise ValueError(f"Unknown columns: {invalid}")
            selected_columns = columns
        else:
            selected_columns = list(all_columns.keys())

        # 3. Build safe SELECT with quoted identifiers
        quoted_cols = ", ".join(self._quote_identifier(c) for c in selected_columns)
        quoted_schema = self._quote_identifier(schema_name)
        quoted_table = self._quote_identifier(table_name)
        # Fetch limit+1 rows to accurately detect truncation
        sql = (
            f"SELECT {quoted_cols} FROM {quoted_schema}.{quoted_table} LIMIT {effective_limit + 1}"
        )

        # 4. Execute via _query helper
        rows = self._query(sql)

        # 5. Determine truncation and trim to actual limit
        truncated = len(rows) > effective_limit
        if truncated:
            rows = rows[:effective_limit]

        return PreviewResult(
            columns=[{"name": c, "type": all_columns[c]} for c in selected_columns],
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
        )
