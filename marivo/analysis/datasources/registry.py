"""Public ``mv.datasources`` API surface."""

from __future__ import annotations

import builtins
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from marivo.analysis.datasources import backends as _backends
from marivo.analysis.datasources import store as _store
from marivo.analysis.errors import DatasourceMissingError


@dataclass(frozen=True)
class DatasourceSummary:
    name: str
    backend_type: str


@dataclass(frozen=True)
class DatasourceDescription:
    name: str
    backend_type: str
    literal_fields: dict[str, Any]
    env_refs: dict[str, str]


@dataclass(frozen=True)
class DatasourceTestResult:
    name: str
    ok: bool
    error: str | None
    latency_ms: int | None


def register(name: str, *, backend_type: str, **fields: Any) -> DatasourceSummary:
    """Create or replace a project-level datasource file."""
    stored = _store.save_one(name=name, backend_type=backend_type, fields=fields)
    return DatasourceSummary(name=stored.name, backend_type=stored.backend_type)


def remove(name: str) -> bool:
    """Delete the named project datasource file."""
    return _store.delete_one(name)


def all() -> builtins.list[DatasourceSummary]:
    """Return every project datasource, sorted by name."""
    return [
        DatasourceSummary(name=p.name, backend_type=p.backend_type)
        for p in sorted(_store.load_all().values(), key=lambda item: item.name)
    ]


def describe(name: str) -> DatasourceDescription:
    """Return the redacted shape of the named datasource."""
    datasource = _store.load_one(name)
    if datasource is None:
        raise DatasourceMissingError(
            message=f"datasource {name!r} is not configured",
            details={"datasource": name, "available": _store.list_names()},
        )
    return DatasourceDescription(
        name=datasource.name,
        backend_type=datasource.backend_type,
        literal_fields=dict(datasource.fields),
        env_refs=dict(datasource.env_refs),
    )


def build_backend(name: str) -> Any:
    """Return a live ibis backend for the named project datasource."""
    datasource = _store.load_one(name)
    if datasource is None:
        raise DatasourceMissingError(
            message=f"datasource {name!r} is not configured",
            details={"datasource": name, "available": _store.list_names()},
        )
    return _backends.build_backend(datasource)


def test(name: str) -> DatasourceTestResult:
    """Open the backend and run a trivial round-trip to verify reachability."""
    start = time.perf_counter()
    backend: Any | None = None
    try:
        backend = build_backend(name)
        backend.list_tables()
        latency_ms = int((time.perf_counter() - start) * 1000)
        return DatasourceTestResult(name=name, ok=True, error=None, latency_ms=latency_ms)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return DatasourceTestResult(
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
