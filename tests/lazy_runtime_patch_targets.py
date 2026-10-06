"""Resolve the remaining pure compiler and datasource patch owners."""

from types import ModuleType

from marivo.analysis import compiler
from marivo.analysis.compiler import placement
from marivo.analysis.materialization import local_execution
from marivo.datasource import backends, json_source


def runtime_patch_owner(symbol: str) -> ModuleType:
    """Return the current module that owns the requested private operation."""
    if symbol in {"place", "source_binding"}:
        return placement
    if symbol == "execute_local":
        return local_execution
    if symbol in {
        "_build_backend_from_effective",
        "_effective_kwargs",
        "require_profile_for_backend_type",
    }:
        return backends
    if symbol == "compile_dataset":
        return compiler
    if symbol == "read_json_source":
        return json_source
    raise ValueError(f"Unknown Runtime patch point: {symbol}")
