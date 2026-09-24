"""Resolve private Runtime patch points after the execution split."""

from types import ModuleType

from marivo.analysis.materialization import (
    dataset_execution,
    local_stage,
    source_preparation,
)


def runtime_patch_owner(symbol: str) -> ModuleType:
    """Return the module that actually looks up a patched private operation."""
    if symbol in {"place", "source_binding"}:
        return dataset_execution
    if symbol == "execute_local":
        return local_stage
    if symbol in {
        "_build_backend_from_effective",
        "_effective_kwargs",
        "require_profile_for_backend_type",
        "compile_dataset",
        "read_json_source",
    }:
        return source_preparation
    raise ValueError(f"Unknown Runtime patch point: {symbol}")
