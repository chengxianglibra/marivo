"""Ontology authoring, discovery, selection, observation, and recovery contract."""

from __future__ import annotations

from typing import Any, cast

import pytest

import marivo.analysis as mv
import marivo.ontology as mo
import marivo.semantic as ms
from marivo.ontology.errors import (
    InvalidOntologyRefError,
    InvalidSemanticEdgeError,
    OntologyLoadError,
)
from tests.conftest import bootstrap_sales_project


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield


def _add_order_count_metric(tmp_path) -> None:
    datasets = tmp_path / "models" / "semantic" / "sales" / "datasets.py"
    source = datasets.read_text()
    source = source.replace(
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))",
        (
            "orders = ms.entity(name='orders', datasource=warehouse, "
            "source=md.table('orders'), "
            "ai_context=ms.ai_context(business_definition='One commerce order.'))"
        ),
    )
    source = source.replace(
        "name='revenue', )",
        (
            "name='revenue', ai_context=ms.ai_context("
            "business_definition='Recognized order revenue.'))"
        ),
    )
    datasets.write_text(
        source
        + "\n@ms.metric(entities=[orders], additivity='additive', name='order_count')\n".replace(
            "name='order_count')",
            (
                "name='order_count', ai_context=ms.ai_context("
                "business_definition='Count of observed orders.'))"
            ),
        )
        + "def order_count(orders):\n"
        + "    return orders.amount.count()\n"
    )


def _write_ontology(tmp_path, body: str) -> None:
    source = tmp_path / "models" / "ontology.py"
    source.write_text(body)


def _valid_ontology_source() -> str:
    return (
        "import marivo.ontology as mo\n"
        "import marivo.semantic as ms\n"
        "\n"
        "order_volume = mo.influences(\n"
        "    name='sales.order_volume_influences_revenue',\n"
        "    driver=ms.ref.metric('sales.order_count'),\n"
        "    outcome=ms.ref.metric('sales.revenue'),\n"
        "    ai_context=ms.ai_context(\n"
        "        business_definition='Order volume may help explain revenue movement.',\n"
        "        guardrails=['Discovery context only; this is not causal evidence.'],\n"
        "    ),\n"
        ")\n"
    )


