"""Map a project datasource entry to a live ibis backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NoReturn, SupportsIndex

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
from marivo.datasource.errors import DatasourceConnectionError, DatasourceFieldInvalidError, repair
from marivo.datasource.ir import DatasourceIR


@dataclass(frozen=True)
class EffectiveDatasourceKwargs:
    kwargs: dict[str, Any] = field(repr=False)
    injected: tuple[cr.SecretValue, ...]
    env_sourced_secrets: tuple[secrets.ResolvedSecret, ...]


def _reject_unqualified_http_auth(datasource: DatasourceIR, profile: Any) -> None:
    if (
        any(
            stem == "http_bearer_token" or stem.startswith("http_header:")
            for stem in datasource.env_refs
        )
        and getattr(profile, "http_credentials", None) is None
    ):
        raise DatasourceFieldInvalidError(
            message=(
                "Authenticated HTTP sources require a provider that owns scoped HTTP credentials."
            ),
            expected="an unauthenticated HTTP source or a credentials-owning provider",
            received=f"authenticated HTTP datasource on backend {datasource.backend_type!r}",
            location=f"datasource {datasource.name!r}",
            repair=repair(
                kind="reauthor",
                canonical_id="duckdb",
                action=(
                    "Use an unauthenticated source, switch to a provider that owns "
                    "scoped HTTP credentials, or stage authenticated data upstream."
                ),
            ),
        )


def _effective_kwargs(datasource: DatasourceIR, profile: Any) -> EffectiveDatasourceKwargs:
    _reject_unqualified_http_auth(datasource, profile)
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


def build_backend_with_secrets(
    datasource: DatasourceIR,
    *,
    read_only: bool = False,
    terminal_timeout_seconds: int | None = None,
) -> BuiltDatasourceBackend:
    """Open an ibis backend and return any env-sourced secret provenance."""
    profile = require_profile_for_backend_type(datasource.backend_type)
    effective = _effective_kwargs(datasource, profile)
    return _build_backend_from_effective(
        datasource,
        effective,
        read_only=read_only,
        terminal_timeout_seconds=terminal_timeout_seconds,
    )


def _build_backend_from_effective(
    datasource: DatasourceIR,
    effective: EffectiveDatasourceKwargs,
    *,
    read_only: bool = False,
    terminal_timeout_seconds: int | None = None,
) -> BuiltDatasourceBackend:
    """Open from already resolved operation-local credentials without resolving twice."""
    profile = require_profile_for_backend_type(datasource.backend_type)
    _reject_unqualified_http_auth(datasource, profile)
    kwargs = dict(effective.kwargs)
    http_scope: object = None
    http_bearer_token: object = None
    http_headers: dict[str, object] = {}
    if datasource.backend_type == "duckdb":
        http_scope = kwargs.pop("http_scope", None)
        http_bearer_token = kwargs.pop("http_bearer_token", None)
        for key in tuple(kwargs):
            if key.startswith("http_header:"):
                http_headers[key.removeprefix("http_header:")] = kwargs.pop(key)
    if terminal_timeout_seconds is not None:
        if datasource.backend_type == "postgres":
            kwargs["autocommit"] = False
            kwargs["options"] = (
                f"-c statement_timeout={terminal_timeout_seconds * 1000} -c TimeZone=UTC"
            )
        elif datasource.backend_type == "trino":
            properties = kwargs.get("session_properties")
            kwargs["session_properties"] = {
                **(properties if isinstance(properties, dict) else {}),
                "query_max_run_time": f"{terminal_timeout_seconds}s",
            }
    if datasource.backend_type == "trino":
        kwargs["timezone"] = "UTC"
    if read_only:
        kwargs = profile.apply_read_only_kwargs(kwargs)
    try:
        backend = profile.connect(datasource.name, kwargs)
    except ImportError as exc:
        raise DatasourceConnectionError(
            message="The selected datasource driver could not be imported.",
            expected=f"installed optional dependencies for {profile.name}",
            received=exc.name or type(exc).__name__,
            location=f"datasource {datasource.name}",
            repair=repair(
                kind="configure",
                canonical_id="register",
                action=f"Install marivo[{profile.name}] in the active Python environment and retry the connection.",
            ),
        ) from exc
    except Exception as exc:
        if profile.connection_conflict(exc):
            raise DatasourceConnectionError(
                message="An existing DuckDB connection uses a different configuration.",
                expected="matching connection settings for one database",
                received="conflicting live connection settings",
                location=f"datasource {datasource.name!r}",
                repair=repair(
                    kind="reconnect",
                    canonical_id="test",
                    action="Close the existing connections and retry with consistent read-only settings.",
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
    try:
        if datasource.backend_type == "duckdb":
            install_credentials = profile.http_credentials
            if install_credentials is not None:
                credentials = install_credentials(
                    backend,
                    scope=http_scope,
                    bearer_token=http_bearer_token,
                    headers=http_headers or None,
                )
                if credentials is not None:
                    backend._marivo_duckdb_http_auth = credentials
        if terminal_timeout_seconds is not None:
            if datasource.backend_type == "mysql":
                control_kwargs = {
                    **kwargs,
                    "connect_timeout": 1,
                    "read_timeout": 1,
                    "write_timeout": 1,
                }
                control = profile.connect(datasource.name, control_kwargs)
                backend._marivo_authoring_cancel_control = control
                backend._marivo_authoring_thread_id = backend.con.thread_id()
                backend._marivo_terminal_timeout_seconds = terminal_timeout_seconds
            if datasource.backend_type == "postgres":
                backend.con.read_only = True
            if datasource.backend_type in {"postgres", "trino"}:
                backend._marivo_terminal_timeout_seconds = terminal_timeout_seconds
    except BaseException as exc:
        built.disconnect()
        if effective.injected and isinstance(exc, Exception):
            raise cr.connection_error(exc, effective.injected) from None
        raise
    try:
        cr.remember_injected(backend, effective.injected)
        secrets.remember_env_sourced(backend, effective.env_sourced_secrets)
        with cr.operation_context() as operation:
            if operation.cancelled:
                built.disconnect()
                raise TimeoutError("Datasource connection operation was cancelled.")
    except BaseException:
        built.disconnect()
        raise
    return built


def build_backend(
    datasource: DatasourceIR,
    *,
    read_only: bool = False,
    terminal_timeout_seconds: int | None = None,
) -> Any:
    """Open and return a live ibis backend for the given datasource."""
    return build_backend_with_secrets(
        datasource,
        read_only=read_only,
        terminal_timeout_seconds=terminal_timeout_seconds,
    ).backend
