"""Public ``mv.profiles`` API surface."""

from __future__ import annotations

import builtins
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from marivo.analysis_py.errors import ProfileMissingError
from marivo.analysis_py.profiles import backends as _backends
from marivo.analysis_py.profiles import store as _store


@dataclass(frozen=True)
class ProfileSummary:
    """One row returned by :func:`list_profiles`."""

    name: str
    backend_type: str


@dataclass(frozen=True)
class ProfileDescription:
    """Detailed view of one profile with secrets redacted.

    ``literal_fields`` contains primitive values written as-is (host, port, etc.).
    ``env_refs`` maps the stem (``password``) to the env var name (``WAREHOUSE_PWD``),
    never the resolved value.
    """

    name: str
    backend_type: str
    literal_fields: dict[str, Any]
    env_refs: dict[str, str]


@dataclass(frozen=True)
class ProfileTestResult:
    """Outcome of :func:`test`."""

    name: str
    ok: bool
    error: str | None
    latency_ms: int | None


def _split_fields(profile: _store.StoredProfile) -> tuple[dict[str, Any], dict[str, str]]:
    literals: dict[str, Any] = {}
    env_refs: dict[str, str] = {}
    for key, value in profile.fields.items():
        if key.endswith("_env") and len(key) > len("_env"):
            stem = key[: -len("_env")]
            env_refs[stem] = str(value)
        else:
            literals[key] = value
    return literals, env_refs


def set(name: str, *, backend_type: str, **fields: Any) -> ProfileSummary:
    """Create or replace a profile entry.

    Sensitive credentials (password, token, api_key, ...) must be supplied via
    ``<field>_env="VAR_NAME"`` so the literal value lives only in the process
    environment. Passing them as literals raises
    :class:`marivo.analysis_py.errors.ProfileSecretInPlaintextError`.
    """
    stored = _store.save_one(name=name, backend_type=backend_type, fields=fields)
    return ProfileSummary(name=stored.name, backend_type=stored.backend_type)


def remove(name: str) -> bool:
    """Delete the named profile. Returns ``True`` if a profile was removed."""
    return _store.delete_one(name)


def list() -> builtins.list[ProfileSummary]:
    """Return every configured profile, sorted by name."""
    return [
        ProfileSummary(name=p.name, backend_type=p.backend_type)
        for p in sorted(_store.load_all().values(), key=lambda item: item.name)
    ]


def describe(name: str) -> ProfileDescription:
    """Return the redacted shape of the named profile.

    Raises :class:`ProfileMissingError` if the profile does not exist.
    """
    profile = _store.load_one(name)
    if profile is None:
        raise ProfileMissingError(
            message=f"profile {name!r} is not configured",
            details={"datasource": name, "available": _store.list_names()},
        )
    literals, env_refs = _split_fields(profile)
    return ProfileDescription(
        name=profile.name,
        backend_type=profile.backend_type,
        literal_fields=literals,
        env_refs=env_refs,
    )


def build_backend(name: str) -> Any:
    """Return a live ibis backend for the named profile.

    This is the function ``mv.session.create / attach`` uses internally when no
    explicit ``backends=`` / ``backend_factory=`` is provided.
    """
    profile = _store.load_one(name)
    if profile is None:
        raise ProfileMissingError(
            message=f"profile {name!r} is not configured",
            details={"datasource": name, "available": _store.list_names()},
        )
    return _backends.build_backend(profile)


def test(name: str) -> ProfileTestResult:
    """Open the backend and run a trivial round-trip to verify reachability."""
    start = time.perf_counter()
    backend: Any | None = None
    try:
        backend = build_backend(name)
        # Lightweight ibis call that every supported backend understands.
        # We do not run SQL; merely listing tables forces a connection negotiation.
        backend.list_tables()
        latency_ms = int((time.perf_counter() - start) * 1000)
        return ProfileTestResult(name=name, ok=True, error=None, latency_ms=latency_ms)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return ProfileTestResult(
            name=name,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            latency_ms=latency_ms,
        )
    finally:
        if backend is not None:
            disconnect = getattr(backend, "disconnect", None)
            if callable(disconnect):
                with suppress(Exception):
                    disconnect()
