"""Authoring surface for project-level datasources."""

from __future__ import annotations

import inspect
import re
from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import MISSING, Field, dataclass, field, fields
from pathlib import Path
from typing import Any, ClassVar, TypeAlias, cast, get_args, get_origin

from marivo._kernel.refs import DatasourceKind, Ref, SemanticKind, _validate_segment
from marivo._kernel.refs import ref as ref_factory
from marivo._kernel.render import Card, RenderableResult
from marivo._kernel.symbolic import (
    DatasourceSymbol,
    datasource_identity,
    native_symbol_ref,
    symbol_factory,
)
from marivo.datasource.credentials.inputs import (
    DuckDBSecret,
    EnvRef,
    credential_labels,
    invalid,
    normalize_secrets,
    require_env,
    secret_metadata,
    secret_refs,
)
from marivo.datasource.errors import (
    DatasourceFieldInvalidError,
    DatasourceObservedEffects,
    repair,
)
from marivo.datasource.ir import (
    AiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
    _normalized_wall_clock_timezone,
    _validate_connection_options,
)
from marivo.datasource.typing import AiContextValue


def _build_ai_context(ai_context: AiContextValue | None) -> AiContextIR:
    if ai_context is None:
        return AiContextIR()
    if not isinstance(ai_context, AiContextValue):
        raise DatasourceFieldInvalidError(
            message=(
                "ai_context= expects an AiContextValue from ms.ai_context(...), "
                "not a raw dict. Construct it explicitly with "
                "ms.ai_context(business_definition=..., guardrails=[...]). "
                "summary= and other unsupported metadata keys are not accepted."
            ),
            expected="an AiContextValue from ms.ai_context(...)",
            received=type(ai_context).__name__,
            location="datasource ai_context",
            repair=repair(
                kind="reauthor",
                canonical_id="duckdb",
                action="Construct ai_context with ms.ai_context(...).",
            ),
        )
    return AiContextIR(
        business_definition=ai_context.business_definition,
        guardrails=ai_context.guardrails,
        aliases=ai_context.aliases,
    )


SENSITIVE_FIELD_STEMS = frozenset(
    {
        "user",
        "password",
        "token",
        "secret",
        "secret_key",
        "access_key",
        "auth",
        "private_key",
        "passphrase",
        "api_key",
    }
)
JsonValue: TypeAlias = str | int | float | bool | list["JsonValue"] | dict[str, "JsonValue"] | None

_META_FIELDS = frozenset(
    {"name", "ai_context", "extra", "fields", "env_refs", "wall_clock_timezone"}
)

_SPEC_TARGET_FIELDS: dict[str, tuple[str, ...]] = {
    "duckdb": ("path",),
    "trino": ("host", "catalog", "port", "schema", "http_scheme"),
    "mysql": ("host", "database", "port"),
    "postgres": ("host", "database", "port", "schema"),
    "clickhouse": ("host", "port", "database", "secure"),
}


def _description(text: str) -> dict[str, str]:
    return {"description": text}


def _is_field_required(dataclass_field: Field[object]) -> bool:
    return dataclass_field.default is MISSING and dataclass_field.default_factory is MISSING


def _allows_none(annotation: object) -> bool:
    """Check whether a type annotation admits None.

    Handles both resolved type objects and string annotations
    (from ``from __future__ import annotations``).
    """
    if annotation is None or annotation is type(None):
        return True
    if isinstance(annotation, str):
        return "None" in annotation and "|" in annotation
    origin = get_origin(annotation)
    if origin is None:
        return False
    return type(None) in get_args(annotation)


def _is_jsonable_value(value: Any) -> bool:
    if isinstance(value, str | int | float | bool) or value is None:
        return True
    if isinstance(value, list):
        return all(_is_jsonable_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_jsonable_value(item) for key, item in value.items())
    return False


def _normalize_value(value: object) -> JsonValue:
    if isinstance(value, tuple):
        return [_normalize_value(item) for item in value]
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    return cast("JsonValue", value)


