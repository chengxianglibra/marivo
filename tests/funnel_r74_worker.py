"""Independent producer/continue/recover acceptance for nonempty graph Findings."""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import marivo.analysis as mv
from marivo.analysis.evidence._dataset_codec import encode_finding_body
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.graph_relation import FrozenBinding, Relation
from marivo.analysis.materialization.graph_snapshot import freeze_graph, thaw_graph
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.session.core import Session
from marivo.refs import ref
from tests.funnel_r74_fixtures import START, build_funnel_public

root, phase = Path(sys.argv[1]), sys.argv[2]
manifest = root / "r74.json"
axes = (ref.dimension("sales.customers.region"),)
if phase == "produce":
    history = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "none" else None
    session, journeys, _, _ = build_funnel_public(root, form=sys.argv[3], history=history)
    current = journeys().funnel(axes=axes).execute()
    baseline = journeys(START - timedelta(days=3)).funnel(axes=axes).execute()
    comparison = current.compare(baseline).execute()
    payload = {
        "current": current.state.artifact_ref.ref,
        "baseline": baseline.state.artifact_ref.ref,
        "session": session.id,
        "comparison": comparison.state.artifact_ref.ref,
        "bodies": [encode_finding_body(item) for item in comparison.findings().items],
    }
    assert comparison.evidence_digest().finding_count == 2
    if history is not None:
        historical_axes = (
            ref.dimension(
                "sales." + ("snapshots" if history == "snapshot" else "validity") + ".region"
            ),
        )
        historical = journeys().funnel(axes=historical_axes).execute()
        historical_comparison = historical.compare(historical).execute()
        payload["historical"] = historical.state.artifact_ref.ref
        payload["historical_comparison"] = historical_comparison.state.artifact_ref.ref
        payload["historical_axes"] = [axis.path for axis in historical_axes]
        payload["historical_frame"] = historical.to_pandas().to_json()
        payload["historical_bodies"] = [
            encode_finding_body(item) for item in historical_comparison.findings().items
        ]
    manifest.write_text(json.dumps(payload))
else:
    payload = json.loads(manifest.read_text())
    store = SessionStore(root / "graph")
    runtime = DatasetRuntime(store, payload["session"])
    session = Session._from_runtime(runtime)

    def forbidden(*args, **kwargs):
        raise AssertionError("fixed continuation opened source or current Semantic")

    import duckdb

    from marivo.analysis.materialization import journey_execution
    from marivo.analysis.methods import journey_matching
    from marivo.datasource.adapters import SourceSession
    from marivo.semantic.reader import SemanticProject

    duckdb.connect = forbidden
    SourceSession.__enter__ = forbidden
    SemanticProject.load = forbidden
    journey_execution.match = forbidden
    journey_matching.match = forbidden
    if phase in ("cap-continue", "cap-recover"):
        comparison = session.artifact(payload["comparison"])
        assert comparison.evidence_digest().finding_count == 1000
        findings, cursor = [], None
        while True:
            page = comparison.findings(limit=100, cursor=cursor)
            findings.extend(page.items)
            if not page.has_more:
                break
            cursor = page.next_cursor
        assert len(findings) == 1000
        assert len({item.finding_id for item in findings}) == 1000
        assert comparison.finding(findings[-1].finding_id) == findings[-1]
        if phase == "cap-continue":
            read = comparison.read(comparison.loss_rate_delta).execute()
            payload["read"] = read.state.artifact_ref.ref
            manifest.write_text(json.dumps(payload))
        else:
            node = thaw_graph(payload["graph"])
            exact = Relation(runtime, node, FrozenBinding(node)).execute()
            assert exact.artifact.artifact_ref == payload["comparison"]
            read = session.artifact(payload["read"])
            assert read.to_pandas().value.notna().sum() == 1003
            assert read.to_pandas().value.dropna().eq(0).all()
        assert not runtime.statistics.statements
        print(json.dumps({"phase": phase, "accepted": True}))
        raise SystemExit(0)
    current = session.artifact(payload["current"])
    baseline = session.artifact(payload["baseline"])
    assert current.read(current.cohort_count).execute().to_pandas().value.tolist() == [1, 1, 1, 1]
    assert baseline.read(baseline.lost_count).execute().to_pandas().value.sum() == 1
    comparison = session.artifact(payload["comparison"])
    assert [encode_finding_body(item) for item in comparison.findings().items] == payload["bodies"]
    first = comparison.findings(limit=1)
    second = comparison.findings(limit=1, cursor=first.next_cursor)
    assert first.has_more and not second.has_more
    assert comparison.finding(second.items[0].finding_id) == second.items[0]
    if "historical" in payload:
        historical = session.artifact(payload["historical"])
        assert historical.to_pandas().to_json() == payload["historical_frame"]
        assert historical.read(historical.lost_count).execute().to_pandas().value.sum() == 1
        historical_comparison = session.artifact(payload["historical_comparison"])
        assert [
            encode_finding_body(item) for item in historical_comparison.findings().items
        ] == payload["historical_bodies"]
    if phase == "continue":
        from marivo.semantic.event import participant_role

        step = mv.step(
            participant=participant_role(event=ref.event("sales.finished"), name="buyer"),
            key="finish",
        )
        logical = comparison.attribute(target=mv.funnel_loss_rate(step=step), axes=axes)
        (root / "continuation.txt").write_text(freeze_graph(logical._node.root))
        allocation = logical.execute()
        payload["allocation"] = allocation.state.artifact_ref.ref
        payload["allocation_bodies"] = [
            encode_finding_body(item) for item in allocation.findings().items
        ]
        if "historical" in payload:
            historical_allocation = historical_comparison.attribute(
                target=mv.funnel_loss_rate(step=step),
                axes=tuple(ref.dimension(path) for path in payload["historical_axes"]),
            ).execute()
            payload["historical_allocation"] = historical_allocation.state.artifact_ref.ref
            payload["historical_allocation_bodies"] = [
                encode_finding_body(item) for item in historical_allocation.findings().items
            ]
        manifest.write_text(json.dumps(payload))
    else:
        node = thaw_graph((root / "continuation.txt").read_text())
        from marivo.analysis.core.graph import MethodNode

        assert isinstance(node, MethodNode)
        exact = Relation(runtime, node, FrozenBinding(node)).execute()
        assert exact.artifact.artifact_ref == payload["allocation"]
        allocation = session.artifact(payload["allocation"])
        assert allocation.evidence_digest().finding_count == 4
        assert [encode_finding_body(item) for item in allocation.findings().items] == payload[
            "allocation_bodies"
        ]
        if "historical" in payload:
            historical_allocation = session.artifact(payload["historical_allocation"])
            assert historical_allocation.evidence_digest().finding_count > 0
            assert [
                encode_finding_body(item) for item in historical_allocation.findings().items
            ] == payload["historical_allocation_bodies"]
    assert not runtime.statistics.statements
print(json.dumps({"phase": phase, "accepted": True}))
