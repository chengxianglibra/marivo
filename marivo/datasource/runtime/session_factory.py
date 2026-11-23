"""Open and probe owned datasource connections."""

from __future__ import annotations

import ibis
import pandas as pd
from ibis.backends import BaseBackend

from marivo._kernel import execution_log as _execution_log
from marivo.datasource import credentials as cr
from marivo.datasource.engines.base import EngineProfile
from marivo.datasource.errors import (
    DatasourceBackendTypeUnsupportedError,
    _execution_error_fields,
    repair,
)
from marivo.datasource.ir import DatasourceIR
from marivo.datasource.runtime.adapters import SourceSession, _invalid
from marivo.datasource.runtime.connections import (
    DEFAULT_CONNECTION_TIMEOUT_SECONDS,
    open_backend,
)


def open_source_session(
    profile: EngineProfile,
    datasource: DatasourceIR,
    *,
    terminal_timeout_seconds: int | None = None,
) -> SourceSession:
    """Open the selected provider's owned source session."""
    if datasource.backend_type != profile.name:
        raise DatasourceBackendTypeUnsupportedError(
            message="Selected datasource provider differs from the declaration.",
            expected=profile.name,
            received=datasource.backend_type,
            location="datasource adapter",
            repair=repair(
                kind="configure",
                canonical_id="register",
                action="Select the provider named by the datasource declaration.",
            ),
        )
    selected = open_backend(
        datasource,
        timeout_seconds=DEFAULT_CONNECTION_TIMEOUT_SECONDS
        if terminal_timeout_seconds is not None
        else None,
        terminal_timeout_seconds=terminal_timeout_seconds,
    )
    try:
        return SourceSession(profile, datasource, selected.backend, backend_lease=selected.lease)
    except BaseException:
        selected.disconnect()
        raise


def probe_backend(backend_name: str, backend: BaseBackend) -> None:
    expression = ibis.literal(1).name("probe").as_table()
    sql = backend.compile(expression, limit=None)
    query = _execution_log.QueryLog(
        sql,
        backend=backend_name,
        purpose="datasource.connectivity",
        sensitive=bool(cr.injected_values(backend)),
        format_error=_execution_error_fields,
    )
    try:
        result = backend.execute(expression, params={}, limit=None)
        if (
            not isinstance(result, pd.DataFrame)
            or tuple(result.columns) != ("probe",)
            or len(result) != 1
            or result.iloc[0, 0] != 1
        ):
            raise _invalid("one Ibis literal result equal to 1", repr(result))
        query.rows = len(result)
    except BaseException as error:
        query.error = error
        raise
    finally:
        query.finish("failed" if query.error is not None else "succeeded")
