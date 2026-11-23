"""Internal datasource contracts for governed reads, inspection and expressions."""

from marivo.datasource._expression_fields import physical_fields
from marivo.datasource.credentials import (
    backend_errors,
    bind_resolver,
    check_binding,
    current_resolver,
    injected_values,
    operation_context,
    redact,
)
from marivo.datasource.credentials.inputs import (
    DuckDBSecret,
    EnvRef,
    credential_labels,
    env,
    http_bearer_secret,
    http_headers_secret,
    s3_secret,
)
from marivo.datasource.engines import (
    DEFAULT_BACKEND_TYPE,
    ENGINE_PROFILES,
    SUPPORTED_BACKEND_TYPES,
    profile_for_backend,
    profile_for_backend_type,
    query_dialect,
    query_policy,
    require_profile_for_backend_type,
    timestamp_is_instant,
)
from marivo.datasource.engines.aggregate_policy import (
    aggregate_operation,
    aggregate_repair_action,
    native_aggregate,
)
from marivo.datasource.engines.calendar_relations import (
    calendar_data,
    calendar_range,
)
from marivo.datasource.engines.query import KeyRows
from marivo.datasource.engines.temporal_lowering import (
    governed_temporal_operation,
    needs_temporal_lowering,
)
from marivo.datasource.engines.temporal_lowering import (
    lower_temporal as lower_native_temporal,
)
from marivo.datasource.engines.temporal_parser import (
    governed_parse_operation,
    parse_strptime,
    translated_strptime_format,
)
from marivo.datasource.errors import _backend_failure_summary as backend_failure_summary
from marivo.datasource.inspection.inspection import (
    _inspect_in_project as inspect_in_project,
)
from marivo.datasource.inspection.inspection import (
    _inspect_resolved_source as inspect_resolved_source,
)
from marivo.datasource.inspection.manage import (
    test_no_persist,
)
from marivo.datasource.inspection.projection import _ibis_table_type as ibis_table_type
from marivo.datasource.inspection.source import SourceInspection
from marivo.datasource.inspection.source import _preflight_scope as preflight_scope
from marivo.datasource.ir import (
    AiContextIR,
    CsvSourceIR,
    DatasourceIR,
    DatasourceSourceLocation,
    EntitySourceIR,
    JsonBodyParam,
    JsonQueryParamValue,
    JsonSourceIR,
    ParquetSourceIR,
    QueryParamScalar,
    QueryParamScalarList,
    SourceParamIR,
    TableSourceIR,
    json_body_to_string,
    json_source_param_names,
    source_name,
    source_to_dict,
)
from marivo.datasource.runtime.adapters import SourceSession
from marivo.datasource.runtime.connections import (
    DatasourceConnectionConfig,
    DatasourceConnectionService,
    load_datasource,
)
from marivo.datasource.runtime.failure_classification import source_unavailability
from marivo.datasource.runtime.json_source import (
    JsonResponseCache,
    normalize_json_source_params,
    read_json_source,
)
from marivo.datasource.runtime.preview import (
    METRIC_PREVIEW_SAMPLE_SIZE,
    PREVIEW_DEFAULT_LIMIT,
    PreviewExecutionLimits,
    PreviewLimitError,
    PreviewResult,
    PreviewSamplePolicy,
    PreviewWarning,
    preview_from_pandas,
    preview_ibis_table,
    validate_preview_limit,
)
from marivo.datasource.runtime.read_types import (
    DURATION_UNIT_METADATA_KEY,
    TIMESTAMP_UNIT_METADATA_KEY,
    BoundSource,
    CompiledRead,
    PhysicalRequirement,
    QualifiedSource,
)
from marivo.datasource.runtime.session_factory import probe_backend
from marivo.datasource.runtime.table_source import (
    TableLookupBackend,
    table_source_expression,
)
from marivo.datasource.source import (
    AuthoringScope,
    PartitionScope,
    UnprunedScope,
)
from marivo.datasource.source import _apply_scope as apply_scope
from marivo.datasource.store.authoring import (
    ClickHouseSpec,
    DatasourceSpec,
    DuckDBSpec,
    MySQLSpec,
    PostgresSpec,
    TrinoSpec,
)
from marivo.datasource.store.builtin import (
    DEFAULT_DATASOURCE_DESCRIPTION,
    DEFAULT_DATASOURCE_NAME,
    default_datasource,
)
from marivo.datasource.store.loader import (
    _load_models_datasources as load_models_datasources,
)
from marivo.datasource.store.loader import (
    _models_root_errors as models_root_errors,
)
from marivo.datasource.temporal_primitives import (
    TemporalExpressionError,
    localize,
    render,
    timestamp,
)

