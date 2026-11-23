"""Operations on an exact leased source connection."""

from contextlib import AbstractContextManager, nullcontext

import ibis.expr.types as ir

from marivo.datasource import credentials as cr
from marivo.datasource.runtime.adapters import SourceSession
from marivo.datasource.runtime.duration_metadata import source_duration_units


def bound_duration_units(session: SourceSession, value: ir.Value) -> tuple[str, ...]:
    return source_duration_units(session.bindings(), value)


def source_native_timeout(session: SourceSession, seconds: int) -> AbstractContextManager[None]:
    timeout = session.provider.native_query_timeout
    return timeout(session._backend, seconds) if timeout is not None else nullcontext()  # noqa: SLF001 - Apply a native timeout to this exact leased connection.


def source_session_closed(session: SourceSession) -> bool:
    return session._closed  # noqa: SLF001 - The connection owner checks whether to open a new lease.


def safe_source_error(session: SourceSession, error: Exception) -> Exception:
    return cr.safe_backend_exception(error, session._backend)  # noqa: SLF001 - Redact with the exact connection's credential scope.
