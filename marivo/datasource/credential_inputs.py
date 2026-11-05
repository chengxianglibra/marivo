"""Immutable credential references and closed DuckDB secret declarations."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import ClassVar, Literal
from urllib.parse import urlsplit

from marivo.datasource.errors import DatasourceFieldInvalidError, repair
from marivo.render import Card, RenderableResult


def invalid(field: str, expected: str, value: object, action: str) -> DatasourceFieldInvalidError:
    return DatasourceFieldInvalidError(
        message=f"Invalid credential input {field!r}.",
        expected=expected,
        received=type(value).__name__,
        location=f"datasource credential field {field!r}",
        repair=repair(kind="reauthor", canonical_id="env", action=action),
    )


@dataclass(frozen=True, repr=False)
class EnvRef(RenderableResult):
    """Immutable credential lookup name; construct with :func:`env`."""

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise invalid("name", "a non-empty credential name", self.name, 'Use md.env("NAME").')

    def _repr_identity(self) -> str:
        return f"EnvRef name={self.name!r}"

    def _card(self) -> Card:
        return Card(identity=self._repr_identity(), available=(".name", ".show()")).field(
            "name", self.name
        )


def env(name: str) -> EnvRef:
    """Reference a credential without capturing its value.

    Args:
        name: Non-empty environment variable name, or host resolver reference.

    Returns:
        An immutable EnvRef, resolved only when opening a connection.

    Example:
        >>> import marivo.datasource as md
        >>> ref = md.env("PG_PASSWORD")
    """
    return EnvRef(name)


def require_env(value: object, field: str) -> EnvRef:
    if not isinstance(value, EnvRef):
        raise invalid(field, "an EnvRef", value, f'Use {field}=md.env("NAME").')
    return value


class _Secret(RenderableResult):
    type: ClassVar[str]
    scope: str

    def _repr_identity(self) -> str:
        return f"{type(self).__name__} type={self.type} scope={self.scope!r} refs={len(secret_refs(self))}"

    def _card(self) -> Card:
        return (
            Card(identity=self._repr_identity(), available=(".scope", ".show()"))
            .field("type", self.type)
            .field("scope", self.scope)
            .field("references", str(len(secret_refs(self))))
        )

    def _validate_scope(self) -> None:
        if not isinstance(self.scope, str):
            raise invalid("scope", "a URL prefix", self.scope, "Declare a scoped URL prefix.")
        try:
            url = urlsplit(self.scope)
            schemes = ("s3",) if self.type == "s3" else ("http", "https")
            port = url.port
            valid = (
                bool(url.hostname)
                and self.scope.lower().startswith(tuple(f"{scheme}://" for scheme in schemes))
                and url.username is None
                and "?" not in self.scope
                and "#" not in self.scope
                and (self.type != "s3" or port is None)
                and not any(character.isspace() or ord(character) < 32 for character in self.scope)
            )
        except ValueError:
            valid = False
        if not valid:
            raise invalid(
                "scope",
                f"a {self.type} URL prefix with a host or bucket",
                self.scope,
                "Use an S3 or HTTP(S) scope with a host and path and without user info, query, or fragment.",
            )


@dataclass(frozen=True, kw_only=True, repr=False)
class S3Secret(_Secret):
    """Scoped static S3 credentials; construct with :func:`s3_secret`."""

    type: ClassVar[str] = "s3"
    scope: str
    region: str
    key_id: EnvRef
    secret: EnvRef
    session_token: EnvRef | None = None
    endpoint: str | None = None
    url_style: Literal["vhost", "path"] | None = None
    use_ssl: bool | None = None

    def __post_init__(self) -> None:
        self._validate_scope()
        for name in ("region", "endpoint"):
            value = getattr(self, name)
            if name == "endpoint" and value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise invalid(name, "a non-empty string", value, f"Supply {name} explicitly.")
        require_env(self.key_id, "key_id")
        require_env(self.secret, "secret")
        if self.session_token is not None:
            require_env(self.session_token, "session_token")
        if self.url_style not in (None, "vhost", "path"):
            raise invalid(
                "url_style", "vhost or path", self.url_style, 'Use url_style="path" or "vhost".'
            )
        if self.use_ssl is not None and not isinstance(self.use_ssl, bool):
            raise invalid("use_ssl", "bool or None", self.use_ssl, "Use use_ssl=True or False.")


@dataclass(frozen=True, kw_only=True, repr=False)
class HttpBearerSecret(_Secret):
    """Scoped HTTP bearer reference; construct with :func:`http_bearer_secret`."""

    type: ClassVar[str] = "http"
    scope: str
    token: EnvRef

    def __post_init__(self) -> None:
        self._validate_scope()
        require_env(self.token, "token")


@dataclass(frozen=True, kw_only=True, repr=False)
class HttpHeadersSecret(_Secret):
    """Scoped HTTP header references; construct with :func:`http_headers_secret`."""

    type: ClassVar[str] = "http"
    scope: str
    headers: Mapping[str, EnvRef]

    def __post_init__(self) -> None:
        self._validate_scope()
        if not isinstance(self.headers, Mapping) or not self.headers:
            raise invalid(
                "headers",
                "a non-empty mapping of header names to EnvRef",
                self.headers,
                'Use headers={"x-api-key": md.env("API_KEY")}.',
            )
        for name, value in self.headers.items():
            if not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", name):
                raise invalid(
                    "headers", "valid HTTP header names", name, "Use a non-empty HTTP header token."
                )
            require_env(value, f"headers[{name!r}]")
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


DuckDBSecret = S3Secret | HttpBearerSecret | HttpHeadersSecret


def s3_secret(
    *,
    scope: str,
    region: str,
    key_id: EnvRef,
    secret: EnvRef,
    session_token: EnvRef | None = None,
    endpoint: str | None = None,
    url_style: Literal["vhost", "path"] | None = None,
    use_ssl: bool | None = None,
) -> S3Secret:
    """Declare a static, scoped S3 secret for ``duckdb(secrets=[...])``.

    Args:
        scope: s3:// URL prefix containing a bucket.
        region: Required AWS region.
        key_id: Access key reference from md.env.
        secret: Secret access key reference from md.env.
        session_token: Optional temporary session token reference.
        endpoint: Host and optional port; defaults to s3.amazonaws.com.
        url_style: vhost (default) or path.
        use_ssl: Whether to use HTTPS; defaults to True.

    Returns:
        An immutable S3Secret. Values resolve at connection time; no SDK chain.

    Example:
        >>> import marivo.datasource as md
        >>> secret = md.s3_secret(scope="s3://lake/", region="us-east-1",
        ...     key_id=md.env("S3_KEY"), secret=md.env("S3_SECRET"))
    """
    return S3Secret(
        scope=scope,
        region=region,
        key_id=key_id,
        secret=secret,
        session_token=session_token,
        endpoint=endpoint,
        url_style=url_style,
        use_ssl=use_ssl,
    )


def http_bearer_secret(*, scope: str, token: EnvRef) -> HttpBearerSecret:
    """Declare scoped HTTP bearer authentication.

    Args:
        scope: HTTP(S) URL prefix limiting credential use.
        token: Bearer token reference from md.env; literal secrets are rejected.

    Returns:
        An immutable HttpBearerSecret for duckdb(secrets=[...]).

    Example:
        >>> import marivo.datasource as md
        >>> secret = md.http_bearer_secret(scope="https://api.example/", token=md.env("TOKEN"))
    """
    return HttpBearerSecret(scope=scope, token=token)


def http_headers_secret(*, scope: str, headers: Mapping[str, EnvRef]) -> HttpHeadersSecret:
    """Declare scoped custom HTTP authentication headers.

    Args:
        scope: HTTP(S) URL prefix limiting credential use.
        headers: Non-empty mapping of header tokens to md.env references.

    Returns:
        An immutable HttpHeadersSecret. A scope cannot also declare bearer auth.

    Example:
        >>> import marivo.datasource as md
        >>> secret = md.http_headers_secret(scope="https://api.example/",
        ...     headers={"x-api-key": md.env("API_KEY")})
    """
    return HttpHeadersSecret(scope=scope, headers=headers)


def secret_refs(secret: _Secret) -> dict[str, str]:
    if isinstance(secret, S3Secret):
        refs = {"key_id": secret.key_id.name, "secret": secret.secret.name}
        if secret.session_token is not None:
            refs["session_token"] = secret.session_token.name
    elif isinstance(secret, HttpBearerSecret):
        refs = {"token": secret.token.name}
    elif isinstance(secret, HttpHeadersSecret):
        refs = {f"header:{name}": ref.name for name, ref in sorted(secret.headers.items())}
    else:
        raise TypeError("Unsupported secret variant")
    return {f"{secret.type}@{secret.scope}.{key}": value for key, value in refs.items()}


def secret_metadata(secret: DuckDBSecret) -> dict[str, object]:
    result: dict[str, object] = {"type": secret.type, "scope": secret.scope}
    if isinstance(secret, S3Secret):
        result.update(
            region=secret.region,
            endpoint=secret.endpoint,
            url_style=secret.url_style,
            use_ssl=secret.use_ssl,
            session_token=secret.session_token is not None,
        )
    elif isinstance(secret, HttpHeadersSecret):
        result.update(headers=sorted(secret.headers))
    return result


def normalize_secrets(value: Sequence[DuckDBSecret]) -> tuple[DuckDBSecret, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise invalid(
            "secrets",
            "a sequence of secret variants",
            value,
            "Use secrets=[md.s3_secret(...)].",
        )
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, (S3Secret, HttpBearerSecret, HttpHeadersSecret)):
            raise invalid(
                "secrets",
                "S3Secret, HttpBearerSecret or HttpHeadersSecret",
                item,
                "Use the md secret constructors.",
            )
        identity = (item.type, item.scope)
        if identity in seen:
            raise invalid(
                "secrets",
                "unique scopes per secret type",
                item,
                "Keep one secret per (type, scope); bearer and headers share the HTTP type.",
            )
        seen.add(identity)
    return tuple(sorted(value, key=lambda item: (item.type, item.scope)))


def credential_labels(fields: Mapping[str, object], refs: Mapping[str, str]) -> list[str]:
    metadata = fields.get("secrets")
    if isinstance(metadata, list):
        return [f"{item['type']}@{item['scope']}" for item in metadata if isinstance(item, dict)]
    return sorted(refs)


def secret_literal(secret: DuckDBSecret) -> str:
    constructor = {
        S3Secret: "s3_secret",
        HttpBearerSecret: "http_bearer_secret",
        HttpHeadersSecret: "http_headers_secret",
    }[type(secret)]

    def literal(value: object) -> str:
        if isinstance(value, EnvRef):
            return f"md.env({value.name!r})"
        if isinstance(value, Mapping):
            return "{" + ", ".join(f"{key!r}: {literal(ref)}" for key, ref in value.items()) + "}"
        return repr(value)

    return (
        f"md.{constructor}("
        + ", ".join(
            f"{f.name}={literal(getattr(secret, f.name))}"
            for f in fields(secret)
            if getattr(secret, f.name) is not None
        )
        + ")"
    )