def _validate_jsonable_field(name: str, key: str, value: object) -> JsonValue:
    normalized = _normalize_value(value)
    if not _is_jsonable_value(normalized):
        raise DatasourceFieldInvalidError(
            message=(
                f"datasource {name!r} field {key!r} has unsupported value type "
                f"{type(value).__name__}"
            ),
            expected="a JSON-compatible datasource field value",
            received=type(value).__name__,
            location=f"models/datasources/ entry {name!r} field {key!r}",
            repair=repair(
                kind="reauthor",
                canonical_id="duckdb",
                action="Use a JSON-compatible datasource field value.",
            ),
        )
    return normalized


@dataclass
class DatasourceLoaderContext:
    pending_objects: list[DatasourceIR] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True, repr=False)
class _SpecBase(RenderableResult):
    """Shared behavior for concrete datasource specification classes."""

    name: str = field(metadata=_description("Global datasource name; must match [a-z][a-z0-9_]*."))
    ai_context: AiContextValue | None = field(
        default=None,
        metadata=_description(
            "Optional AI-facing datasource context, via ms.ai_context(...). "
            "Put text descriptions in ai_context.business_definition."
        ),
    )
    extra: dict[str, JsonValue] | None = field(
        default=None,
        metadata=_description(
            "Rare JSON-safe ibis keyword arguments not modeled by the typed class."
        ),
    )

    wall_clock_timezone: str | None = field(
        default=None,
        metadata={
            "meaning": (
                "Default source timezone for undeclared naive timestamps and business dates; "
                "does not affect physical instants or connection settings."
            )
        },
    )

    backend_type: ClassVar[str]
    fields: dict[str, JsonValue] = field(init=False)
    env_refs: dict[str, str] = field(init=False)

    @property
    def ref(self) -> DatasourceSymbol:
        """Typed reference to this datasource for semantic authoring."""
        return symbol_factory.datasource(self.name)

    def __post_init__(self) -> None:
        validate_datasource_name(self.name)
        self._validate_required_string_fields()
        object.__setattr__(
            self,
            "wall_clock_timezone",
            _normalized_wall_clock_timezone(
                self.wall_clock_timezone, name=self.name, backend_type=self.backend_type
            ),
        )
        literal_fields, env_refs = self._split_declared_fields()
        _validate_connection_options(self.name, self.backend_type, literal_fields)
        object.__setattr__(self, "fields", literal_fields)
        object.__setattr__(self, "env_refs", env_refs)
        object.__setattr__(self, "ai_context", _build_ai_context(self.ai_context))

    def _repr_identity(self) -> str:
        return f"{type(self).__name__} name={self.name} backend={self.backend_type}"

    def _card(self) -> Card:
        target_fields = _SPEC_TARGET_FIELDS[self.backend_type]
        rendered_target = " | ".join(
            f"{name}={self.fields[name]!r}" for name in target_fields if name in self.fields
        )
        credential_fields = credential_labels(self.fields, self.env_refs)
        additional_fields = set(self.fields).difference(target_fields)
        for dataclass_field in fields(self):
            if dataclass_field.name not in additional_fields:
                continue
            if (
                dataclass_field.default is not MISSING
                and getattr(self, dataclass_field.name) == dataclass_field.default
            ):
                additional_fields.remove(dataclass_field.name)

        card = (
            Card(
                identity=self._repr_identity(),
                available=(".fields", ".env_refs", ".show()"),
            )
            .status("valid declaration")
            .field("ref", ref_factory.datasource(self.name).key)
            .field("target", rendered_target)
        )
        if self.wall_clock_timezone is not None:
            card.field("wall-clock timezone", self.wall_clock_timezone)
        if credential_fields:
            ref_count = len(self.env_refs)
            card.field(
                "credentials",
                f"{' | '.join(credential_fields)} "
                f"({ref_count} {'ref' if ref_count == 1 else 'refs'})",
            )
        if additional_fields:
            field_count = len(additional_fields)
            card.field(
                "additional configuration",
                f"{field_count} {'field' if field_count == 1 else 'fields'}; inspect .fields",
            )
        return card

    def _validate_required_string_fields(self) -> None:
        for dataclass_field in fields(self):
            if dataclass_field.name in _META_FIELDS:
                continue
            if dataclass_field.name in {"user", "password", "auth"}:
                continue
            value = getattr(self, dataclass_field.name)
            if value is None:
                continue
            if _is_field_required(dataclass_field) and isinstance(value, str) and not value:
                raise DatasourceFieldInvalidError(
                    message=(
                        f"datasource {self.name!r} field {dataclass_field.name!r} "
                        "must be a non-empty string"
                    ),
                    expected="a non-empty string",
                    received=repr(value),
                    location=f"models/datasources/ entry {self.name!r} field {dataclass_field.name!r}",
                    repair=repair(
                        kind="reauthor",
                        canonical_id=self.backend_type,
                        action="Provide a non-empty required datasource field.",
                    ),
                )

    def _split_declared_fields(self) -> tuple[dict[str, JsonValue], dict[str, str]]:
        literal_fields: dict[str, JsonValue] = {}
        env_refs: dict[str, str] = {}
        for dataclass_field in fields(self):
            key = dataclass_field.name
            if key in _META_FIELDS:
                continue
            value = getattr(self, key)
            if value is None and _allows_none(dataclass_field.type):
                continue
            if key == "secrets":
                secrets = normalize_secrets(value)
                object.__setattr__(self, "secrets", secrets)
                if secrets:
                    literal_fields[key] = _validate_jsonable_field(
                        self.name, key, [secret_metadata(item) for item in secrets]
                    )
                    for item in secrets:
                        env_refs.update(secret_refs(item))
                continue
            if key in {"user", "password", "auth"}:
                env_refs[key] = require_env(value, key).name
                continue
            literal_fields[key] = _validate_jsonable_field(self.name, key, value)

        if self.extra is not None:
            if not isinstance(self.extra, dict) or not all(  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
                isinstance(key, str) for key in self.extra
            ):
                raise invalid(
                    "extra",
                    "a dictionary with string keys",
                    self.extra,
                    "Use a dictionary of literal backend options with string keys.",
                )
            for key, value in self.extra.items():
                if key == "wall_clock_timezone" or (
                    self.backend_type == "trino" and key == "timezone"
                ):
                    raise DatasourceFieldInvalidError(
                        message=f"datasource {self.name!r} extra cannot contain {key!r}",
                        expected="wall_clock_timezone as a first-class source declaration",
                        received=f"extra[{key!r}]",
                        location=f"datasource {self.name!r} extra",
                        repair=repair(
                            kind="reauthor",
                            canonical_id=self.backend_type,
                            action="Remove this extra option and declare wall_clock_timezone explicitly.",
                        ),
                    )
                if (
                    key.lower() in SENSITIVE_FIELD_STEMS
                    or key
                    in {
                        "secrets",
                        "key_id",
                        "session_token",
                        "http_scope",
                        "http_bearer_token",
                        "http_headers",
                    }
                    or (
                        self.backend_type == "duckdb"
                        and key.lower()
                        in {
                            "s3_access_key_id",
                            "s3_secret_access_key",
                            "s3_session_token",
                            "http_proxy_username",
                            "http_proxy_password",
                        }
                    )
                    or key.endswith("_env")
                    or "@" in key
                    or key.startswith("http_header:")
                ):
                    raise invalid(
                        key,
                        "credentials declared with md.env and secret constructors",
                        value,
                        'Remove credential options from extra; use md.env("NAME") in the corresponding credential field.',
                    )
                literal_fields[key] = _validate_jsonable_field(self.name, key, value)
        return literal_fields, env_refs

    def _to_ir(self, *, location: DatasourceSourceLocation) -> DatasourceIR:
        return DatasourceIR(
            semantic_id=self.name,
            name=self.name,
            backend_type=self.backend_type,
            wall_clock_timezone=self.wall_clock_timezone,
            fields=dict(self.fields),
            env_refs=dict(self.env_refs),
            ai_context=cast("AiContextIR", self.ai_context),
            python_symbol=self.name,
            location=location,
        )


