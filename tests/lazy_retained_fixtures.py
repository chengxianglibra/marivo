"""Isolated real-source setup for retained-state continuation and cold journeys."""

from pathlib import Path
from typing import Literal

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import (
    LocalTarget,
    ObjectTarget,
    S3Access,
)
from tests.lazy_adapter_fixtures import AdapterFixture
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database


def setup_retained(
    project: Path,
    kind: Literal["local", "engine", "object"] = "local",
    *,
    access: S3Access | None = None,
) -> AdapterFixture:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    target = (
        LocalTarget()
        if kind == "engine"
        else ObjectTarget("fixture")
        if kind == "object"
        else LocalTarget()
    )
    runtime = DatasetRuntime.create(
        project,
        "retained",
        target=target,
        object_bindings=() if access is None else (access,),
    )
    return AdapterFixture(
        runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar), database
    )
