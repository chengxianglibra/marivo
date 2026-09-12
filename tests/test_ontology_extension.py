"""Ontology authoring, discovery, selection, observation, and recovery contract."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import pytest

import marivo
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


def test_ontology_public_surface_and_help_are_closed(capsys) -> None:
    assert mo.__all__ == [
        "OntologyCatalog",
        "SemanticEdgeRef",
        "errors",
        "influences",
        "load",
        "related_to",
    ]
    assert not hasattr(mo, "edge")
    assert not hasattr(mo, "precedes")

    marivo.help()
    root_text = capsys.readouterr().out
    assert 'marivo.help("authoring")' in root_text
    assert 'marivo.help("analysis")' in root_text
    assert 'marivo.help("ontology")' not in root_text

    marivo.help("ontology")
    ontology_text = capsys.readouterr().out
    assert "marivo.ontology" in ontology_text
    assert 'marivo.help("ontology.<target>")' in ontology_text
    assert "authoring: Load the one optional project ontology" in ontology_text
    assert "Ontology is not executable semantic authority" in ontology_text

    marivo.help("ontology.influences")
    text = capsys.readouterr().out
    assert "Entrypoint: mo.influences" in text
    assert "Signature:" in text
    assert "Output family: SemanticEdgeRef" in text
    assert "Example:" in text
    assert "Constraints:" in text
    assert "driver" in text
    assert "outcome" in text
    assert "not assert causality" in text
    assert "data access: none" in text
    assert "connection: none" in text
    assert "mutations: semantic_source" in text

    marivo.help("ontology.load")
    text = capsys.readouterr().out
    assert "Entrypoint: mo.load" in text
    assert "Signature:" in text
    assert "Output family: OntologyCatalog" in text
    assert "semantic_catalog = ms.load()" in text
    assert "data access: local_metadata_read" in text
    assert "connection: none" in text
    assert "mutations: none" in text

    with pytest.raises(InvalidSemanticEdgeError):
        mo.influences(
            name="outside_loader",
            driver=ms.ref.metric("sales.order_count"),
            outcome=ms.ref.metric("sales.revenue"),
            ai_context=ms.ai_context(business_definition="Not load-scoped."),
        )


def test_ontology_root_reveals_current_environment(capsys) -> None:
    marivo.help("ontology")
    text = capsys.readouterr().out

    assert f"Marivo: {marivo.__version__}" in text
    assert f"Python: {sys.executable}" in text
    assert f"Package: {Path(marivo.__file__).resolve()}" in text


def test_ontology_focused_help_discloses_both_constructor_contracts(capsys) -> None:
    marivo.help("ontology.related_to")
    text = capsys.readouterr().out

    assert "Entrypoint: mo.related_to" in text
    assert "Signature:" in text
    assert "Output family: SemanticEdgeRef" in text
    assert "edge = mo.related_to(" in text
    assert "left and right are distinct" in text
    assert "execute only when mo.load() evaluates models/ontology.py" in text


def test_ontology_type_help_discloses_real_public_consumption(capsys) -> None:
    marivo.help("ontology.OntologyCatalog")
    catalog_text = capsys.readouterr().out
    assert "configured, definition_fingerprint" in catalog_text
    assert "edge_count" in catalog_text
    assert "Public consumption: render, show" in catalog_text
    assert 'marivo.help("ontology.authoring")' in catalog_text

    marivo.help("ontology.SemanticEdgeRef")
    ref_text = capsys.readouterr().out
    assert "Public fields: kind, path, key" in ref_text
    assert "Public consumption: to_dict" in ref_text
    assert "marivo.ontology_ref/v1" in ref_text


@pytest.mark.parametrize(
    ("type_target", "string_target"),
    (
        (mo.OntologyCatalog, "ontology.OntologyCatalog"),
        (mo.SemanticEdgeRef, "ontology.SemanticEdgeRef"),
    ),
)
def test_ontology_type_object_help_matches_string_contract(
    type_target: type, string_target: str, capsys
) -> None:
    marivo.help(type_target)
    type_text = capsys.readouterr().out
    marivo.help(string_target)
    string_text = capsys.readouterr().out

    assert type_text == string_text
    assert " at 0x" not in type_text
    assert "Runtime fields:" not in type_text


def test_ontology_error_instance_help_renders_structured_repair(capsys) -> None:
    with pytest.raises(InvalidSemanticEdgeError) as exc_info:
        mo.influences(
            name="outside_loader",
            driver=ms.ref.metric("sales.order_count"),
            outcome=ms.ref.metric("sales.revenue"),
            ai_context=ms.ai_context(business_definition="Not load-scoped."),
        )

    marivo.help(exc_info.value)
    text = capsys.readouterr().out

    assert "Ontology error briefing" in text
    assert "Expected: an authored call executed by mo.load" in text
    assert "Received: constructor call outside ontology loading" in text
    assert "Kind: reauthor" in text
    assert 'Next help: marivo.help("ontology.authoring")' in text


def test_ontology_load_type_error_help_preserves_diagnostics_and_repair(capsys) -> None:
    with pytest.raises(InvalidOntologyRefError) as exc_info:
        mo.load(semantic=object())

    marivo.help(exc_info.value)
    text = capsys.readouterr().out

    assert "Ontology error briefing" in text
    assert "Message: mo.load requires an exact SemanticCatalog" in text
    assert "Expected: SemanticCatalog from ms.load() or session.catalog" in text
    assert "Received: object" in text
    assert "Kind: reauthor" in text
    assert "Pass the exact SemanticCatalog returned by ms.load() or session.catalog." in text
    assert 'Next help: marivo.help("ontology.authoring")' in text


def test_ontology_error_help_preserves_diagnostics_without_repair(capsys) -> None:
    error = InvalidOntologyRefError(
        kind="invalid_ontology_ref",
        message="diagnostic-only ontology error",
        expected="a current semantic ref",
        received="object",
    )
    assert error.repair is None

    marivo.help(error)
    text = capsys.readouterr().out

    assert "Ontology error briefing" in text
    assert "Message: diagnostic-only ontology error" in text
    assert "Expected: a current semantic ref" in text
    assert "Received: object" in text
    assert "Repair:" not in text


def test_absent_empty_and_invalid_ontology_states_are_distinct(tmp_path, capsys) -> None:
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
    marivo.help(exc_info.value.issues[0])
    issue_text = capsys.readouterr().out
    assert "Replace the endpoint with the exact .ref" in issue_text
    assert 'Next help: marivo.help("ontology.authoring")' in issue_text

    session = mv.session.get_or_create(name="invalid-ontology")
    assert not hasattr(session, "_ontology_state")


def test_ontology_execution_error_issue_has_structured_repair(tmp_path, capsys) -> None:
    bootstrap_sales_project(tmp_path)
    semantic = ms.load(workspace_dir=tmp_path)
    _write_ontology(tmp_path, "raise RuntimeError('authored failure')\n")

    with pytest.raises(OntologyLoadError) as exc_info:
        mo.load(semantic=semantic)

    issue = exc_info.value.issues[0]
    marivo.help(issue)
    text = capsys.readouterr().out
    assert "Message: error executing authored ontology: authored failure" in text
    assert "Expected: a valid models/ontology.py module" in text
    assert "Received: RuntimeError" in text
    assert "Fix models/ontology.py so it imports and declares every edge successfully." in text
    assert 'Next help: marivo.help("ontology.authoring")' in text


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
