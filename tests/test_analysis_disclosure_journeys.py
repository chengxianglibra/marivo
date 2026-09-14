"""Independent task routes, rendered examples and public lazy-analysis continuation."""

from __future__ import annotations

import ast
import io
import re
import subprocess
import sys
from collections import Counter, deque
from contextlib import redirect_stdout
from operator import attrgetter
from pathlib import Path

import duckdb
import pytest

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis._capabilities.dataset_model import CallableInput, NavigationInput
from marivo.analysis._capabilities.registry import REGISTRY
from tests.lazy_disclosure_fixtures import example_inputs


def _help(target: object) -> str:
    output = io.StringIO()
    with redirect_stdout(output):
        marivo.help(target)
    return output.getvalue()


def _routes(text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(re.findall(r"marivo\.help\(['\"](analysis(?:\.[^'\"]+)?)['\"]\)", text))
    )


def test_task_discovery_is_bounded_and_reaches_prerequisites_without_guessing() -> None:
    queue = deque([("analysis", 0)])
    distances: dict[str, int] = {}
    while queue:
        target, distance = queue.popleft()
        if target in distances:
            continue
        distances[target] = distance
        queue.extend((child, distance + 1) for child in _routes(_help(target)))
    for target, bound in {
        "session.get_or_create": 2,
        "session.resume": 2,
        "observe": 2,
        "population.create": 2,
        "events.match": 2,
        "lifecycle.replay": 2,
        "catalog.require": 3,
        "catalog.readiness": 3,
        "metric_dataset.aggregate": 3,
        "metric_dataset.compare": 3,
        "delta_dataset.attribute": 3,
        "metric_dataset.correlate": 3,
        "metric_dataset.forecast": 3,
        "discovery.driver_axes": 3,
        "time_scope": 3,
        "BoundedCompletenessDeclarationV1.create": 3,
        "SourceOriginCompletenessDeclarationV1.create": 3,
        "artifact.findings": 2,
        "session.revalidate": 2,
        "session.artifact": 2,
        "session.runs": 3,
    }.items():
        assert distances["analysis." + target] <= bound
    assert not any(re.search(r"\.page_\d+", target) for target in distances)
    discovery = set(REGISTRY.discovery_ids())
    assert not {"Session", "Finding", "TimeScope", "TimeScope.model_dump"} & discovery
    assert {"TimeScope", "Finding", "session.get_run"} <= {
        t.removeprefix("analysis.") for t in distances
    }
    membership = Counter(
        m for d in REGISTRY.descriptors if isinstance(d, NavigationInput) for m in d.members
    )
    assert all(count == 1 for count in membership.values())


