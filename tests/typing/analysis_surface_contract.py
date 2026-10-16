"""Concrete typing for current member and artifact recovery entrypoints."""

from typing import TYPE_CHECKING

from typing_extensions import assert_type

import marivo.analysis as mv
import marivo.semantic as ms

if TYPE_CHECKING:
    session = mv.session.get_or_create("recovery-typing")
    members = session.members(ms.ref.entity("sales.orders"))
    assert_type(members, mv.LogicalAnalysisDomain)
    recovered = session.artifact("artifact_current")
    assert_type(recovered, mv.Artifact)
    metric = session.catalog.metrics.get("sales.revenue")
    query = session.observe({"revenue": metric})
    assert_type(query, mv.Query)
    assert_type(query.execute(), mv.Artifact)
