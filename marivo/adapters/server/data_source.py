from __future__ import annotations

import logging
from typing import Any

from marivo.adapters.server.datasource_registry import DatasourceRegistry
from marivo.contracts.errors import DomainError, ErrorCode, NotFoundError, ValidationError
from marivo.contracts.ids import DatasourceId
from marivo.contracts.values import ColumnInfo, LogicalQuery, QueryResult, SourceRef, SourceSchema
from marivo.routing import QueryRouter
from marivo.storage.analytics import AnalyticsEngine

logger = logging.getLogger(__name__)


class DataSourceAdapter:
    """Wraps ``AnalyticsEngine`` + ``QueryRouter`` -> ``DataSource``.

    Delegates ``execute`` to the analytics engine and ``schema`` to the
    catalog adapter via the router's metadata store.
    """

    def __init__(
        self,
        engine: AnalyticsEngine,
        router: QueryRouter,
    ) -> None:
        self._engine = engine
        self._router = router

    def execute(self, query: LogicalQuery) -> QueryResult:
        """Execute a logical query against the analytics engine."""
        try:
            rows = self._engine.query_rows(
                query.sql, list(query.params.values()) if query.params else None
            )
        except Exception as exc:
            raise DomainError(ErrorCode.QUERY_EXECUTION_FAILED, str(exc)) from exc
        if not rows:
            return QueryResult(columns=[], rows=[], row_count=0, query_sql=query.sql)
        columns = list(rows[0].keys())
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            query_sql=query.sql,
        )

    def resolve_tables(self, table_names: list[str], *, session_id: str | None = None) -> Any:
        """Delegate table resolution to the QueryRouter via RoutingRuntime."""
        from marivo.execution.routing_runtime import RoutingRuntime

        routing_runtime = RoutingRuntime(self._router, self._engine)
        return routing_runtime.resolve_tables(table_names, session_id=session_id)

    def schema(self, source_ref: SourceRef) -> SourceSchema:
        """Return the schema for the referenced source table.

        Delegates to ``DatasourceRegistry.browse_catalog_columns`` via
        the router's ``datasource_service``. Falls back to an empty
        schema if the datasource or table cannot be resolved.
        """
        try:
            datasource_id = source_ref.datasource_id
            datasource_service = self._router.datasource_service
            try:
                col_dicts = datasource_service.browse_catalog_columns(
                    datasource_id,
                    source_ref.schema_name,
                    source_ref.table_name,
                )
            except (KeyError, NotImplementedError, ValueError):
                # Datasource or table not found; return empty schema
                return SourceSchema(columns=[])
            columns = [
                ColumnInfo(
                    name=col.get("name", "unknown"),
                    dtype=col.get("data_type", "unknown"),
                    nullable=col.get("properties", {}).get("nullable", True),
                )
                for col in col_dicts
            ]
            return SourceSchema(columns=columns)
        except KeyError as exc:
            raise DomainError(ErrorCode.DATASOURCE_UNAVAILABLE, str(exc)) from exc
        except Exception as exc:
            raise DomainError(ErrorCode.DATASOURCE_UNAVAILABLE, str(exc)) from exc