def test_examples_declare_only_their_actual_external_inputs() -> None:
    for descriptor in REGISTRY.descriptors:
        if not isinstance(descriptor, CallableInput):
            continue
        tree = ast.parse(descriptor.example.code)
        reads = {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        writes = {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                writes.update(a.asname or a.name.split(".")[0] for a in node.names)
        assert set(descriptor.example.requires) == reads - writes, descriptor.canonical_id
    creation = _help("analysis.session.get_or_create")
    assert "Example inputs: none" in creation
    assert "namespace" not in creation
    assert "session = mv.session\n" not in _help("analysis.session.runs")


@pytest.mark.parametrize(
    "key",
    (
        "metric",
        "dimensioned",
        "time_metric",
        "delta",
        "funnel_delta",
        "events",
        "lifecycle",
        "population",
    ),
)
def test_current_contract_continuations_resolve_to_the_actual_bound_method(key: str) -> None:
    dataset = example_inputs(REGISTRY)[key]
    assert isinstance(dataset, mv.Dataset)
    text = dataset.contract().render(max_output_bytes=None)
    continuations = re.findall(r"call=dataset\.([\w.]+); marivo.help\('([^']+)'\)", text)
    assert continuations
    for method, target in continuations:
        assert _help(attrgetter(method)(dataset)) == _help(target)
    assert "analysis.actions.execute" in text
    assert "analysis.methods')" not in text


def test_attribution_discloses_all_registered_authority_variants_and_top_k_meaning() -> None:
    text = _help("analysis.delta_dataset.attribute")
    for method in ("additive", "component-mix", "distinct-membership", "distribution-Shapley"):
        assert method in text
    assert "per mapped parent" in text
    assert "None retains all members" in text
    assert "eight mapped players per comparison scope and resolution, including Other" in text
    assert "contribution row bound" not in text


class _Walk:
    """Follow only links already rendered by public Help or a current contract."""

    def __init__(self) -> None:
        self.available = {"analysis"}

    def read(self, target: str) -> str:
        assert target in self.available, f"Undisclosed target: {target}"
        text = _help(target)
        self.available.update(_routes(text))
        return text

    def inspect(self, dataset: mv.Dataset) -> None:
        self.available.update(_routes(dataset.contract().render(max_output_bytes=None)))


@pytest.mark.runtime
def test_public_help_walk_executes_evidence_and_recovers_offline_in_a_fresh_process(
    authoring_evidence_project: Path,
) -> None:
    database = authoring_evidence_project / "warehouse.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("UPDATE orders SET log_date='20410718', amount=100 WHERE query_id=4")
    walk = _Walk()
    walk.read("analysis")
    walk.read("analysis.entry")
    creation = walk.read("analysis.session.get_or_create")
    scope: dict[str, object] = {}
    exec(creation.split("Example:\n", 1)[1].rsplit("\nExpected:", 1)[0], scope)
    session = scope["result"]
    assert isinstance(session, mv.Session)
    walk.read("analysis.catalog")
    walk.read("analysis.catalog.require")
    walk.read("analysis.catalog.readiness")
    revenue = session.catalog.require(ms.ref.metric("sales.revenue"))
    region = ms.ref.dimension("sales.orders.region")
    session.catalog.readiness(refs=[revenue.ref, region])
    walk.read("analysis.observe")
    walk.read("analysis.time_scope")
    current = session.observe(
        revenue, time_scope=mv.time_scope(start="2041-07-18", end="2041-07-19")
    )
    baseline = session.observe(
        revenue, time_scope=mv.time_scope(start="2041-07-17", end="2041-07-18")
    )
    walk.inspect(current)
    walk.read("analysis.metric_dataset.with_dimensions")
    current = current.with_dimensions(region)
    baseline = baseline.with_dimensions(region)
    walk.inspect(current)
    walk.read("analysis.metric_dataset.aggregate")
    current, baseline = current.aggregate(), baseline.aggregate()
    walk.inspect(current)
    walk.read("analysis.metric_dataset.compare")
    change = current.compare(baseline)
    walk.inspect(change)
    walk.read("analysis.delta_dataset.attribute")
    attribution = change.attribute(axes=(region,))
    assert session.runs().items == ()
    assert not session._runtime.statistics.statements
    walk.inspect(attribution)
    walk.read("analysis.actions.execute")
    result = attribution.execute()
    walk.inspect(result)
    walk.read("analysis.actions.show")
    result.show()
    walk.read("analysis.evidence")
    walk.read("analysis.artifact.findings")
    findings = result.findings()
    assert findings.items
    walk.read("analysis.artifact.finding")
    assert result.finding(findings.items[0].finding_id) == findings.items[0]
    walk.read("analysis.session.revalidate")
    inspection = session.revalidate(result.state.artifact_ref)
    assert inspection.artifact_integrity == "valid"
    walk.read("analysis.datasets.materialized")
    walk.read("analysis.actions.to_pandas")
    assert result.to_pandas()["contribution"].sum() == pytest.approx(100.0)
    assert len(session.runs().items) == 1
    database.rename(database.with_suffix(".offline"))
    code = """
import io
import re
import sys
from contextlib import redirect_stdout
import marivo
import marivo.analysis as mv
available = {"analysis"}
def read(target):
    assert target in available, target
    out = io.StringIO()
    with redirect_stdout(out):
        marivo.help(target)
    available.update(re.findall(r"marivo.help\\('([^']+)'\\)", out.getvalue()))
read("analysis")
read("analysis.runtime")
read("analysis.runtime.sessions")
read("analysis.session.namespace")
read("analysis.session.resume")
session = mv.session.resume(sys.argv[1], by="id")
read("analysis.runtime.runs")
read("analysis.session.runs")
page = session.runs()
read("analysis.RunPage")
read("analysis.SucceededRun")
read("analysis.session.artifact")
result = session.artifact(page.items[0].output_artifact_ref)
assert str(result.state.artifact_ref) == sys.argv[2]
read("analysis.datasets.materialized")
read("analysis.actions.to_pandas")
assert result.to_pandas()["contribution"].sum() == 100.0
assert not session._runtime.statistics.statements
assert len(session.runs().items) == 1
print("offline Help recovery passed")
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, session.id, str(result.state.artifact_ref)],
        cwd=authoring_evidence_project,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "offline Help recovery passed" in completed.stdout
