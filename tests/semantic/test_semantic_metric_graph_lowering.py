"""Current definition lowering and the boundary against the retired Metric IR."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.expressions import DeclaredMetricRef, PerReduce, SourceAggregate
from marivo._definition.graph import computation_fingerprint, walk
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic._definition_identity import scoped_definition_fingerprint
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.reader import SemanticProject

ProjectFactory = Callable[[dict[str, str]], SemanticProject]

_MODEL = """\
import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders", columns={"amount":"amount", "sample_time":"sample_time"}))
amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY")
sample_time_ref = ms.time_dimension_column(name="sample_time", entity=orders, column="sample_time", granularity="minute", parse=ms.datetime(timezone="UTC", sample_interval=(5,"minute")))
finalized = ms.finalize()
amount = finalized.resolve(amount_ref)
sample_time = finalized.resolve(sample_time_ref)
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
    assert isinstance(consume_metric(first).node, DeclaredMetricRef) and isinstance(
        consume_metric(second).node, DeclaredMetricRef
    )
    assert consume_metric(first).node.ref.ref != consume_metric(second).node.ref.ref


def test_nested_metric_closure_captures_its_dependencies_without_source_io(
    semantic_project_factory: ProjectFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _catalog(semantic_project_factory)
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("definition-closure", report_timezone="UTC")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("definition binding opened a source")

    monkeypatch.setattr("marivo.datasource.backends._build_backend_from_effective", forbidden)
    query = session.observe({"nested": catalog.metrics.get("sales.outer")})
    closure = walk(
        tuple(node for _, node in query._graph.root.metrics),
        resolve=dict(query._graph.signature.declared_metrics).get,
    )
    assert any(isinstance(node, SourceAggregate) for node in closure.order)
    assert session.runs().items == ()


def test_temporal_sample_fold_remains_explicit_in_definition_order(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    folded = catalog._state.registry.declared_metrics["sales.folded"]
    assert isinstance(consume_metric(folded).node, PerReduce)
    assert consume_metric(folded).node.reducer == "quantile"
    assert consume_metric(folded).node.q == 0.95
    assert isinstance(consume_metric(folded).node.operand, SourceAggregate)
    assert consume_metric(folded).node.operand.reducer == "mean"


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
                root=ms.ref.metric("sales.revenue"),
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
