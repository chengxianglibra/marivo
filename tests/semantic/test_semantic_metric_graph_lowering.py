"""Current definition lowering and the boundary against the retired Metric IR."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo._definition.expressions import DeclaredMetricRef, PerReduce, SourceAggregate
from marivo._definition.graph import computation_fingerprint, walk
from marivo._definition.relations import ObserveQuery
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.refs import ref as ref_factory
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic._definition_identity import scoped_definition_fingerprint
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import terminal_runs

ProjectFactory = Callable[[dict[str, str]], SemanticProject]


def _captured_graph(frame: AnalysisFrame) -> BoundGraph:
    assert isinstance(frame._backing, ExpressionFrameBacking)
    assert isinstance(frame._backing.root, SourceFrameBinding)
    return frame._backing.root.graph


_MODEL = """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders", columns={"amount":"amount", "sample_time":"sample_time"}))
amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY")
sample_time_ref = ms.time_dimension_column(name="sample_time", entity=orders, column="sample_time", sample_interval=(5,"minute"), parse=ms.timestamp(timezone="UTC"))
amount = amount_ref
sample_time = sample_time_ref
@ms.metric()
def revenue():
    return amount.sum()
@ms.metric()
def revenue_alias():
    return amount.sum()
@ms.metric()
def count():
    return amount.count()
@ms.metric()
def distinct():
    return amount.count_distinct()
@ms.metric()
def outer():
    return (revenue / count) / revenue
@ms.metric()
def folded():
    return amount.mean().per(sample_time).quantile(0.95)
"""


def _catalog(factory: ProjectFactory, source: str = _MODEL) -> SemanticCatalog:
    project = factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
            "sales/metrics.py": source,
        }
    )
    return SemanticCatalog(project)


@pytest.mark.parametrize("name", ("count", "distinct"))
def test_count_unit_survives_published_definition_resolution(
    semantic_project_factory: ProjectFactory, name: str
) -> None:
    catalog = _catalog(semantic_project_factory)
    assert consume_metric(catalog.metrics.get(f"sales.{name}")).unit == "1"
    assert consume_metric(catalog._state.registry.declared_metrics[f"sales.{name}"]).unit == "1"


def test_equivalent_named_metrics_share_computation_and_keep_distinct_authority(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    declarations = catalog._state.registry.declared_metrics
    assert computation_fingerprint(
        consume_metric(declarations["sales.revenue"]).node
    ) == computation_fingerprint(consume_metric(declarations["sales.revenue_alias"]).node)
    first = catalog.metrics.get("sales.revenue")
    second = catalog.metrics.get("sales.revenue_alias")
    first_node, second_node = consume_metric(first).node, consume_metric(second).node
    assert isinstance(first_node, DeclaredMetricRef)
    assert isinstance(second_node, DeclaredMetricRef)
    assert first_node.ref.ref != second_node.ref.ref


def test_nested_metric_closure_captures_its_dependencies_without_source_io(
    semantic_project_factory: ProjectFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _catalog(semantic_project_factory)
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("definition-closure", timezone="UTC")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("definition binding opened a source")

    monkeypatch.setattr("marivo.datasource.backends._build_backend_from_effective", forbidden)
    query = session.observe({"nested": catalog.metrics.get("sales.outer")})
    graph = _captured_graph(query)
    assert isinstance(graph.root, ObserveQuery)
    closure = walk(
        tuple(node for _, node in graph.root.metrics),
        resolve=dict(graph.signature.declared_metrics).get,
    )
    assert any(isinstance(node, SourceAggregate) for node in closure.order)
    assert terminal_runs(session) == ()


def test_temporal_sample_fold_remains_explicit_in_definition_order(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    folded = catalog._state.registry.declared_metrics["sales.folded"]
    node = consume_metric(folded).node
    assert isinstance(node, PerReduce)
    assert node.reducer == "quantile"
    assert node.q == 0.95
    assert isinstance(node.operand, SourceAggregate)
    assert node.operand.reducer == "mean"


def test_projected_source_mapping_is_canonical_and_changes_consumed_identity(
    semantic_project_factory: ProjectFactory,
) -> None:
    sources = (
        _MODEL,
        _MODEL.replace(
            '{"amount":"amount", "sample_time":"sample_time"}',
            '{"sample_time":"sample_time", "amount":"amount"}',
        ),
        _MODEL.replace('"amount":"amount"', '"amount":"amount_v2"'),
    )
    identities = []
    for source in sources:
        catalog = _catalog(semantic_project_factory, source)
        state = catalog._state
        identities.append(
            scoped_definition_fingerprint(
                root=ref_factory.metric("sales.revenue"),
                definitions=state.definitions,
                dependencies=state.dependencies,
                sidecar=state.sidecar,
            )
        )
    assert identities[0] == identities[1]
    assert identities[2] != identities[0]


def test_arbitrary_measure_helpers_are_rejected_without_executing_authored_function(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = (
        _MODEL.split("finalized =", 1)[0]
        + """\
def transform(value):
    raise AssertionError("Authored helper executed")
@ms.measure(entity=orders, additivity=ms.additive_all())
def invalid(rows):
    return transform(rows.amount)
"""
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
            "sales/metrics.py": source,
        }
    )
    assert not project.is_ready()
    (error,) = project.errors()
    assert error.kind == "invalid_component_body"
    assert error.repair is not None
    assert "Authored helper executed" not in str(error)
