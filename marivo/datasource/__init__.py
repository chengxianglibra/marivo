"""Project-level datasource authoring and management API."""

from __future__ import annotations

from marivo.datasource.credentials import (
    CredentialRequest,
    CredentialResolver,
    SecretValue,
    credential_scope,
)
from marivo.datasource.credentials.inputs import (
    EnvRef,
    HttpBearerSecret,
    HttpHeadersSecret,
    S3Secret,
    env,
    http_bearer_secret,
    http_headers_secret,
    s3_secret,
)
from marivo.datasource.errors import DatasourceCredentialError, DatasourceCredentialScopeError
from marivo.datasource.inspection.catalog import DatasourceCatalog, load
from marivo.datasource.inspection.details import PartitionInspection
from marivo.datasource.inspection.inspection import inspect
from marivo.datasource.inspection.manage import (
    DatasourceDescription,
    DatasourceList,
    DatasourceSummary,
    DatasourceTestResult,
    register,
    remove,
)
from marivo.datasource.inspection.raw_sql import raw_sql
from marivo.datasource.inspection.source import SourceInspection
from marivo.datasource.source import (
    TableSource,
    csv,
    json,
    parquet,
    partition,
    source_param,
    table,
    time_range,
    unpruned,
)
from marivo.datasource.store.authoring import (
    ClickHouseSpec,
    DatasourceSpec,
    DuckDBSpec,
    MySQLSpec,
    PostgresSpec,
    TrinoSpec,
)
from marivo.datasource.store.builders import clickhouse, duckdb, mysql, postgres, trino
from marivo.datasource.store.evidence import DiscoveryEvidence

__all__ = [
    "ClickHouseSpec",
    "CredentialRequest",
    "CredentialResolver",
    "DatasourceCatalog",
    "DatasourceCredentialError",
    "DatasourceCredentialScopeError",
    "DatasourceDescription",
    "DatasourceList",
    "DatasourceSpec",
    "DatasourceSummary",
    "DatasourceTestResult",
    "DiscoveryEvidence",
    "DuckDBSpec",
    "EnvRef",
    "HttpBearerSecret",
    "HttpHeadersSecret",
    "MySQLSpec",
    "PartitionInspection",
    "PostgresSpec",
    "S3Secret",
    "SecretValue",
    "SourceInspection",
    "TableSource",
    "TrinoSpec",
    "clickhouse",
    "credential_scope",
    "csv",
    "duckdb",
    "env",
    "http_bearer_secret",
    "http_headers_secret",
    "inspect",
    "json",
    "load",
    "mysql",
    "parquet",
    "partition",
    "postgres",
    "raw_sql",
    "register",
    "remove",
    "s3_secret",
    "source_param",
    "table",
    "time_range",
    "trino",
    "unpruned",
]
