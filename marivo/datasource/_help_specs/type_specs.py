"""Datasource result and credential type contracts."""

from collections.abc import Mapping
from types import MappingProxyType

from marivo._kernel.data_render import _display_help
from marivo.datasource._help_specs.builders import _target
from marivo.datasource.credentials import CredentialRequest, CredentialResolver, SecretValue
from marivo.datasource.credentials.inputs import (
    EnvRef,
    HttpBearerSecret,
    HttpHeadersSecret,
    S3Secret,
)
from marivo.datasource.inspection.catalog import DatasourceCatalog
from marivo.datasource.inspection.details import (
    ExecutionCapabilities,
    Partitioning,
    PartitionInspection,
    PhysicalExtent,
)
from marivo.datasource.inspection.manage import (
    DatasourceDescription,
    DatasourceFailure,
    DatasourceList,
    DatasourceSummary,
    DatasourceTestResult,
)
from marivo.datasource.inspection.raw_sql import RawSqlResult
from marivo.datasource.inspection.source import SourceInspection
from marivo.datasource.ir import (
    CsvSourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    SourceParamIR,
    TableSourceIR,
)
from marivo.datasource.source import PartitionScope, UnprunedScope
from marivo.datasource.store.authoring import (
    ClickHouseSpec,
    DuckDBSpec,
    MySQLSpec,
    PostgresSpec,
    TrinoSpec,
)
from marivo.datasource.store.evidence import DiscoveryEvidence
from marivo.introspection.capabilities.registry import TypeContractSpec
from marivo.introspection.live.targets import canonical_target

show_render = ("show", "render")


def add(
    cls: type,
    name: str,
    producers: tuple[str, ...],
    *,
    properties: tuple[str, ...] = (),
    methods: tuple[str, ...] = (),
    type_page_methods: tuple[str, ...] = (),
    consumers: tuple[str, ...] = (),
    guidance: tuple[str, ...] = (),
) -> tuple[type, TypeContractSpec]:
    if all(method in methods for method in show_render) and not type_page_methods:
        type_page_methods = show_render
        guidance = (
            *guidance,
            "Display: .show(*, max_output_bytes=8192) prints and returns None; "
            ".render(*, max_output_bytes=8192) returns text. "
            "Use None to remove the byte budget; output may omit details within a budget.",
        )
    return cls, TypeContractSpec(
        name=name,
        producers=tuple(_target(canonical_target(value)) for value in producers),
        public_properties=properties,
        public_methods=methods,
        type_page_methods=type_page_methods,
        guidance=guidance,
        consumers=tuple(_target(canonical_target(value)) for value in consumers),
    )


