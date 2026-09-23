"""Compound source-chain and unsupported-adapter terminal economics."""

from pathlib import Path

import pandas as pd
import pytest

from marivo.analysis.compiler.placement import SourceStep, place
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from tests.lazy_acceptance_capture import counts
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

pytestmark = pytest.mark.runtime


def test_compound_source_chain_and_retained_rollup(tmp_path: Path) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "compound-economics")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    revenue = ref.metric("sales.revenue")
    lines = ref.metric("sales.line_revenue")
    region = ref.dimension("sales.customers.region")
    population = sources.population(ref.entity("sales.customers"))
    observed = sources.observe([revenue, lines], population=population)
    reduced = observed.where(gt(revenue, 0)).with_dimensions(region).aggregate()
    target = reduced.rank(reduced.fields.metric(revenue)).limit(2).metric(revenue)
    assert counts(runtime) == {"analysis_action_runs": 0, "dataset_artifacts": 0}
    assert runtime.statistics.events == {}
    physical = place(target)
    assert len(physical.steps) == 1 and isinstance(physical.steps[0], SourceStep)
    result = target.execute()
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.transferred_rows == 2
    frame = result.to_pandas()
    assert frame["revenue"].tolist() == [100.0, 47.0]
    assert counts(runtime) == {"analysis_action_runs": 1, "dataset_artifacts": 1}
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert runtime.revalidate(result.state.artifact_ref).storage_authority == "readable"
    database.rename(tmp_path / "source.offline")
    assert target.execute().state.artifact_ref == result.state.artifact_ref
    assert runtime.statistics.primary_queries == runtime.statistics.transferred_rows == 0
    assert counts(runtime) == {"analysis_action_runs": 1, "dataset_artifacts": 1}
    folded = result.rollup(drop_dimensions=(region,)).execute()
    assert runtime.statistics.events.get("profile_resolution", 0) == 0
    folded_frame = folded.to_pandas()
    assert folded_frame["revenue"].tolist() == [147.0]
    assert counts(runtime) == {"analysis_action_runs": 2, "dataset_artifacts": 2}
    assert runtime.revalidate(folded.state.artifact_ref).storage_authority == "readable"
    pd.testing.assert_frame_equal(result.to_pandas(), frame)
