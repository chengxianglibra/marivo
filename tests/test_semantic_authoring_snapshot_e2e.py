"""Milestone 1 acceptance journey for coherent-slice semantic authoring."""

from __future__ import annotations

from pathlib import Path

import marivo.semantic as ms


def test_coherent_slice_loads_once_and_resolves_every_authored_ref(
    authoring_evidence_project: Path,
    monkeypatch,
) -> None:
    source_path = authoring_evidence_project / "models" / "semantic" / "sales" / "models.py"
    source_path.write_text(
        source_path.read_text()
        + "\norder_count = ms.count(\n"
        + "    name='order_count', entity=orders,\n"
        + "    ai_context=ms.ai_context(business_definition='Accepted order count.'),\n"
        + ")\n"
        + "average_order_value = ms.ratio(\n"
        + "    name='average_order_value', numerator=revenue, denominator=order_count, unit='USD',\n"
        + "    ai_context=ms.ai_context(\n"
        + "        business_definition='Accepted revenue divided by accepted order count.',\n"
        + "        guardrails=['Use the same accepted-order population in both components.'],\n"
        + "    ),\n"
        + ")\n"
    )

    calls = 0
    real_load = ms.load

    def counted_load(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_load(*args, **kwargs)

    monkeypatch.setattr(ms, "load", counted_load)
    catalog = ms.load(workspace_dir=authoring_evidence_project)

    refs = (
        ms.ref.entity("sales.orders"),
        ms.ref.dimension("sales.orders.region"),
        ms.ref.time_dimension("sales.orders.log_date"),
        ms.ref.measure("sales.orders.amount"),
        ms.ref.metric("sales.revenue"),
        ms.ref.metric("sales.order_count"),
        ms.ref.metric("sales.average_order_value"),
    )
    assert calls == 1
    assert tuple(catalog.require(ref).ref for ref in refs) == refs
    report = catalog.readiness(refs=list(refs))
    assert not report.blockers
    assert report.analysis_ready_inputs == refs


def test_milestone1_has_no_verify_or_semantic_snapshot_projection() -> None:
    from marivo.datasource.snapshot import DiscoverySnapshot
    from marivo.semantic.catalog import SemanticCatalog

    assert not hasattr(SemanticCatalog, "verify")
    for name in ("entity", "dimensions", "values", "time_dimensions", "measures", "relationships"):
        assert not hasattr(DiscoverySnapshot, name)