@dataclass(frozen=True, kw_only=True, repr=False)
class DuckDBSpec(_SpecBase):
    """DuckDB datasource specification."""

    backend_type: ClassVar[str] = "duckdb"
    path: str = field(
        default=":memory:", metadata=_description("DuckDB database path or :memory:.")
    )
    read_only: bool = field(
        default=False,
        metadata=_description("Open a DuckDB database file in native read-only mode."),
    )
    secrets: Sequence[DuckDBSecret] = field(
        default=(),
        metadata=_description("Scoped S3 and HTTP secrets containing md.env references."),
    )


@dataclass(frozen=True, kw_only=True, repr=False)
class TrinoSpec(_SpecBase):
    """Trino datasource specification."""

    backend_type: ClassVar[str] = "trino"
    host: str = field(metadata=_description("Trino coordinator host."))
    catalog: str = field(
        metadata=_description("Trino catalog; mapped to ibis database at connect time.")
    )
    user: EnvRef = field(metadata=_description("Required EnvRef from md.env for the Trino user."))
    port: int | None = field(
        default=None, metadata=_description("Trino port; ibis default is 8080.")
    )
    schema: str | None = field(default=None, metadata=_description("Optional default schema."))
    source: str | None = field(
        default=None, metadata=_description("Optional client application/source tag.")
    )
    http_scheme: str | None = field(default=None, metadata=_description("Set to 'https' for TLS."))
    client_tags: tuple[str, ...] | None = field(
        default=None, metadata=_description("Optional Trino client tags.")
    )
    session_properties: dict[str, JsonValue] | None = field(
        default=None, metadata=_description("Optional Trino session properties.")
    )
    auth: EnvRef | None = field(
        default=None,
        metadata=_description("EnvRef from md.env for Trino auth token or password."),
    )