TYPE_CONTRACTS: Mapping[type, TypeContractSpec] = MappingProxyType(
    dict(
        (
            add(
                CredentialRequest,
                "CredentialRequest",
                ("credential_scope",),
                properties=(
                    "reference",
                    "project_root",
                    "datasource",
                    "fields",
                ),
                methods=("show",),
                consumers=("CredentialResolver.resolve",),
            ),
            add(
                CredentialResolver,
                "CredentialResolver",
                (),
                methods=("resolve",),
                consumers=("credential_scope",),
            ),
            add(
                SecretValue,
                "SecretValue",
                ("CredentialResolver.resolve",),
                methods=("reveal",),
                consumers=("SecretValue.reveal",),
            ),
            *(
                add(
                    cls,
                    cls.__name__,
                    (producer,),
                    properties=properties,
                    methods=show_render,
                    consumers=("duckdb",),
                )
                for cls, producer, properties in (
                    (EnvRef, "env", ("name",)),
                    (
                        S3Secret,
                        "s3_secret",
                        (
                            "scope",
                            "region",
                            "key_id",
                            "secret",
                            "session_token",
                            "endpoint",
                            "url_style",
                            "use_ssl",
                        ),
                    ),
                    (HttpBearerSecret, "http_bearer_secret", ("scope", "token")),
                    (HttpHeadersSecret, "http_headers_secret", ("scope", "headers")),
                )
            ),
            *(
                add(
                    spec_type,
                    spec_type.__name__,
                    (producer,),
                    properties=(
                        "name",
                        "backend_type",
                        "fields",
                        "env_refs",
                        "wall_clock_timezone",
                        "ref",
                    ),
                    methods=show_render,
                    consumers=("register",),
                )
                for spec_type, producer in (
                    (DuckDBSpec, "duckdb"),
                    (TrinoSpec, "trino"),
                    (MySQLSpec, "mysql"),
                    (PostgresSpec, "postgres"),
                    (ClickHouseSpec, "clickhouse"),
                )
            ),
            add(
                DatasourceCatalog,
                "DatasourceCatalog",
                ("load",),
                methods=("list", "describe", "test", *show_render),
                guidance=("Display reads current local and configured datasource declarations.",),
            ),
            add(
                DatasourceSummary,
                "DatasourceSummary",
                ("register", "DatasourceCatalog.list"),
                properties=("name", "backend_type"),
                methods=show_render,
            ),
            add(
                DatasourceList,
                "DatasourceList",
                ("DatasourceCatalog.list",),
                properties=("items",),
                methods=show_render,
            ),
            add(
                DatasourceDescription,
                "DatasourceDescription",
                ("DatasourceCatalog.describe",),
                properties=(
                    "name",
                    "backend_type",
                    "literal_fields",
                    "env_refs",
                    "wall_clock_timezone",
                ),
                methods=show_render,
            ),
            add(
                DatasourceFailure,
                "DatasourceFailure",
                ("DatasourceCatalog.test",),
                guidance=("Read result.failure from a failed datasource test result.",),
                properties=(
                    "code",
                    "exception_type",
                    "backend_code",
                    "backend_name",
                    "message",
                    "timeout_seconds",
                ),
            ),
            add(
                DatasourceTestResult,
                "DatasourceTestResult",
                ("DatasourceCatalog.test",),
                properties=("name", "ok", "latency_ms", "failure", "repair"),
                methods=show_render,
            ),
            add(
                RawSqlResult,
                "RawSqlResult",
                ("raw_sql",),
                properties=(
                    "datasource",
                    "backend_type",
                    "sql",
                    "reason",
                    "columns",
                    "types",
                    "rows",
                    "returned_row_count",
                    "warnings",
                ),
                methods=(*show_render, "to_pandas"),
                type_page_methods=show_render,
                guidance=_display_help(),
            ),
            *(
                add(
                    source_type,
                    source_type.__name__,
                    (source_type.__name__.removesuffix("SourceIR").lower(),),
                    properties=(
                        ("kind", "table", "database", "columns")
                        if source_type is TableSourceIR
                        else ("kind",)
                    ),
                    consumers=("inspect",),
                )
                for source_type in (TableSourceIR, ParquetSourceIR, CsvSourceIR, JsonSourceIR)
            ),
            add(
                SourceParamIR,
                "SourceParamIR",
                ("source_param",),
                properties=("name",),
                consumers=("json",),
            ),
            add(
                PartitionScope,
                "PartitionScope",
                ("partition", "time_range"),
                properties=("values", "max_rows", "timeout_seconds"),
                methods=show_render,
                consumers=("SourceInspection.sample",),
            ),
            add(
                UnprunedScope,
                "UnprunedScope",
                ("unpruned",),
                properties=("max_rows", "timeout_seconds"),
                methods=show_render,
                consumers=("SourceInspection.sample",),
            ),
            add(
                PhysicalExtent,
                "PhysicalExtent",
                (),
                guidance=("Read inspection.physical_extent from md.inspect(...).",),
                properties=(
                    "row_count",
                    "row_count_kind",
                    "size_bytes",
                    "size_kind",
                    "source",
                    "notes",
                ),
            ),
            add(
                Partitioning,
                "Partitioning",
                (),
                guidance=("Read inspection.partitioning or inspection.partitions().partitioning.",),
                properties=(
                    "state",
                    "fields",
                    "value_source",
                    "values",
                    "values_complete",
                    "truncated",
                ),
            ),
            add(
                ExecutionCapabilities,
                "ExecutionCapabilities",
                (),
                guidance=("Read inspection.execution_capabilities from md.inspect(...).",),
                properties=(
                    "partition_predicate_supported",
                    "transformed_partition_supported",
                    "timeout_enforced",
                    "byte_estimate_supported",
                ),
            ),
            add(
                PartitionInspection,
                "PartitionInspection",
                ("SourceInspection.partitions",),
                properties=(
                    "datasource",
                    "source",
                    "partitioning",
                    "limit",
                    "order",
                    "status",
                    "issues",
                ),
                methods=show_render,
            ),
            add(
                SourceInspection,
                "SourceInspection",
                ("inspect",),
                properties=(
                    "datasource",
                    "source",
                    "physical_extent",
                    "partitioning",
                    "execution_capabilities",
                    "schema",
                    "projectable_columns",
                    "warnings",
                ),
                methods=("partitions", "source_column", "sample", *show_render),
                consumers=(
                    "SourceInspection.partitions",
                    "SourceInspection.source_column",
                    "SourceInspection.sample",
                ),
            ),
            add(
                DiscoveryEvidence,
                "DiscoveryEvidence",
                ("SourceInspection.sample",),
                properties=(
                    "id",
                    "datasource",
                    "source",
                    "scope",
                    "source_params",
                    "columns",
                    "schema_fingerprint",
                    "profiles",
                    "coverage",
                    "persist_values",
                    "value_evidence_state",
                    "cache_status",
                    "created_at",
                    "expires_at",
                    "retained_values",
                ),
                methods=show_render,
                guidance=(
                    "With default persistence, a fresh acquisition and its in-process cache retain profile summary values, "
                    "but retained_values is empty and value_evidence_state is value_evidence_unavailable. "
                    "persist_values=True retains named rows and persists bounded values; "
                    "coverage.retained_row_count alone does not mean rows can be read.",
                ),
            ),
        )
    )
)