__all__ = [
    "DEFAULT_BACKEND_TYPE",
    "DEFAULT_DATASOURCE_DESCRIPTION",
    "DEFAULT_DATASOURCE_NAME",
    "DURATION_UNIT_METADATA_KEY",
    "ENGINE_PROFILES",
    "METRIC_PREVIEW_SAMPLE_SIZE",
    "PREVIEW_DEFAULT_LIMIT",
    "SUPPORTED_BACKEND_TYPES",
    "TIMESTAMP_UNIT_METADATA_KEY",
    "AiContextIR",
    "AuthoringScope",
    "BoundSource",
    "ClickHouseSpec",
    "CompiledRead",
    "CsvSourceIR",
    "DatasourceConnectionConfig",
    "DatasourceConnectionService",
    "DatasourceIR",
    "DatasourceSourceLocation",
    "DatasourceSpec",
    "DuckDBSecret",
    "DuckDBSpec",
    "EntitySourceIR",
    "EnvRef",
    "JsonBodyParam",
    "JsonQueryParamValue",
    "JsonResponseCache",
    "JsonSourceIR",
    "KeyRows",
    "MySQLSpec",
    "ParquetSourceIR",
    "PartitionScope",
    "PhysicalRequirement",
    "PostgresSpec",
    "PreviewExecutionLimits",
    "PreviewLimitError",
    "PreviewResult",
    "PreviewSamplePolicy",
    "PreviewWarning",
    "QualifiedSource",
    "QueryParamScalar",
    "QueryParamScalarList",
    "SourceInspection",
    "SourceParamIR",
    "SourceSession",
    "TableLookupBackend",
    "TableSourceIR",
    "TemporalExpressionError",
    "TrinoSpec",
    "UnprunedScope",
    "aggregate_operation",
    "aggregate_repair_action",
    "apply_scope",
    "backend_errors",
    "backend_failure_summary",
    "bind_resolver",
    "calendar_data",
    "calendar_range",
    "check_binding",
    "credential_labels",
    "current_resolver",
    "default_datasource",
    "env",
    "governed_parse_operation",
    "governed_temporal_operation",
    "http_bearer_secret",
    "http_headers_secret",
    "ibis_table_type",
    "injected_values",
    "inspect_in_project",
    "inspect_resolved_source",
    "json_body_to_string",
    "json_source_param_names",
    "load_datasource",
    "load_models_datasources",
    "localize",
    "lower_native_temporal",
    "models_root_errors",
    "native_aggregate",
    "needs_temporal_lowering",
    "normalize_json_source_params",
    "operation_context",
    "parse_strptime",
    "physical_fields",
    "preflight_scope",
    "preview_from_pandas",
    "preview_ibis_table",
    "probe_backend",
    "profile_for_backend",
    "profile_for_backend_type",
    "query_dialect",
    "query_policy",
    "read_json_source",
    "redact",
    "render",
    "require_profile_for_backend_type",
    "s3_secret",
    "source_name",
    "source_to_dict",
    "source_unavailability",
    "table_source_expression",
    "test_no_persist",
    "timestamp",
    "timestamp_is_instant",
    "translated_strptime_format",
    "validate_preview_limit",
]
