"""Concrete typing for current data and exact persisted Frame recovery."""

from typing import TYPE_CHECKING

from typing_extensions import assert_type

import marivo.analysis as mv

if TYPE_CHECKING:
    assert_type(
        mv.session.get_or_create("repeatable", question="Why?", timezone="UTC", domains=["sales"]),
        mv.Session,
    )
    session = mv.session.get_or_create("recovery-typing")
    orders = session.catalog.get("entity:sales.orders")
    assert orders.kind == "entity"
    members = session.members(orders)
    assert_type(members, mv.Population)
    assert_type(session.frame(members), mv.AnalysisFrame)
    recovered = session.persisted_frame("artifact_current")
    assert_type(recovered, mv.AnalysisFrame)
    assert_type(session.population_from(recovered, on=()), mv.Population)
    assert_type(recovered.artifact_id, str)
    assert_type(recovered.session_id, str)
    assert_type(recovered.findings(), mv.FindingPage)
    assert_type(recovered.evidence(limit=100), mv.EvidencePage)
    assert_type(recovered.show(n=0), None)
    revenue = session.catalog.get("metric:sales.revenue")
    assert revenue.kind == "metric"
    metric = revenue
    frame = session.observe({"revenue": metric})
    assert_type(frame, mv.AnalysisFrame)
    assert_type(frame.compute(), mv.AnalysisFrame)
    assert_type(frame.persist(), mv.AnalysisFrame)
    assert_type(frame.over(frame["revenue"].mean()), mv.Column)
    assert_type(frame.over(mv.row_count(), by=frame.axes), mv.Column)
    assert_type(recovered["revenue"].share(along=recovered.axes), mv.Column)
    assert_type(
        frame.correlate(frame["revenue"], frame["revenue"], method="pearson"), mv.AnalysisFrame
    )
    assert_type(
        frame.reduce({"r": frame["revenue"].corr(frame["revenue"], method="spearman")}),
        mv.AnalysisFrame,
    )
    assert_type(
        frame.reduce({"n": frame["revenue"].pair_count(frame["revenue"])}), mv.AnalysisFrame
    )
