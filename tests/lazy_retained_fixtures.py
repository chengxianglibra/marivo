"""Isolated real-source setup for retained-state continuation and cold journeys."""

from pathlib import Path
from typing import Literal

from marivo.analysis.materialization.admission import DatasetRuntime
from tests.lazy_adapter_fixtures import AdapterFixture
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database


def setup_retained(
    project: Path,
    kind: Literal["local", "engine"] = "local",
) -> AdapterFixture:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(project, "retained")
    return AdapterFixture(
        runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar), database
    )
