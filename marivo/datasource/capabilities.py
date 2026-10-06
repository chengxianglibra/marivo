"""Provider-owned capability channel: registered fixed statements and scoped HTTP credentials.

This module is the 2026-09-28 user-approved internal-SQL exception recorded in the
R0 SQL ledger R1.6 overlay. Providers register fixed statement templates here; the
channel renders them with strictly quoted literals or provider-quoted identifiers and
submits the rendered text through the backend's native raw SQL transport. Statement
text is pinned by a catalog snapshot test, and every submission is recorded on the
backend for auditing. Credential values never appear in rendered text. Bound
parameters also carry the exact ClickHouse owned-query cancellation identity.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, runtime_checkable

from marivo.datasource.errors import DatasourceSourceCapabilityError, repair

if TYPE_CHECKING:
    from ibis.backends import BaseBackend

    from marivo.datasource.engines.base import EngineProfile

ProviderName: TypeAlias = str
StatementPurpose: TypeAlias = str


@dataclass(frozen=True)
class ProviderStatement:
    """One registered fixed statement owned by a provider."""

    statement_id: str
    template: str
    literal_slots: frozenset[str] = frozenset()
    identifier_slots: frozenset[str] = frozenset()
    parameterized: bool = False
    allowed_purposes: frozenset[str] = frozenset()
    integer_ranges: tuple[tuple[str, int, int], ...] = ()

    def __post_init__(self) -> None:
        if not self.statement_id or "." not in self.statement_id:
            raise ValueError("statement_id must be a non-empty '<provider>.<name>' key")
        if self.parameterized and (self.literal_slots or self.identifier_slots):
            raise ValueError("parameterized statements carry no render slots")
        slots = self.literal_slots | self.identifier_slots
        for slot, lower, upper in self.integer_ranges:
            if slot not in self.literal_slots or lower > upper:
                raise ValueError(
                    "integer ranges require a declared literal slot and ordered bounds"
                )
        for slot in slots:
            if "{" + slot + "}" not in self.template:
                raise ValueError(f"template is missing the {slot!r} slot")


@dataclass
class ProviderStatementSubmission:
    """Audit record for one channel submission attached to a live backend.

    Credential values are never recorded here: parameterized submissions keep
    the fixed statement text only.
    """

    provider: ProviderName
    statement_id: str
    purpose: StatementPurpose
    sql: str
    state: Literal["submitted", "succeeded", "failed"]
    failure_summary: str | None = None


@runtime_checkable
class ProviderHttpCredentials(Protocol):
    """Scoped HTTP credentials attached to a provider connection."""

    @property
    def scope(self) -> str: ...

    def headers_for(self, url: str) -> dict[str, str]: ...


_REGISTRY: dict[ProviderName, dict[str, ProviderStatement]] = {}


def register_provider_statements(
    provider: ProviderName, statements: Mapping[str, ProviderStatement]
) -> None:
    """Register one provider's fixed statements; duplicate ids are rejected."""
    owned = _REGISTRY.setdefault(provider, {})
    for name, statement in statements.items():
        expected_id = f"{provider}.{name}"
        if statement.statement_id != expected_id:
            raise ValueError(
                f"statement {name!r} declares id {statement.statement_id!r}; "
                f"expected {expected_id!r}"
            )
        if statement.statement_id in owned:
            raise ValueError(f"duplicate provider statement {statement.statement_id!r}")
        owned[statement.statement_id] = statement


def provider_statement(provider: ProviderName, statement_id: str) -> ProviderStatement:
    """Resolve one registered statement or raise a structured capability error."""
    owned = _REGISTRY.get(provider)
    statement = owned.get(statement_id) if owned is not None else None
    if statement is None:
        raise DatasourceSourceCapabilityError(
            message=(
                f"provider {provider!r} has no registered statement {statement_id!r}; "
                "the capability channel is closed to unregistered SQL."
            ),
            expected=f"a registered {provider} provider statement",
            received=statement_id,
            location="datasource capability channel",
            repair=repair(
                kind="configure",
                canonical_id="register",
                action=(
                    "Register the statement in the provider's engine module or use "
                    "a governed Ibis expression."
                ),
            ),
        )
    return statement


def provider_statement_catalog() -> Mapping[ProviderName, Mapping[str, ProviderStatement]]:
    """Return the frozen registration view used by the snapshot pinning test."""
    return {provider: dict(owned) for provider, owned in sorted(_REGISTRY.items())}


def _quote_literal(value: object) -> str:
    text = str(value)
    return "'" + text.replace("'", "''") + "'"


