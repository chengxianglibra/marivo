"""Concrete typing for current data and exact persisted Frame recovery."""

from typing import TYPE_CHECKING

from typing_extensions import assert_type

import marivo.analysis as mv
import marivo.semantic as ms

if TYPE_CHECKING:
    session = mv.session.create("recovery-typing")
    members = session.members(session.catalog.require(ms.ref.entity("sales.orders")))
    assert_type(members, mv.Population)
    assert_type(session.frame(members), mv.AnalysisFrame)
    recovered = session.persisted_frame("artifact_current")
    assert_type(recovered, mv.AnalysisFrame)
    assert_type(session.population_from(recovered, on=()), mv.Population)
    assert_type(recovered.artifact, mv.Artifact)
    assert_type(recovered.artifact.show(), None)
    metric = session.catalog.metrics.get("sales.revenue")
    frame = session.observe({"revenue": metric})
    assert_type(frame, mv.AnalysisFrame)
    assert_type(frame.compute(), mv.AnalysisFrame)
    assert_type(frame.persist(), mv.AnalysisFrame)
