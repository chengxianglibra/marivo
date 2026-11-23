"""Catalog proofs that a bound source reads stored fields without computed columns."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from marivo.datasource.engines.statements import (
    ProviderStatement,
    execute_provider_statement,
    register_provider_statements,
)

if TYPE_CHECKING:
    import ibis.expr.operations as ops
    from ibis.backends import BaseBackend

    from marivo.datasource.engines.base import EngineProfile
    from marivo.datasource.ir import DatasourceIR


_STATEMENTS = {
    "duckdb": (
        "SELECT 1 FROM duckdb_tables() t WHERE lower(t.table_name) = lower({table}) "
        "AND lower(t.database_name) = lower(COALESCE(NULLIF({catalog}, ''), current_database())) "
        "AND lower(t.schema_name) = lower(COALESCE(NULLIF({schema}, ''), current_schema())) "
        "AND (SELECT count(*) FROM duckdb_tables() other "
        "WHERE lower(other.table_name) = lower(t.table_name) "
        "AND ({catalog} = '' OR lower(other.database_name) = lower({catalog})) "
        "AND ({schema} = '' OR lower(other.schema_name) = lower({schema}))) = 1 "
        "AND NOT EXISTS (SELECT 1 FROM duckdb_views() v WHERE lower(v.view_name) = lower(t.table_name) "
        "AND ({catalog} = '' OR lower(v.database_name) = lower({catalog})) "
        "AND ({schema} = '' OR lower(v.schema_name) = lower({schema}))) "
        "AND NOT EXISTS (SELECT 1 FROM duckdb_columns() c WHERE c.table_oid = t.table_oid "
        "AND c.database_name = t.database_name AND c.column_default IS NOT NULL) LIMIT 1",
        frozenset({"catalog", "schema", "table"}),
        frozenset(),
    ),
    "postgres": (
        "SELECT 1 FROM pg_catalog.pg_class c WHERE c.relkind = 'r' "
        "AND c.oid = pg_catalog.to_regclass(CASE WHEN {schema} = '' "
        "THEN pg_catalog.quote_ident({table}) ELSE pg_catalog.quote_ident({schema}) "
        "|| '.' || pg_catalog.quote_ident({table}) END) "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent = c.oid) "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid = c.oid "
        "AND a.attnum > 0 AND a.attgenerated <> '') LIMIT 1",
        frozenset({"schema", "table"}),
        frozenset(),
    ),
    "mysql": (
        "SELECT 1 FROM information_schema.tables t "
        "WHERE BINARY t.table_schema = BINARY COALESCE(NULLIF({schema}, ''), DATABASE()) "
        "AND BINARY t.table_name = BINARY {table} AND t.table_type = 'BASE TABLE' "
        "AND t.engine IN ('InnoDB', 'MyISAM', 'MEMORY') "
        "AND NOT EXISTS (SELECT 1 FROM information_schema.columns c "
        "WHERE BINARY c.table_schema = BINARY t.table_schema AND BINARY c.table_name = BINARY t.table_name "
        "AND COALESCE(c.generation_expression, '') <> '') LIMIT 1",
        frozenset({"schema", "table"}),
        frozenset(),
    ),
    "trino": (
        "SELECT 1 FROM {catalog_tables} t WHERE t.table_catalog = {catalog} "
        "AND t.table_schema = {schema} AND t.table_name = {table} "
        "AND t.table_type = 'BASE TABLE' "
        "AND EXISTS (SELECT 1 FROM system.metadata.catalogs c WHERE c.catalog_name = {catalog} "
        "AND c.connector_name IN ('iceberg', 'hive', 'memory', 'delta_lake')) LIMIT 1",
        frozenset({"catalog", "schema", "table"}),
        frozenset({"catalog_tables"}),
    ),
    "clickhouse": (
        "SELECT 1 FROM system.tables t WHERE t.database = coalesce(nullIf({schema}, ''), currentDatabase()) "
        "AND t.name = {table} AND (endsWith(t.engine, 'MergeTree') "
        "OR t.engine IN ('Memory', 'Log', 'TinyLog', 'StripeLog')) "
        "AND (t.database, t.name) NOT IN "
        "(SELECT database, table FROM system.columns WHERE default_kind != '') LIMIT 1",
        frozenset({"schema", "table"}),
        frozenset(),
    ),
}

for _name, (_sql, _literal_slots, _identifier_slots) in _STATEMENTS.items():
    register_provider_statements(
        _name,
        {
            "tables.stored_projection": ProviderStatement(
                statement_id=f"{_name}.tables.stored_projection",
                template=_sql,
                literal_slots=_literal_slots,
                identifier_slots=_identifier_slots,
                allowed_purposes=frozenset({f"datasource.metadata.{_name}"}),
            )
        },
    )


def proves_stored_projection(
    backend: BaseBackend,
    profile: EngineProfile,
    datasource: DatasourceIR,
    relation: ops.DatabaseTable,
) -> bool:
    """Qualify the exact catalog relation through a metadata-only provider statement."""
    specification = _STATEMENTS.get(profile.name)
    if specification is None or relation.source is not backend:
        return False
    _, literal_slots, identifier_slots = specification
    catalog = relation.namespace.catalog or str(datasource.fields.get("catalog") or "")
    schema = relation.namespace.database or str(datasource.fields.get("schema") or "")
    if identifier_slots and not (catalog and schema):
        return False
    values = {"catalog": catalog, "schema": schema, "table": relation.name}
    identifiers: Mapping[str, tuple[str, ...]] = (
        {"catalog_tables": (catalog, "information_schema", "tables")} if identifier_slots else {}
    )
    return bool(
        execute_provider_statement(
            backend,
            profile,
            f"{profile.name}.tables.stored_projection",
            values={name: values[name] for name in literal_slots},
            identifiers=identifiers,
            purpose=f"datasource.metadata.{profile.name}",
        )
    )
