"""Map a project datasource entry to a live ibis backend."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, NoReturn, SupportsIndex
from urllib.parse import urlsplit

from ibis.backends import BaseBackend

from marivo.datasource import credentials as cr
from marivo.datasource import secrets
from marivo.datasource._lifetime import BackendLease
from marivo.datasource.engines import (
    SUPPORTED_BACKEND_TYPES as SUPPORTED_BACKEND_TYPES,
)
from marivo.datasource.engines import (
    require_profile_for_backend_type,
)
from marivo.datasource.errors import (
    DatasourceConnectionError,
    DatasourceFieldInvalidError,
    DatasourceMetadataError,
    repair,
)
from marivo.datasource.ir import DatasourceIR, JsonSourceIR


@dataclass(frozen=True)
class EffectiveDatasourceKwargs:
    kwargs: dict[str, Any] = field(repr=False)
    injected: tuple[cr.SecretValue, ...]
    env_sourced_secrets: tuple[secrets.ResolvedSecret, ...]


def _effective_kwargs(datasource: DatasourceIR) -> EffectiveDatasourceKwargs:
    resolved: dict[str, Any] = dict(datasource.fields)
    env_sourced: list[secrets.ResolvedSecret] = []
    resolver = cr.current_resolver()
    injected: dict[str, cr.SecretValue] = {}
    for stem, env_var in datasource.env_refs.items():
        if not isinstance(env_var, str) or not env_var:
            raise DatasourceFieldInvalidError(
                message=(
                    f"datasource {datasource.name!r} field {stem}_env must be a non-empty "
                    "env var name"
                ),
                expected="a non-empty environment variable name",
                received=repr(env_var),
                location=f"models/datasources/ entry {datasource.name!r} field {stem}_env",
                repair=repair(
                    kind="environment",
                    canonical_id="test",
                    action="Set a non-empty environment variable reference.",
                ),
            )
        if resolver is not None:
            if env_var not in injected:
                with cr.operation_context() as operation:
                    injected[env_var] = cr.resolve_reference(
                        resolver,
                        operation,
                        reference=env_var,
                        datasource=datasource.name,
                        fields=tuple(
                            sorted(
                                key for key, ref in datasource.env_refs.items() if ref == env_var
                            )
                        ),
                    )
            resolved[stem] = injected[env_var].reveal()
            continue
        resolved_secret = secrets.resolve(env_var, datasource=datasource.name, field=stem)
        resolved[stem] = resolved_secret.value
        if isinstance(resolved_secret.provider, secrets.EnvProvider):
            env_sourced.append(resolved_secret)
    return EffectiveDatasourceKwargs(
        kwargs=resolved,
        injected=tuple(injected.values()),
        env_sourced_secrets=tuple(env_sourced),
    )


@dataclass(frozen=True)
class BuiltDatasourceBackend:
    backend: BaseBackend = field(repr=False)
    env_sourced_secrets: tuple[secrets.ResolvedSecret, ...] = field(repr=False)
    injected: tuple[cr.SecretValue, ...] = field(default=(), repr=False)
    thread_affine: bool = field(default=False, repr=False)
    lease: BackendLease = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "lease", BackendLease(self.backend, thread_affine=self.thread_affine)
        )

    def disconnect(self) -> None:
        self.lease.close()

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("Live datasource backends cannot be serialized.")


@dataclass(frozen=True)
class _DuckDBHttpAuth:
    scope: str
    headers: tuple[tuple[str, str], ...] = field(repr=False)


def _configure_duckdb_http_auth(
    backend: object,
    *,
    scope: object,
    bearer_token: object,
    headers: object,
) -> _DuckDBHttpAuth | None:
    if bearer_token is None and not headers:
        return None
    raw_sql = getattr(backend, "raw_sql", None)
    if not callable(raw_sql):
        raise DatasourceFieldInvalidError(
            message="DuckDB HTTP auth requires a backend with raw_sql support",
            expected="a DuckDB backend",
            received=type(backend).__name__,
            location="DuckDB HTTP auth",
            repair=repair(
                kind="reconnect",
                canonical_id="test",
                action="Reconnect using the declared DuckDB datasource.",
            ),
        )
    if not isinstance(scope, str):
        raise DatasourceFieldInvalidError(
            message="DuckDB HTTP auth scope was not resolved",
            expected="an HTTP(S) scope string",
            received=repr(scope),
            location="DuckDB HTTP auth",
            repair=repair(
                kind="reauthor",
                canonical_id="duckdb",
                action="Declare an explicit HTTP(S) scope on the DuckDB datasource.",
            ),
        )
    if isinstance(bearer_token, str):
        raw_sql(
            "CREATE OR REPLACE SECRET marivo_http_auth (TYPE HTTP, BEARER_TOKEN ?, SCOPE ?)",
            parameters=[bearer_token, scope],
        )
        return _DuckDBHttpAuth(
            scope=scope,
            headers=(("Authorization", f"Bearer {bearer_token}"),),
        )
    if (
        isinstance(headers, dict)
        and headers
        and all(isinstance(name, str) and isinstance(value, str) for name, value in headers.items())
    ):
        raw_sql(
            "CREATE OR REPLACE SECRET marivo_http_auth (TYPE HTTP, EXTRA_HTTP_HEADERS ?, SCOPE ?)",
            parameters=[headers, scope],
        )
        return _DuckDBHttpAuth(scope=scope, headers=tuple(headers.items()))
    raise DatasourceFieldInvalidError(
        message="DuckDB custom HTTP authentication was not fully resolved",
        expected="environment-sourced custom HTTP headers",
        received="incomplete HTTP authentication fields",
        location="DuckDB HTTP auth",
        repair=repair(
            kind="reauthor",
            canonical_id="duckdb",
            action="Declare one complete environment-backed HTTP auth mode.",
        ),
    )


def _url_is_in_http_scope(url: str, scope: str) -> bool:
    candidate = urlsplit(url)
    configured = urlsplit(scope)
    if candidate.scheme.lower() != configured.scheme.lower():
        return False
    if candidate.netloc.lower() != configured.netloc.lower():
        return False
    scope_path = configured.path.rstrip("/")
    return candidate.path == scope_path or candidate.path.startswith(f"{scope_path}/")


def json_http_headers(backend: object, url: str) -> dict[str, str]:
    """Return datasource-owned headers only when a URL is inside its declared scope."""
    auth = getattr(backend, "_marivo_duckdb_http_auth", None)
    if not isinstance(auth, _DuckDBHttpAuth) or not _url_is_in_http_scope(url, auth.scope):
        return {}
    return dict(auth.headers)


_HTTP_SCHEME = re.compile(r"^https?://", re.IGNORECASE)


def apply_json_http_settings(backend: object, source: object) -> None:
    """Enable force_download for http(s) JSON sources; no-op for local paths."""
    if not isinstance(source, JsonSourceIR):
        return
    if not _HTTP_SCHEME.match(source.path):
        return
    raw_sql = getattr(backend, "raw_sql", None)
    if not callable(raw_sql):
        raise DatasourceMetadataError(
            message=(
                f"json source {source.path!r} is http(s), but this datasource "
                "backend cannot read remote JSON. md.json(...) remote GET and "
                "JSON-body POST sources require a DuckDB backend."
            ),
            expected="a DuckDB backend with httpfs support",
            received="backend without raw_sql",
            location=f"md.json({source.path!r})",
            repair=repair(
                kind="reauthor",
                canonical_id="json",
                action="Use a local JSON path or configure a DuckDB datasource.",
                snippet='source = md.json("data/events/*.json", format="newline_delimited")',
            ),
        )
    raw_sql("SET force_download=true")


def build_backend(
    datasource: DatasourceIR,
    *,
    read_only: bool = False,
) -> BuiltDatasourceBackend:
    """Open an ibis backend and return any env-sourced secret provenance."""
    profile = require_profile_for_backend_type(datasource.backend_type)
    effective = _effective_kwargs(datasource)
    kwargs = dict(effective.kwargs)
    http_scope = None
    http_bearer_token = None
    http_headers: dict[str, object] = {}
    if datasource.backend_type == "duckdb":
        http_scope = kwargs.pop("http_scope", None)
        http_bearer_token = kwargs.pop("http_bearer_token", None)
        for key in tuple(kwargs):
            if key.startswith("http_header:"):
                http_headers[key.removeprefix("http_header:")] = kwargs.pop(key)
    if read_only:
        kwargs = profile.apply_read_only_kwargs(kwargs)
    try:
        backend = profile.connect(datasource.name, kwargs)
    except Exception as exc:
        if profile.connection_conflict(exc):
            raise DatasourceConnectionError(
                message="DuckDB already has a connection with an incompatible open mode.",
                expected="a connection compatible with the declared datasource configuration",
                received="conflicting live DuckDB connection",
                location=f"datasource {datasource.name!r}",
                repair=repair(
                    kind="reconnect",
                    canonical_id="connect",
                    action="Close the Session or explicit connection holding this DuckDB file, or use matching declared read_only settings, then retry.",
                ),
            ) from None
        if effective.injected:
            raise cr.connection_error(exc, effective.injected) from None
        raise
    built = BuiltDatasourceBackend(
        backend=backend,
        env_sourced_secrets=effective.env_sourced_secrets,
        injected=effective.injected,
        thread_affine=profile.connection_thread == "caller",
    )
    lease = built.lease
    try:
        if datasource.backend_type == "duckdb":
            http_auth = _configure_duckdb_http_auth(
                backend,
                scope=http_scope,
                bearer_token=http_bearer_token,
                headers=http_headers,
            )
            if http_auth is not None:
                backend._marivo_duckdb_http_auth = http_auth
    except BaseException as exc:
        lease.close()
        if effective.injected and isinstance(exc, Exception):
            raise cr.connection_error(exc, effective.injected) from None
        raise
    try:
        cr.remember_injected(backend, effective.injected)
        secrets.remember_env_sourced(backend, effective.env_sourced_secrets)
        with cr.operation_context() as operation:
            if operation.cancelled:
                lease.close()
                raise TimeoutError("Datasource connection operation was cancelled.")
    except BaseException:
        lease.close()
        raise
    return built