@dataclass(frozen=True, kw_only=True, repr=False)
class MySQLSpec(_SpecBase):
    """MySQL datasource specification."""

    backend_type: ClassVar[str] = "mysql"
    host: str = field(metadata=_description("MySQL host."))
    database: str = field(metadata=_description("MySQL database name."))
    port: int | None = field(
        default=None, metadata=_description("MySQL port; ibis default is 3306.")
    )
    autocommit: bool | None = field(
        default=None, metadata=_description("Use True or omit this option to use autocommit.")
    )
    user: EnvRef | None = field(
        default=None, metadata=_description("EnvRef from md.env for MySQL user.")
    )
    password: EnvRef | None = field(
        default=None, metadata=_description("EnvRef from md.env for MySQL password.")
    )


@dataclass(frozen=True, kw_only=True, repr=False)
class PostgresSpec(_SpecBase):
    """Postgres datasource specification."""

    backend_type: ClassVar[str] = "postgres"
    host: str = field(metadata=_description("Postgres host."))
    database: str = field(metadata=_description("Postgres database name."))
    port: int | None = field(
        default=None, metadata=_description("Postgres port; ibis default is 5432.")
    )
    schema: str | None = field(default=None, metadata=_description("Optional default schema."))
    autocommit: bool | None = field(
        default=None, metadata=_description("Use True or omit this option to use autocommit.")
    )
    user: EnvRef | None = field(
        default=None, metadata=_description("EnvRef from md.env for Postgres user.")
    )
    password: EnvRef | None = field(
        default=None, metadata=_description("EnvRef from md.env for Postgres password.")
    )


@dataclass(frozen=True, kw_only=True, repr=False)
class ClickHouseSpec(_SpecBase):
    """ClickHouse datasource specification."""

    backend_type: ClassVar[str] = "clickhouse"
    host: str = field(metadata=_description("ClickHouse host."))
    port: int | None = field(
        default=None,
        metadata=_description("ClickHouse port; native default is 9000, secure default is 9440."),
    )
    database: str | None = field(
        default=None, metadata=_description("ClickHouse database; ibis default is 'default'.")
    )
    secure: bool | None = field(default=None, metadata=_description("Enable TLS for ClickHouse."))
    settings: dict[str, JsonValue] | None = field(
        default=None, metadata=_description("Optional ClickHouse settings map.")
    )
    user: EnvRef | None = field(
        default=None, metadata=_description("EnvRef from md.env for ClickHouse user.")
    )
    password: EnvRef | None = field(
        default=None, metadata=_description("EnvRef from md.env for ClickHouse password.")
    )


