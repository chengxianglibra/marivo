"""Ontology authoring, discovery, selection, observation, and recovery contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

import ibis
import pandas as pd
import pytest

import marivo
import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.ontology as mo
import marivo.semantic as ms
from marivo.analysis.errors import (
    ArtifactAuthorityUnknownError,
    ArtifactStaleError,
    CandidateNotObservableError,
    CandidateScopeOverrideForbiddenError,
    FrameMetaInvalidError,
    OntologyNotConfiguredError,
    OntologyUnavailableError,
)
from marivo.analysis.frames.candidate import CandidateSet, OntologyMetricCandidate
from marivo.ontology.errors import (
    InvalidOntologyRefError,
    InvalidSemanticEdgeError,
    OntologyLoadError,
)
from tests.conftest import bootstrap_sales_project


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


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


def _session_with_orders(tmp_path):
    con = ibis.duckdb.connect(":memory:")
    con.create_table(
        "orders",
        pd.DataFrame(
            {
                "created_at": pd.to_datetime(
                    ["2026-07-01", "2026-07-01", "2026-07-02", "2026-07-03"]
                ),
                "amount": [10.0, 20.0, 15.0, 25.0],
            }
        ),
        overwrite=True,
    )
    return mv.session.get_or_create(
        name="ontology",
        backends={"warehouse": lambda: con},
    )


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

    session = mv.session.get_or_create(name="invalid-ontology", use_datasources=False)
    assert session._ontology_state == "unavailable"


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


def test_semantic_hypothesis_end_to_end_and_candidate_origin(tmp_path) -> None:
    _ready_project(tmp_path)
    session = _session_with_orders(tmp_path)
    source = session.observe(
        ms.ref.metric("sales.revenue"),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04"),
        grain=mv.grain("day"),
    )

    contract = source.contract()
    continuation = next(
        item
        for item in contract.affordances
        if item.capability_id == "discover.semantic_hypotheses"
    )
    assert continuation.expected_output_family == "CandidateSet[semantic_hypothesis]"
    assert all(item.status == "pass" for item in continuation.preconditions)

    candidates = session.discover.semantic_hypotheses(source)
    assert isinstance(candidates, CandidateSet)
    assert candidates.meta.shape == "semantic_hypothesis"
    assert candidates.meta.resolution_summary.emitted_candidates == 1
    assert session.evidence.findings(artifact_ref=candidates.ref).items == ()
    row = candidates.to_pandas().iloc[0]
    assert pd.isna(row["score"])
    assert row["edge_relation"] == "influences"
    assert json.loads(row["metric_ref"])["path"] == "sales.order_count"
    assert str(row["item_id"]).startswith("candidate_")
    assert len(str(row["item_id"])) == len("candidate_") + 64
    rendered = candidates.render()
    assert "Order volume may help explain revenue movement." in rendered
    assert "candidates.select(item_id=" in rendered
    select_continuation = next(
        item
        for item in candidates.contract().affordances
        if item.capability_id == "CandidateSet.select"
    )
    assert select_continuation.expected_output_family == "OntologyMetricCandidate"
    assert (
        "candidates.select(item_id=...) -> OntologyMetricCandidate"
        in candidates.contract().render()
    )

    recovered = session.get_frame(candidates.ref)
    selected = recovered.select(item_id=str(row["item_id"]))
    assert isinstance(selected, OntologyMetricCandidate)
    assert selected.metric_ref.path == "sales.order_count"
    with pytest.raises(TypeError):
        OntologyMetricCandidate()
    with pytest.raises(CandidateScopeOverrideForbiddenError):
        session.observe(selected, time_scope=None)
    with pytest.raises(CandidateNotObservableError):
        session.observe(cast("Any", [selected]))

    observed = session.observe(selected, analysis_purpose="test explicit ontology re-entry")
    assert observed.meta.candidate_origins
    origin = observed.meta.candidate_origins[-1]
    assert origin.item_id == selected.item_id
    assert origin.edge_context.business_definition.startswith("Order volume")
    cold = session.get_frame(observed.ref)
    assert cold.meta.candidate_origins == observed.meta.candidate_origins

    association = session.correlate(source, observed)
    assert association.meta.candidate_origins == observed.meta.candidate_origins
    finding = session.evidence.findings(artifact_ref=association.ref).items[0]
    assert finding.derivation.candidate_origins == observed.meta.candidate_origins

    tested = session.hypothesis_test(source, observed)
    assert tested.meta.candidate_origins == observed.meta.candidate_origins
    test_finding = session.evidence.findings(artifact_ref=tested.ref).items[0]
    assert test_finding.derivation.candidate_origins == observed.meta.candidate_origins

    driver_source = session.observe(ms.ref.metric("sales.order_count"))
    reverse = session.discover.semantic_hypotheses(driver_source)
    assert reverse.meta.resolution_summary.examined_edges == 0
    assert reverse.meta.resolution_summary.emitted_candidates == 0


@pytest.mark.parametrize(
    ("old", "new", "definition_ref"),
    (
        ("return orders.amount.sum()", "return orders.amount.mean()", "metric:sales.revenue"),
        (
            "return orders.amount.count()",
            "return orders.amount.max()",
            "metric:sales.order_count",
        ),
        (
            "source=md.table('orders')",
            "source=md.table('orders_v2')",
            "entity:sales.orders",
        ),
    ),
)
def test_candidate_reentry_uses_shared_source_and_readiness_authority(
    tmp_path,
    old: str,
    new: str,
    definition_ref: str,
) -> None:
    _ready_project(tmp_path)
    session = _session_with_orders(tmp_path)
    source = session.observe(ms.ref.metric("sales.revenue"))
    candidates = session.discover.semantic_hypotheses(source)
    selected = candidates.select(item_id=str(candidates.to_pandas().iloc[0]["item_id"]))
    datasets = tmp_path / "models" / "semantic" / "sales" / "datasets.py"
    datasets.write_text(datasets.read_text().replace(old, new))
    session._catalog = ms.load()

    with pytest.raises(ArtifactStaleError) as exc_info:
        session.observe(selected)

    error = exc_info.value
    assert error._context["capability_id"] == "observe"
    assert error._context["parameter"] == "metrics"
    assert definition_ref in error._context["definition_refs"]


def test_candidate_reentry_fails_closed_when_source_authority_is_unavailable(tmp_path) -> None:
    _ready_project(tmp_path)
    session = _session_with_orders(tmp_path)
    source = session.observe(ms.ref.metric("sales.revenue"))
    candidates = session.discover.semantic_hypotheses(source)
    selected = candidates.select(item_id=str(candidates.to_pandas().iloc[0]["item_id"]))
    source_ref = source.meta.artifact_id or source.meta.ref
    session._store.delete_artifact(session.id, source_ref)
    (session._layout.frames_dir / source_ref / "data.parquet").unlink()

    with pytest.raises(ArtifactAuthorityUnknownError) as exc_info:
        session.observe(selected)

    error = exc_info.value
    assert error._context["capability_id"] == "observe"
    assert error._context["parameter"] == "metrics"
    assert source_ref in error._context["definition_refs"]


def test_configured_empty_ontology_commits_empty_candidate_set(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    _add_order_count_metric(tmp_path)
    _write_ontology(tmp_path, "import marivo.ontology as mo\n")
    session = _session_with_orders(tmp_path)
    source = session.observe(ms.ref.metric("sales.revenue"))

    candidates = session.discover.semantic_hypotheses(source)

    assert session._ontology_state == "ready"
    assert candidates.meta.resolution_summary.examined_edges == 0
    assert candidates.meta.resolution_summary.emitted_candidates == 0
    assert session.get_frame(candidates.ref).to_pandas().empty
    assert "examined_edges=0" in candidates.render()


def test_discovery_excludes_target_that_cannot_plan_exact_source_axis(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    _add_order_count_metric(tmp_path)
    datasets = tmp_path / "models" / "semantic" / "sales" / "datasets.py"
    datasets_source = datasets.read_text()
    datasets.write_text(
        datasets_source
        + "\ncustomers = ms.entity(\n"
        + "    name='customers', datasource=warehouse, source=md.table('customers'),\n"
        + "    ai_context=ms.ai_context(business_definition='One customer account.'),\n"
        + ")\n"
        + "@ms.metric(entities=[customers], additivity='additive', name='customer_count',\n"
        + "    ai_context=ms.ai_context(business_definition='Count of customer accounts.'))\n"
        + "def customer_count(customers):\n"
        + "    return customers.customer_id.count()\n"
    )
    _write_ontology(
        tmp_path,
        (
            "import marivo.ontology as mo\n"
            "import marivo.semantic as ms\n"
            "mo.influences(\n"
            "    name='sales.customers_influence_revenue',\n"
            "    driver=ms.ref.metric('sales.customer_count'),\n"
            "    outcome=ms.ref.metric('sales.revenue'),\n"
            "    ai_context=ms.ai_context("
            "business_definition='Customer population may influence revenue.'),\n"
            ")\n"
        ),
    )
    con = ibis.duckdb.connect(":memory:")
    con.create_table(
        "orders",
        pd.DataFrame({"amount": [10.0, 20.0], "region": ["us", "eu"]}),
        overwrite=True,
    )
    con.create_table(
        "customers",
        pd.DataFrame({"customer_id": [1, 2, 3]}),
        overwrite=True,
    )
    session = mv.session.get_or_create(
        name="incompatible-scope",
        backends={"warehouse": lambda: con},
    )
    source = session.observe(
        ms.ref.metric("sales.revenue"),
        dimensions=[ms.ref.dimension("sales.orders.region")],
    )

    candidates = session.discover.semantic_hypotheses(source)

    summary = candidates.meta.resolution_summary
    assert summary.emitted_candidates == 0
    assert summary.excluded_counts.incompatible_inherited_scope == 1
    assert summary.exclusions[0].metric_ref is not None
    assert summary.exclusions[0].metric_ref.path == "sales.customer_count"
    assert any(
        issue.kind == "incompatible_inherited_scope" for issue in candidates.contract().issues
    )


@pytest.mark.parametrize("limit", [None, True, False, 0, -1, 201])
def test_semantic_hypothesis_limit_is_closed(tmp_path, limit: object) -> None:
    _ready_project(tmp_path)
    session = _session_with_orders(tmp_path)
    source = session.observe(ms.ref.metric("sales.revenue"))
    run_count = len(session._store.list_runs(session.id))

    with pytest.raises(mv.errors.SemanticKindMismatchError):
        session.discover.semantic_hypotheses(source, limit=cast("Any", limit))
    assert len(session._store.list_runs(session.id)) == run_count


def test_discovery_errors_for_absent_and_session_unavailable(tmp_path) -> None:
    bootstrap_sales_project(tmp_path)
    _add_order_count_metric(tmp_path)
    session = _session_with_orders(tmp_path)
    source = session.observe  # retain a normal non-ontology callable path smoke check
    assert callable(source)
    assert session._ontology_state == "absent"

    frame = session.observe(ms.ref.metric("sales.revenue"))
    with pytest.raises(OntologyNotConfiguredError):
        session.discover.semantic_hypotheses(frame)

    _write_ontology(
        tmp_path,
        _valid_ontology_source().replace("sales.order_count", "sales.missing"),
    )
    session_attach._reset_process_state()
    unavailable = _session_with_orders(tmp_path)
    assert unavailable._ontology_state == "unavailable"
    unavailable_frame = unavailable.observe(ms.ref.metric("sales.revenue"))
    with pytest.raises(OntologyUnavailableError):
        unavailable.discover.semantic_hypotheses(unavailable_frame)


def test_candidate_recovery_rejects_digest_tampering(tmp_path) -> None:
    _ready_project(tmp_path)
    session = _session_with_orders(tmp_path)
    source = session.observe(ms.ref.metric("sales.revenue"))
    candidates = session.discover.semantic_hypotheses(source)
    row = session._store.get_artifact(session.id, candidates.ref)
    assert row is not None
    data_path = session.project_root / row["path"]
    data = pd.read_parquet(data_path)
    data.loc[0, "item_id"] = "candidate_" + "0" * 64
    data.to_parquet(data_path, index=False)

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        session.get_frame(candidates.ref)
    assert exc_info.value._context["reason"] == "digest_mismatch"


def test_candidate_recovery_rejects_edge_relation_context_mismatch(tmp_path) -> None:
    _ready_project(tmp_path)
    session = _session_with_orders(tmp_path)
    source = session.observe(ms.ref.metric("sales.revenue"))
    candidates = session.discover.semantic_hypotheses(source)
    artifact = session._store.get_artifact(session.id, candidates.ref)
    assert artifact is not None
    data_path = session.project_root / artifact["path"]
    data = pd.read_parquet(data_path)
    data.loc[0, "edge_relation"] = "related_to"
    data.to_parquet(data_path, index=False)

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        session.get_frame(candidates.ref)

    assert exc_info.value._context["reason"] == "edge_relation_context_mismatch"


def test_frame_recovery_rejects_conflicting_candidate_origins(tmp_path) -> None:
    _ready_project(tmp_path)
    session = _session_with_orders(tmp_path)
    source = session.observe(ms.ref.metric("sales.revenue"))
    candidates = session.discover.semantic_hypotheses(source)
    selected = candidates.select(item_id=str(candidates.to_pandas().iloc[0]["item_id"]))
    observed = session.observe(selected)
    artifact = session._store.get_artifact(session.id, observed.ref)
    assert artifact is not None
    meta_path = session.project_root / artifact["meta_path"]
    payload = json.loads(meta_path.read_text())
    duplicate = json.loads(json.dumps(payload["candidate_origins"][0]))
    duplicate["readiness_fingerprint"] += "-conflict"
    payload["candidate_origins"].append(duplicate)
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        session.get_frame(observed.ref)

    assert "conflicting CandidateOrigin payload" in str(
        exc_info.value._context["validation_errors"]
    )


def test_candidate_limit_bounds_readiness_bindings_to_emitted_rows(tmp_path) -> None:
    _ready_project(tmp_path)
    datasets = tmp_path / "models" / "semantic" / "sales" / "datasets.py"
    datasets.write_text(
        datasets.read_text()
        + "\n@ms.metric(entities=[orders], additivity='additive', "
        + "name='positive_order_count', ai_context=ms.ai_context("
        + "business_definition='Count of orders with positive amounts.'))\n"
        + "def positive_order_count(orders):\n"
        + "    return orders.amount.count()\n"
    )
    ontology = tmp_path / "models" / "ontology.py"
    ontology.write_text(
        ontology.read_text()
        + "\nmo.influences(\n"
        + "    name='sales.positive_order_count_influences_revenue',\n"
        + "    driver=ms.ref.metric('sales.positive_order_count'),\n"
        + "    outcome=ms.ref.metric('sales.revenue'),\n"
        + "    ai_context=ms.ai_context("
        + "business_definition='Positive order volume may help explain revenue.'),\n"
        + ")\n"
    )
    session = _session_with_orders(tmp_path)
    source = session.observe(ms.ref.metric("sales.revenue"))

    candidates = session.discover.semantic_hypotheses(source, limit=1)

    row_metric = json.loads(candidates.to_pandas().iloc[0]["metric_ref"])["path"]
    binding_metrics = tuple(
        binding.metric_ref.path for binding in candidates.meta.readiness_bindings
    )
    assert candidates.meta.resolution_summary.candidate_count_before_limit == 2
    assert candidates.meta.resolution_summary.emitted_candidates == 1
    assert binding_metrics == (row_metric,)