def render_provider_statement(
    statement: ProviderStatement,
    profile: EngineProfile,
    *,
    values: Mapping[str, object] = {},
    identifiers: Mapping[str, str | tuple[str, ...]] = {},
) -> str:
    """Render a fixed template with strictly quoted literals and identifiers."""
    supplied_literals = set(values)
    supplied_identifiers = set(identifiers)
    if supplied_literals & supplied_identifiers:
        overlap = sorted(supplied_literals & supplied_identifiers)
        raise ValueError(f"slots supplied as both literal and identifier: {overlap}")
    expected = statement.literal_slots | statement.identifier_slots
    supplied = supplied_literals | supplied_identifiers
    missing = sorted(expected - supplied)
    extra = sorted(supplied - expected)
    if missing or extra:
        raise ValueError(
            f"statement {statement.statement_id!r} slot mismatch: "
            f"missing={missing}, unexpected={extra}"
        )
    if (
        supplied_literals != statement.literal_slots
        or supplied_identifiers != statement.identifier_slots
    ):
        raise ValueError("statement slots must use their declared literal or identifier kind")
    if statement.parameterized:
        return statement.template
    rendered: dict[str, str] = {}
    for slot, lower, upper in statement.integer_ranges:
        value = values[slot]
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(
                f"statement {statement.statement_id!r} requires {slot} in [{lower}, {upper}]"
            )
    for slot, value in values.items():
        rendered[slot] = (
            str(value)
            if slot in {item[0] for item in statement.integer_ranges}
            else _quote_literal(value)
        )
    for slot, value in identifiers.items():
        parts = value if isinstance(value, tuple) else (value,)
        quote = profile.identifier_quote
        escaped = (str(part).replace(quote, quote + quote) for part in parts)
        rendered[slot] = ".".join(f"{quote}{part}{quote}" for part in escaped)
    return re.sub(r"\{([^{}]+)\}", lambda match: rendered[match.group(1)], statement.template)


def _submissions(backend: BaseBackend) -> list[ProviderStatementSubmission]:
    log = getattr(backend, "_marivo_provider_submissions", None)
    if log is None:
        log = []
        backend._marivo_provider_submissions = log
    return log


def provider_statement_log(backend: BaseBackend) -> tuple[ProviderStatementSubmission, ...]:
    """Return the channel submissions recorded on one live backend."""
    return tuple(getattr(backend, "_marivo_provider_submissions", ()))


def execute_provider_statement(
    backend: BaseBackend,
    profile: EngineProfile,
    statement_id: str,
    *,
    values: Mapping[str, object] = {},
    identifiers: Mapping[str, str | tuple[str, ...]] = {},
    purpose: StatementPurpose,
    parameters: Sequence[object] | Mapping[str, str] | None = None,
) -> tuple[dict[str, object], ...]:
    """Submit one registered statement through the backend's native transport.

    Metadata text goes to ``backend.raw_sql(sql)``. Credential installs pass
    parameters separately and audit only the template and sanitized status.
    Per-fact metadata failures are expected to be caught by the caller.
    """
    from marivo.datasource.engines.base import decode_cursor_frame
    from marivo.datasource.errors import _backend_failure_summary

    statement = provider_statement(profile.name, statement_id)
    if purpose not in statement.allowed_purposes:
        raise DatasourceSourceCapabilityError(
            message="The provider statement cannot execute for this purpose.",
            expected=str(sorted(statement.allowed_purposes)),
            received=purpose,
            location="datasource capability channel",
            repair=repair(
                kind="configure",
                canonical_id="register",
                action="Use an authorized statement purpose.",
            ),
        )
    if statement.parameterized != (parameters is not None):
        raise ValueError("statement parameters must match the registered parameterized mode")
    if statement_id == "clickhouse.analysis.cancel_owned_query" and (
        not isinstance(parameters, Mapping)
        or set(parameters) != {"id", "user"}
        or any(type(value) is not str or not value for value in parameters.values())
    ):
        raise ValueError("ClickHouse cancellation requires bound nonempty id and user strings")
    sql = render_provider_statement(statement, profile, values=values, identifiers=identifiers)
    submission = ProviderStatementSubmission(
        provider=profile.name,
        statement_id=statement_id,
        purpose=purpose,
        sql=sql,
        state="submitted",
    )
    log = _submissions(backend)
    log.append(submission)
    try:
        cursor = (
            backend.raw_sql(sql, parameters=parameters)
            if parameters is not None
            else backend.raw_sql(sql)
        )
        # DuckDB raw_sql returns a connection-local result handle whose close()
        # would sever the shared connection; never close it here. Other drivers
        # get a best-effort DB-API cursor close.
        connection_local = getattr(backend, "name", None) == "duckdb"
        try:
            frame = decode_cursor_frame(cursor, include_types=False, max_rows=None)
        finally:
            if not connection_local:
                close = getattr(cursor, "close", None)
                if callable(close):
                    close()
    except Exception as exc:
        submission.state = "failed"
        submission.failure_summary = (
            "Parameterized provider statement failed"
            if statement.parameterized
            else _backend_failure_summary(exc).message
        )
        raise
    submission.state = "succeeded"
    return frame.rows


def url_is_in_http_scope(url: str, scope: str) -> bool:
    """Return True only when *url* stays inside the declared host/path scope."""
    from urllib.parse import urlsplit

    candidate = urlsplit(url)
    configured = urlsplit(scope)
    if candidate.scheme.lower() != configured.scheme.lower():
        return False
    if candidate.netloc.lower() != configured.netloc.lower():
        return False
    scope_path = configured.path.rstrip("/")
    return candidate.path == scope_path or candidate.path.startswith(f"{scope_path}/")


def json_http_headers(backend: BaseBackend, url: str) -> dict[str, str]:
    """Return datasource-owned headers only when a URL is inside its scope."""
    auth = getattr(backend, "_marivo_duckdb_http_auth", None)
    if not isinstance(auth, ProviderHttpCredentials):
        return {}
    return auth.headers_for(url)