DatasourceSpec: TypeAlias = DuckDBSpec | TrinoSpec | MySQLSpec | PostgresSpec | ClickHouseSpec


def _loaded_datasource_ref(value: object, *, project_root: Path) -> Ref[DatasourceKind] | None:
    """Validate a loaded object's project before exposing its neutral identity."""
    identity = datasource_identity(value)
    if identity is None:
        return None
    if identity.project_root.resolve() != project_root.resolve():
        raise DatasourceFieldInvalidError(
            message="The datasource object belongs to another project.",
            expected=f"a datasource from project {project_root.resolve()}",
            received=f"{identity.reference.key} from project {identity.project_root}",
            location="datasource",
            effect_observed=DatasourceObservedEffects(query_executed=False),
            repair=repair(
                kind="inspect",
                canonical_id="load",
                action="Select the datasource explicitly from the receiving project.",
            ),
        )
    return identity.reference


def _storage_name(value: str | Ref[DatasourceKind] | DatasourceSymbol) -> str:
    """Return the private project storage key for a validated datasource identity."""
    symbolic = native_symbol_ref(value)
    if symbolic is not None and symbolic.kind is SemanticKind.DATASOURCE:
        return symbolic.path
    if type(value) is Ref and value.kind is SemanticKind.DATASOURCE:
        return value.path
    if type(value) is str:
        validate_datasource_name(value)
        return value
    raise TypeError(
        f"datasource storage identity must be str or Ref[datasource], got {type(value).__name__}"
    )


_DATASOURCE_CTX: ContextVar[DatasourceLoaderContext | None] = ContextVar(
    "_DATASOURCE_CTX",
    default=None,
)


_DECLARATION_WRAPPERS = frozenset(
    {__name__, "marivo.datasource.store.builders", "marivo.telemetry.decorator"}
)


def _caller_location() -> DatasourceSourceLocation:
    try:
        frame = inspect.currentframe()
        if frame is not None:
            caller_frame = frame.f_back
            while caller_frame is not None:
                if caller_frame.f_globals.get("__name__") not in _DECLARATION_WRAPPERS:
                    return DatasourceSourceLocation(
                        file=caller_frame.f_code.co_filename,
                        line=caller_frame.f_lineno,
                    )
                caller_frame = caller_frame.f_back
    except AttributeError:
        pass
    return DatasourceSourceLocation(file="<unknown>", line=0)


def _current_ctx() -> DatasourceLoaderContext | None:
    return _DATASOURCE_CTX.get()


def validate_datasource_name(name: Any) -> None:
    if not isinstance(name, str) or not name:
        raise DatasourceFieldInvalidError(
            message="datasource name must be a non-empty string",
            expected="a non-empty datasource storage name",
            received=repr(name),
            location="datasource name",
            repair=repair(
                kind="reauthor",
                canonical_id="duckdb",
                action="Provide a non-empty datasource name.",
            ),
        )
    try:
        _validate_segment(name, role="datasource name")
    except ValueError:
        suggested = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
        if not suggested:
            suggested = "datasource"
        if not suggested[0].isalpha():
            suggested = f"ds_{suggested}"
        raise DatasourceFieldInvalidError(
            message=f"datasource {name!r} is not a valid datasource name",
            expected="[a-z][a-z0-9_]*",
            received=name,
            location="datasource name",
            repair=repair(
                kind="reauthor",
                canonical_id="duckdb",
                action=(
                    f"Rename the datasource to {suggested!r}. Its identity becomes "
                    f"datasource:{suggested}. Update references to use the new identity."
                ),
            ),
        ) from None


def _ir_from_spec(spec: DatasourceSpec, *, location: DatasourceSourceLocation) -> DatasourceIR:
    return spec._to_ir(location=location)


def _declare_if_loading(spec: DatasourceSpec) -> None:
    ctx = _current_ctx()
    if ctx is not None:
        ctx.pending_objects.append(_ir_from_spec(spec, location=_caller_location()))