class RoutingDataSource:
    """DataSource that routes queries to per-datasource cached engines.

    When ``datasource_id`` is present on the query, the correct engine is
    resolved (and cached) from the registry.  When absent, the default
    engine (typically DuckDB) is used.
    """

    def __init__(
        self,
        default_engine: AnalyticsEngine,
        registry: DatasourceRegistry,
        query_router: QueryRouter,
    ) -> None:
        self._default_engine = default_engine
        self._registry = registry
        self._query_router = query_router
        self._engine_cache: dict[DatasourceId, AnalyticsEngine] = {}

    # -- DataSource interface --------------------------------------------------

    def execute(self, query: LogicalQuery | str) -> QueryResult:
        """Execute a logical query against the resolved analytics engine.

        Accepts both ``LogicalQuery`` objects and raw SQL strings for
        parity with the local DuckDB adapter.
        """
        # Normalize raw SQL strings to LogicalQuery for uniform handling
        if isinstance(query, str):
            query = LogicalQuery(sql=query, params={}, datasource_id=None)
        engine = self._resolve_engine(query.datasource_id)
        try:
            rows = engine.query_rows(
                query.sql,
                list(query.params.values()) if query.params else None,
            )
        except ImportError as exc:
            raise DomainError(
                ErrorCode.DATASOURCE_UNAVAILABLE,
                f"Engine driver not installed: {exc}. Install with: pip install marivo[trino]",
            ) from exc
        except ValidationError:
            raise
        except NotFoundError:
            raise
        except DomainError:
            raise
        except Exception as exc:
            exc_name = type(exc).__name__
            if "Parser" in exc_name or "Syntax" in exc_name:
                raise ValidationError(
                    code=ErrorCode.VALIDATION,
                    message=f"Query could not be parsed: {exc}",
                ) from exc
            if "Catalog" in exc_name:
                raise NotFoundError(
                    code=ErrorCode.NOT_FOUND,
                    message=f"Catalog object not found: {exc}",
                ) from exc
            raise DomainError(ErrorCode.QUERY_EXECUTION_FAILED, str(exc)) from exc
        if not rows:
            return QueryResult(columns=[], rows=[], row_count=0, query_sql=query.sql)
        columns = list(rows[0].keys())
        return QueryResult(columns=columns, rows=rows, row_count=len(rows), query_sql=query.sql)

    def schema(self, source_ref: SourceRef) -> SourceSchema:
        """Return the schema for the referenced source table.

        First tries ``DatasourceRegistry.browse_catalog_columns``.
        If the datasource is not registered, falls back to querying
        the default analytics engine's ``information_schema.columns``
        directly (parity with local DuckDB adapter).
        """
        try:
            col_dicts = self._registry.browse_catalog_columns(
                source_ref.datasource_id,
                source_ref.schema_name,
                source_ref.table_name,
            )
            columns = [
                ColumnInfo(
                    name=col.get("name", "unknown"),
                    dtype=col.get("data_type", "unknown"),
                    nullable=col.get("properties", {}).get("nullable", True),
                )
                for col in col_dicts
            ]
            return SourceSchema(columns=columns)
        except (KeyError, NotImplementedError, ValueError):
            # Datasource not registered; fall through to engine query
            pass
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(ErrorCode.DATASOURCE_UNAVAILABLE, str(exc)) from exc

        # Fallback: query the default engine's information_schema
        try:
            rows = self._default_engine.query_rows(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ? "
                "ORDER BY ordinal_position",
                [source_ref.schema_name, source_ref.table_name],
            )
        except Exception as exc:
            raise NotFoundError(
                code=ErrorCode.NOT_FOUND,
                message=f"Table '{source_ref.schema_name}.{source_ref.table_name}' not found: {exc}",
            ) from exc
        if not rows:
            raise NotFoundError(
                code=ErrorCode.NOT_FOUND,
                message=f"Table '{source_ref.schema_name}.{source_ref.table_name}' not found in data source",
            )
        columns = [
            ColumnInfo(
                name=row["column_name"],
                dtype=row["data_type"],
                nullable=row.get("is_nullable", "YES") == "YES",
            )
            for row in rows
        ]
        return SourceSchema(columns=columns)

    def resolve_tables(self, table_names: list[str], *, session_id: str | None = None) -> Any:
        """Delegate table resolution to the QueryRouter via RoutingRuntime."""
        from marivo.execution.routing_runtime import RoutingRuntime

        runtime = RoutingRuntime(self._query_router, self._default_engine)
        return runtime.resolve_tables(table_names, session_id=session_id)

    # -- Internal --------------------------------------------------------------

    def _resolve_engine(self, datasource_id: DatasourceId | None) -> AnalyticsEngine:
        """Return the analytics engine for the given datasource, with caching."""
        if datasource_id is None:
            return self._default_engine
        if datasource_id in self._engine_cache:
            return self._engine_cache[datasource_id]
        try:
            engine = self._registry.build_analytics_engine(datasource_id)
        except (KeyError, ValueError) as exc:
            raise DomainError(
                ErrorCode.DATASOURCE_UNAVAILABLE,
                f"Datasource {datasource_id!r} not found or unavailable",
            ) from exc
        self._engine_cache[datasource_id] = engine
        return engine