def _ready_project(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    _add_order_count_metric(tmp_path)
    _write_ontology(tmp_path, _valid_ontology_source())


def test_ontology_constructor_requires_load_context() -> None:
    with pytest.raises(InvalidSemanticEdgeError):
        mo.influences(
            name="outside_loader",
            driver=ms.ref.metric("sales.order_count"),
            outcome=ms.ref.metric("sales.revenue"),
            ai_context=ms.ai_context(business_definition="Not load-scoped."),
        )


def test_ontology_edge_error_exposes_structured_repair() -> None:
    with pytest.raises(InvalidSemanticEdgeError) as exc_info:
        mo.influences(
            name="outside_loader",
            driver=ms.ref.metric("sales.order_count"),
            outcome=ms.ref.metric("sales.revenue"),
            ai_context=ms.ai_context(business_definition="Not load-scoped."),
        )

    error = exc_info.value
    assert error.kind == "invalid_semantic_edge"
    assert error.expected.startswith("an authored call executed by mo.load")
    assert error.received == "constructor call outside ontology loading"
    assert error.repair is not None
    assert error.repair.kind == "reauthor"
    assert error.repair.help_target.canonical_id == "authoring"


def test_ontology_load_type_error_exposes_structured_repair() -> None:
    with pytest.raises(InvalidOntologyRefError) as exc_info:
        mo.load(semantic=object())

    error = exc_info.value
    assert error.kind == "invalid_ontology_ref"
    assert error.expected == "SemanticCatalog from ms.load() or session.catalog"
    assert error.received == "object"
    assert error.repair is not None
    assert error.repair.kind == "reauthor"
    assert error.repair.help_target.canonical_id == "authoring"


def test_absent_empty_and_invalid_ontology_states_are_distinct(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    _add_order_count_metric(tmp_path)
    semantic = ms.load(workspace_dir=tmp_path)

    absent = mo.load(semantic=semantic)
    assert absent.configured is False
    assert absent.edge_count == 0

    _write_ontology(tmp_path, "import marivo.ontology as mo\n")
    empty = mo.load(semantic=semantic)
    assert empty.configured is True
    assert empty.edge_count == 0
    assert empty.definition_fingerprint != absent.definition_fingerprint

    _write_ontology(
        tmp_path,
        _valid_ontology_source().replace("sales.order_count", "sales.missing"),
    )
    with pytest.raises(OntologyLoadError) as exc_info:
        mo.load(semantic=semantic)
    assert exc_info.value.issues
    issue = exc_info.value.issues[0]
    assert issue.kind == "invalid_ontology_ref"
    assert issue.expected is not None
    assert issue.received is not None
    assert issue.repair is not None
    assert issue.repair.help_target.canonical_id == "authoring"

    session = mv.session.get_or_create(name="invalid-ontology")
    assert not hasattr(session, "_ontology_state")


def test_ontology_execution_error_issue_has_structured_repair(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    semantic = ms.load(workspace_dir=tmp_path)
    _write_ontology(tmp_path, "raise RuntimeError('authored failure')\n")

    with pytest.raises(OntologyLoadError) as exc_info:
        mo.load(semantic=semantic)

    issue = exc_info.value.issues[0]
    assert issue.kind == "invalid_semantic_edge"
    assert issue.expected == "a valid models/ontology.py module"
    assert issue.received == "RuntimeError"
    assert issue.repair is not None
    assert issue.repair.kind == "reauthor"
    assert issue.repair.help_target.canonical_id == "authoring"


def test_relation_constructors_enforce_roles_context_and_symmetric_identity(tmp_path) -> None:
    with pytest.raises(mo.errors.InvalidOntologyRefError):
        mo.influences(
            name="invalid_event_driver",
            driver=cast("Any", ms.ref.event("sales.ordered")),
            outcome=ms.ref.metric("sales.revenue"),
            ai_context=ms.ai_context(business_definition="Invalid endpoint."),
        )
    with pytest.raises(mo.errors.InvalidOntologyRefError):
        mo.related_to(
            name="invalid_dimension_endpoint",
            left=cast("Any", ms.ref.dimension("sales.orders.region")),
            right=ms.ref.metric("sales.revenue"),
            ai_context=ms.ai_context(business_definition="Invalid endpoint."),
        )
    with pytest.raises(InvalidSemanticEdgeError) as exc_info:
        mo.influences(
            name="raw_context",
            driver=ms.ref.metric("sales.order_count"),
            outcome=ms.ref.metric("sales.revenue"),
            ai_context=cast("Any", {"business_definition": "Raw mapping."}),
        )
    assert exc_info.value.kind == "invalid_ai_context"

    bootstrap_sales_project(tmp_path)
    _add_order_count_metric(tmp_path)
    _write_ontology(
        tmp_path,
        (
            "import marivo.ontology as mo\n"
            "import marivo.semantic as ms\n"
            "ctx = ms.ai_context(business_definition='Symmetric context.')\n"
            "mo.related_to(name='sales.first', "
            "left=ms.ref.metric('sales.revenue'), "
            "right=ms.ref.metric('sales.order_count'), ai_context=ctx)\n"
            "mo.related_to(name='sales.second', "
            "left=ms.ref.metric('sales.order_count'), "
            "right=ms.ref.metric('sales.revenue'), ai_context=ctx)\n"
        ),
    )

    with pytest.raises(OntologyLoadError) as load_error:
        mo.load(semantic=ms.load(workspace_dir=tmp_path))
    assert "duplicate related_to endpoint pair" in str(load_error.value.issues[0])
