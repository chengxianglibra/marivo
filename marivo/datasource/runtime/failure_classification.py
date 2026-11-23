"""Categorize retained datasource failures without a running connection."""

from typing import Literal

from marivo.datasource.errors import _BackendFailureSummary


def source_unavailability(
    failure: _BackendFailureSummary,
) -> Literal[
    "permission_denied",
    "unsupported",
    "source_unavailable",
    "timeout",
    "connection_unavailable",
    "unknown",
]:
    trino_error = failure.backend_name if failure.exception_type.startswith("Trino") else None
    if trino_error == "PERMISSION_DENIED" or failure.exception_type == "PermissionError":
        return "permission_denied"
    elif trino_error == "NOT_SUPPORTED":
        return "unsupported"
    elif trino_error in {"TABLE_NOT_FOUND", "SCHEMA_NOT_FOUND", "CATALOG_NOT_FOUND"} or (
        failure.exception_type in {"TableNotFound", "FileNotFoundError"}
    ):
        return "source_unavailable"
    elif failure.exception_type in {"TimeoutError", "DatasourceConnectionTimeoutError"}:
        return "timeout"
    elif failure.exception_type in {
        "ConnectionError",
        "ConnectionAbortedError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "BrokenPipeError",
        "TrinoConnectionError",
    }:
        return "connection_unavailable"
    return "unknown"
