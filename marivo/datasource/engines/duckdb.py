"""DuckDB engine profile."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import Timer

from ibis.backends import BaseBackend

from marivo.datasource.engines.base import (
    AuthoringCapabilities,
    EngineMetadataIntrospection,
    EngineProfile,
    QuantileCapability,
    default_table_name_parts,
    identity_str,
    schema_only_metadata_inspect,
)


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    path = kwargs.get("path", ":memory:")
    connect_kwargs: dict[str, object] = dict(kwargs)
    connect_kwargs.pop("path", None)
    connect_kwargs["database"] = path
    connect_kwargs["threads"] = 1
    connect_kwargs["TimeZone"] = "UTC"
    if "read_only" in connect_kwargs:
        connect_kwargs["read_only"] = bool(connect_kwargs["read_only"])
    backend = ibis.duckdb.connect(**connect_kwargs)
    backend._marivo_timezone_name = "UTC"
    return backend


def apply_read_only_kwargs(kwargs: Mapping[str, object]) -> dict[str, object]:
    out = dict(kwargs)
    out["read_only"] = True
    return out


@contextmanager
def authoring_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    connection = getattr(backend, "con", None)
    interrupt = getattr(connection, "interrupt", None)
    if not callable(interrupt):
        raise RuntimeError("duckdb backend does not expose connection.interrupt()")
    timer = Timer(timeout_seconds, interrupt)
    try:
        timer.start()
        yield
    finally:
        timer.cancel()


PROFILE = EngineProfile(
    name="duckdb",
    aliases=(),
    authoring_func="duckdb",
    required_modules=("ibis.backends.duckdb",),
    connect=connect,
    apply_read_only_kwargs=apply_read_only_kwargs,
    identifier_quote='"',
    table_name_parts=default_table_name_parts,
    inspect_partition_values=None,
    metadata=EngineMetadataIntrospection(inspect_table=schema_only_metadata_inspect),
    authoring_capabilities=AuthoringCapabilities(
        partition_predicate_supported=True,
        transformed_partition_supported=False,
        timeout_enforced=True,
        byte_estimate_supported=False,
    ),
    translate_strptime_format=identity_str,
    datetime_decode_policy="local_naive_label",
    quantile=QuantileCapability(mode="exact", method="linear_interpolation"),
    percentile_uses_approx_quantile=False,
    authoring_timeout=authoring_timeout,
)
