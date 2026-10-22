"""Coherent-slice acceptance journey for coherent-slice semantic authoring."""

from __future__ import annotations

from pathlib import Path

import marivo.semantic as ms
from marivo.refs import ref as ref_factory
from tests.support.refs import as_ref


def test_coherent_slice_loads_once_and_resolves_every_authored_ref(
    authoring_evidence_project: Path,
    monkeypatch,
) -> None:
    source_path = authoring_evidence_project / "models" / "semantic" / "sales" / "revenue.py"
    source_path.write_text(
        source_path.read_text().replace(
            "from .models import amount as amount_ref\n",
            "from .models import amount as amount_ref\nfrom .models import orders as orders_ref\n",
        )
        + "\n\norders = orders_ref\n\n\n"
        + "@ms.metric(ai_context=ms.ai_context(business_definition='Accepted order count.'))\n"
        + "def order_count():\n"
        + "    return orders.count()\n\n\n"
        + "@ms.metric(\n"
        + "    ai_context=ms.ai_context(\n"
        + "        business_definition='Accepted revenue divided by accepted order count.',\n"
        + "        guardrails=['Use the same accepted-order population in both components.'],\n"
        + "    ),\n"
        + ")\n"
        + "def average_order_value():\n"
        + "    return revenue / order_count\n"
    )

    calls = 0
    real_load = ms.load

    def counted_load(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_load(*args, **kwargs)

    monkeypatch.setattr(ms, "load", counted_load)
    catalog = ms.load(workspace_dir=authoring_evidence_project)

    physical = (
        ms.ref.entity("sales.orders"),
        ms.ref.dimension("sales.orders.region"),
        ms.ref.time_dimension("sales.orders.log_date"),
        ms.ref.measure("sales.orders.amount"),
    )
    published = (
        ms.ref.metric("sales.average_order_value"),
        ms.ref.metric("sales.order_count"),
        ms.ref.metric("sales.revenue"),
    )
    assert calls == 1
    for ref in (*physical, *published):
        identity = catalog.describe(ref).ref
        assert identity is not None and as_ref(identity) == as_ref(ref)
    assert catalog.metrics._refs == tuple(as_ref(ref) for ref in published)
    aov = catalog.describe(ms.ref.metric("sales.average_order_value"))
    assert aov.unit == "USD"
    # Named dependencies remain explicit; each component owns its physical closure.
    assert aov._reads == (
        ref_factory.metric("sales.order_count"),
        ref_factory.metric("sales.revenue"),
    )
    report = catalog.readiness(refs=list(physical))
    assert not report.blockers
    assert report._analysis_ready_inputs == tuple(as_ref(ref) for ref in physical)
