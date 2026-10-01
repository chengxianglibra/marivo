"""Analysis contract continuations and cold runtime behavior."""

from __future__ import annotations

import subprocess
import sys
from operator import attrgetter
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis._capabilities.registry import REGISTRY
from tests.lazy_disclosure_fixtures import example_inputs


@pytest.mark.parametrize(
    "key",
    (
        "metric",
        "dimensioned",
        "time_metric",
        "events",
        "lifecycle",
        "population",
    ),
)
def test_current_continuation_registry_resolves_to_the_actual_bound_method(key: str) -> None:
    dataset = example_inputs(REGISTRY)[key]
    assert isinstance(dataset, mv.Dataset)
    from marivo.analysis._capabilities.surface import ANALYSIS_LIVE_SURFACE
    from marivo.introspection.live.resolve import resolve_live_target

    continuations = tuple(
        descriptor
        for consumer in dataset._registry.consumers_for(dataset)
        if (descriptor := REGISTRY.continuation_descriptor(dataset, consumer.id)) is not None
    )
    assert continuations
    for descriptor in continuations:
        method = descriptor.public_entrypoint.removeprefix("dataset.")
        method_target = resolve_live_target(attrgetter(method)(dataset), ANALYSIS_LIVE_SURFACE)
        route_target = resolve_live_target(
            descriptor.canonical_id,
            ANALYSIS_LIVE_SURFACE,
        )
        assert method_target.descriptor is descriptor
        assert route_target.descriptor is descriptor


@pytest.mark.runtime
def test_analysis_executes_evidence_and_recovers_offline_in_a_fresh_process(
    authoring_evidence_project: Path,
) -> None:
    database = authoring_evidence_project / "warehouse.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("UPDATE orders SET log_date='20410718', amount=100 WHERE query_id=4")
    session = mv.session.get_or_create(name="analysis-evidence")
    revenue = session.catalog.require(ms.ref.metric("sales.revenue"))
    region = ms.ref.dimension("sales.orders.region")
    session.catalog.readiness(refs=[revenue.ref, region])
    current = session.observe(
        revenue, time_scope=mv.time_scope(start="2041-07-18", end="2041-07-19")
    )
    baseline = session.observe(
        revenue, time_scope=mv.time_scope(start="2041-07-17", end="2041-07-18")
    )
    current = current.with_dimensions(region)
    baseline = baseline.with_dimensions(region)
    current, baseline = current.aggregate(), baseline.aggregate()
    change = current.compare(baseline)
    attribution = change.attribute(axes=(region,))
    assert session.runs().items == ()
    assert not session._runtime.statistics.statements
    result = attribution.execute()
    result.show()
    findings = result.findings()
    assert findings.items
    assert result.finding(findings.items[0].finding_id) == findings.items[0]
    inspection = session.revalidate(result.state.artifact_ref)
    assert inspection.artifact_integrity == "valid"
    assert result.to_pandas()["contribution"].sum() == pytest.approx(100.0)
    assert len(session.runs().items) == 1
    database.rename(database.with_suffix(".offline"))
    code = """
import sys
import marivo.analysis as mv
session = mv.session.resume(sys.argv[1], by="id")
page = session.runs()
result = session.artifact(page.items[0].output_artifact_ref)
assert str(result.state.artifact_ref) == sys.argv[2]
assert result.to_pandas()["contribution"].sum() == 100.0
assert not session._runtime.statistics.statements
assert len(session.runs().items) == 1
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, session.id, str(result.state.artifact_ref)],
        cwd=authoring_evidence_project,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
