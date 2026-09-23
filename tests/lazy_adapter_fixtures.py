"""Small real-source setup shared by adapter, failure and process acceptance tests."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.session._lazy_sources import LazySources
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database


@dataclass(frozen=True, slots=True)
class AdapterFixture:
    runtime: DatasetRuntime
    sources: LazySources
    database: Path


def setup_adapter(
    project: Path,
    kind: Literal["engine"],
    *,
    event: Callable[[str], None] | None = None,
) -> AdapterFixture:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(
        project,
        "adapter",
        event=event,
    )
    return AdapterFixture(
        runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar), database
    )
